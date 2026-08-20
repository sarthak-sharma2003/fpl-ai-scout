"""Bookmaker odds ingest: football-data.co.uk -> DuckDB `match_odds`.

Why this source: `fdr` is a hand-set 1-5 integer FPL publishes before the season,
and `teams.strength` is NULL until FPL seeds it. Market odds are a live consensus
that already prices in summer transfers, injuries and preseason — exactly the
information our rolling features cannot see in August, when the squad is picked.
football-data.co.uk does not *produce* odds; it archives what the books posted,
free, one CSV per season, no key.

PRE-MATCH, NOT CLOSING (deliberate). The files carry two odds families: `Avg*`
(collected before the weekend) and `AvgC*` (closing, just before kickoff).
Closing is sharper, but the upcoming-fixtures feed we must serve from can only
ever expose pre-match odds — an FPL deadline falls ~1.5h before the first match
of the gameweek, so closing odds for later fixtures in the gameweek do not exist
yet at decision time. Training on closing and serving on pre-match would be
train/serve skew: the model would learn to trust a sharpness the live path can
never deliver. So both paths use the `Avg*` family. See ODDS_COLUMNS.

Leak-safety: every odds column is priced strictly before kickoff, so unlike
vaastav's `xP` (post-match contaminated, see models/dataset.py) these are safe to
join onto historical rows directly, with no .shift(1) machinery.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import httpx
import numpy as np
import pandas as pd
from scipy.optimize import brentq

BASE_URL = "https://www.football-data.co.uk/mmz4281"
FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
DIVISION = "E0"  # Premier League

# Our season label -> football-data's 4-digit directory
SEASON_CODES = {
    "2021-22": "2122",
    "2022-23": "2223",
    "2023-24": "2324",
    "2024-25": "2425",
    "2025-26": "2526",
    "2026-27": "2627",
}

# Pre-match (not closing) columns — see module docstring. Every one of these is
# DECIMAL ODDS, not a probability: "Avg>2.5" of 2.09 means 2.09-to-1 on over 2.5
# goals, i.e. ~48% before the margin comes out.
ODDS_COLUMNS = ["AvgH", "AvgD", "AvgA", "Avg>2.5", "Avg<2.5", "AHh"]

POLITE_INTERVAL = 0.3
FINISHED_TTL = 7 * 24 * 3600  # closed seasons never change
LIVE_TTL = 6 * 3600  # upcoming-fixture odds move; refresh a few times a day

# football-data team name -> FPL `teams.name`. Only the disagreements: every
# other club name is byte-identical across the two sources. FPL itself has used
# two spellings for Ipswich across seasons, so resolution falls back to a
# prefix match against that season's real team list (_resolve_team_ids).
TEAM_NAME_OVERRIDES = {
    "Man United": "Man Utd",
    "Tottenham": "Spurs",
    "Sheffield United": "Sheffield Utd",
    # promoted for 26/27 — football-data drops the suffix FPL keeps
    "Coventry": "Coventry City",
    "Hull": "Hull City",
}


def looks_like_csv(content: bytes) -> bool:
    """A season directory that doesn't exist yet 301s to another division's file
    rather than 404ing, so the payload is sniffed rather than trusted.

    Some seasons' files carry a UTF-8 BOM and some don't, and bytes.lstrip()
    does NOT remove it — an earlier version checked `.lstrip().startswith(b"div,")`
    and silently rejected 3 of 5 seasons, which looked exactly like an upstream
    outage rather than a parsing bug.
    """
    return content.lstrip(b"\xef\xbb\xbf").lstrip().lower().startswith(b"div,")


def _poisson_p_over_25(total_goals: float) -> float:
    """P(X >= 3) for X ~ Poisson(total_goals)."""
    lam = total_goals
    return 1.0 - np.exp(-lam) * (1.0 + lam + lam * lam / 2.0)


def expected_total_goals(p_over_25: float) -> float:
    """Invert the Poisson over/under-2.5 line into an expected total goals.

    The market quotes P(over 2.5), not a goal expectation; a Poisson match-total
    is the standard one-parameter bridge between them and is monotone, so the
    inversion is unique. Bracketed on [0.05, 12] — real Premier League totals sit
    near 2.7, so the bracket only ever guards against a corrupt input row.
    """
    if not np.isfinite(p_over_25) or not 0.0 < p_over_25 < 1.0:
        return float("nan")
    lo, hi = 0.05, 12.0
    if p_over_25 <= _poisson_p_over_25(lo) or p_over_25 >= _poisson_p_over_25(hi):
        return float("nan")
    return float(brentq(lambda g: _poisson_p_over_25(g) - p_over_25, lo, hi))


def devig_two_way(over: float, under: float) -> float:
    """Over/under odds pair -> P(over), margin removed. Returns NaN on bad input."""
    if not all(np.isfinite([over, under])) or min(over, under) <= 0:
        return float("nan")
    inv_over, inv_under = 1.0 / over, 1.0 / under
    return float(inv_over / (inv_over + inv_under))


def devig(home: float, draw: float, away: float) -> tuple[float, float, float]:
    """Odds -> probabilities summing to 1.

    Raw 1/odds sums to ~1.05: the bookmaker's margin (overround). Proportional
    normalisation is the standard cheap removal. It slightly under-corrects
    longshots (the favourite-longshot bias), which for our purpose — ranking
    fixtures by difficulty — is immaterial.
    """
    if not all(np.isfinite([home, draw, away])) or min(home, draw, away) <= 0:
        return (float("nan"),) * 3
    inv = np.array([1.0 / home, 1.0 / draw, 1.0 / away])
    p = inv / inv.sum()
    return float(p[0]), float(p[1]), float(p[2])


@dataclass
class OddsClient:
    cache_dir: Path
    _client: httpx.Client = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(timeout=30.0, follow_redirects=True)

    def __enter__(self) -> OddsClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    def _fetch_csv(self, url: str, cache_name: str, ttl: float) -> pd.DataFrame | None:
        cache_path = self.cache_dir / cache_name
        if cache_path.exists() and time.time() - cache_path.stat().st_mtime < ttl:
            return pd.read_csv(cache_path, low_memory=False)
        try:
            response = self._client.get(url)
            response.raise_for_status()
        except httpx.HTTPError:
            # Stale cache beats no odds at all; a failed odds fetch must never
            # take the nightly pipeline down (the features degrade to NULL,
            # which LightGBM handles natively).
            if cache_path.exists():
                return pd.read_csv(cache_path, low_memory=False)
            return None
        if not looks_like_csv(response.content):
            return None
        time.sleep(POLITE_INTERVAL)
        cache_path.write_bytes(response.content)
        return pd.read_csv(cache_path, low_memory=False)

    def season_results(self, season: str) -> pd.DataFrame | None:
        code = SEASON_CODES.get(season)
        if code is None:
            return None
        # a season still in progress keeps gaining rows -> short TTL
        ttl = LIVE_TTL if season == max(SEASON_CODES) else FINISHED_TTL
        df = self._fetch_csv(
            f"{BASE_URL}/{code}/{DIVISION}.csv", f"{code}_{DIVISION}.csv", ttl
        )
        if df is None or "Div" not in df.columns:
            return None
        # A season not published yet 301s to a DIFFERENT DIVISION's file, which
        # is a perfectly valid CSV — so "is this a CSV" is not a sufficient
        # guard. Without this, 26/27 loaded National League fixtures.
        return df[df["Div"] == DIVISION].copy()

    def upcoming_fixtures(self) -> pd.DataFrame | None:
        df = self._fetch_csv(FIXTURES_URL, "fixtures.csv", LIVE_TTL)
        if df is None or "Div" not in df.columns:
            return None
        return df[df["Div"] == DIVISION].copy()


def _resolve_team_ids(con: duckdb.DuckDBPyConnection, season: str) -> dict[str, int]:
    """football-data team name -> our team_id, for one season."""
    teams = con.execute(
        "SELECT team_id, name FROM teams WHERE season = ?", [season]
    ).df()
    by_name = {str(r["name"]): int(r["team_id"]) for _, r in teams.iterrows()}
    mapping: dict[str, int] = {}
    for fd_name in set(TEAM_NAME_OVERRIDES) | set(by_name):
        target = TEAM_NAME_OVERRIDES.get(fd_name, fd_name)
        if target in by_name:
            mapping[fd_name] = by_name[target]
            continue
        # FPL has used both "Ipswich" and "Ipswich Town"; prefix match covers
        # that class of suffix drift without a fuzzy matcher.
        for name, team_id in by_name.items():
            if name.startswith(target) or target.startswith(name):
                mapping[fd_name] = team_id
                break
    return mapping


def _normalise(raw: pd.DataFrame, season: str, name_to_id: dict[str, int]) -> pd.DataFrame:
    """Raw football-data rows -> one row per (season, home_team_id, away_team_id)
    carrying de-vigged probabilities and market expected goals per side."""
    missing = [c for c in ODDS_COLUMNS if c not in raw.columns]
    if missing:
        return pd.DataFrame()
    df = raw.dropna(subset=["HomeTeam", "AwayTeam"]).copy()

    df["home_team_id"] = df["HomeTeam"].map(name_to_id)
    df["away_team_id"] = df["AwayTeam"].map(name_to_id)
    unmatched = sorted(
        set(df.loc[df["home_team_id"].isna(), "HomeTeam"])
        | set(df.loc[df["away_team_id"].isna(), "AwayTeam"])
    )
    if unmatched:
        # Loud, not silent: an unmapped club would otherwise become NULL odds
        # for every one of its players, all season, with nothing failing.
        raise ValueError(
            f"{season}: unmapped football-data team names {unmatched} — "
            f"add them to TEAM_NAME_OVERRIDES in ingest/odds.py"
        )

    probs = df.apply(
        lambda r: devig(r["AvgH"], r["AvgD"], r["AvgA"]), axis=1, result_type="expand"
    )
    df[["p_home_win", "p_draw", "p_away_win"]] = probs
    p_over_25 = df.apply(lambda r: devig_two_way(r["Avg>2.5"], r["Avg<2.5"]), axis=1)
    df["exp_total_goals"] = p_over_25.map(expected_total_goals)
    # AHh is the handicap applied to the HOME side (Man City at home = -2.75),
    # i.e. the market's expected goal supremacy with the sign flipped.
    df["home_supremacy"] = -pd.to_numeric(df["AHh"], errors="coerce")
    df["exp_goals_home"] = (df["exp_total_goals"] + df["home_supremacy"]) / 2.0
    df["exp_goals_away"] = (df["exp_total_goals"] - df["home_supremacy"]) / 2.0
    df["season"] = season

    out = df[
        [
            "season", "home_team_id", "away_team_id", "p_home_win", "p_draw",
            "p_away_win", "exp_total_goals", "exp_goals_home", "exp_goals_away",
        ]
    ].copy()
    out["home_team_id"] = out["home_team_id"].astype(int)
    out["away_team_id"] = out["away_team_id"].astype(int)
    # A league plays each ordered pairing exactly once per season, so this key is
    # unique — and it survives the postponements that make a date join fragile.
    return out.drop_duplicates(subset=["season", "home_team_id", "away_team_id"], keep="last")


def sync_odds(
    con: duckdb.DuckDBPyConnection, cache_dir: Path, seasons: list[str]
) -> dict[str, int]:
    """Loads odds for `seasons` plus any upcoming Premier League fixtures.

    Upcoming rows come from a different endpoint and are what the live path
    actually reads: without them the feature is NULL exactly when it matters
    most (the pre-deadline projection). They are written last so a played
    fixture's real odds supersede its earlier upcoming snapshot.
    """
    written: dict[str, int] = {}
    with OddsClient(cache_dir=cache_dir) as client:
        frames = []
        for season in seasons:
            raw = client.season_results(season)
            if raw is None or len(raw) == 0:
                written[season] = 0
                continue
            normalised = _normalise(raw, season, _resolve_team_ids(con, season))
            written[season] = len(normalised)
            frames.append(normalised)

        upcoming = client.upcoming_fixtures()
        if upcoming is not None and len(upcoming) > 0 and seasons:
            live_season = max(seasons)
            normalised = _normalise(upcoming, live_season, _resolve_team_ids(con, live_season))
            written["upcoming"] = len(normalised)
            frames.append(normalised)
        else:
            written["upcoming"] = 0

        if frames:
            odds_df = pd.concat(frames, ignore_index=True)  # noqa: F841 (replacement scan)
            con.execute(
                "INSERT OR REPLACE INTO match_odds BY NAME SELECT * FROM odds_df"
            )
    return written
