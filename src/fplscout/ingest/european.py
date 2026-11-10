"""Midweek European results: ESPN scoreboard -> DuckDB `european_fixtures`.

Why this source: `rest_days` (features/build.py) is computed only from a
player's previous PREMIER LEAGUE kickoff, so a club that played a Tuesday
Champions League/Europa League/Conference League tie between two league
games looks fully rested when it wasn't. ESPN's public scoreboard JSON (no
key, no auth) covers all three UEFA club competitions in one consistent
shape and updates live during matches.

Every season from 2021-22 is loaded, played and scheduled alike, so the
models can train on it: features/build.py turns it into `euro_days_before` /
`euro_days_after` (days from a league kickoff to the club's nearest European
tie either side).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx
import pandas as pd

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"
COMPETITIONS = {
    "uefa.champions": "UCL",
    "uefa.europa": "UEL",
    "uefa.europa.conf": "UECL",
}

# ESPN team displayName -> FPL `teams.name`. Only the disagreements: every
# other current PL club's ESPN name prefix-matches (see _resolve_team_ids).
# Verified against ESPN's eng.1/teams list, not guessed.
TEAM_NAME_OVERRIDES = {
    "AFC Bournemouth": "Bournemouth",
    "Manchester City": "Man City",
    "Manchester United": "Man Utd",
    "Nottingham Forest": "Nott'm Forest",
    "Tottenham Hotspur": "Spurs",
    "Wolverhampton Wanderers": "Wolves",
    "Sheffield United": "Sheffield Utd",
    "West Bromwich Albion": "West Brom",
}
FIRST_SEASON = "2021-22"  # first season all three competitions exist

POLITE_INTERVAL = 0.3
LIVE_TTL = 3 * 3600  # scores move during live midweek matches


@dataclass
class EuropeanClient:
    cache_dir: Path
    _client: httpx.Client = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(timeout=30.0, follow_redirects=True)

    def __enter__(self) -> EuropeanClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    def year(self, slug: str, year: int) -> list[dict]:
        """Every event of one calendar year, in one request: `dates=YYYY`
        returns the whole year, results and scheduled ties alike (verified
        live 2026-11-10: UCL 2024 = 137 events, the full 29 knockout + 108
        league-phase). A date RANGE (`YYYYMMDD-YYYYMMDD`) is a different form
        and returns HTTP 400 on all three competitions. A finished year never
        changes, so its file is kept for good. Never raises -- a dead feed
        must not take the pipeline down; it just contributes no events."""
        cache_path = self.cache_dir / f"{slug}_{year}.json"
        if cache_path.exists() and (
            year < datetime.now(UTC).year
            or time.time() - cache_path.stat().st_mtime < LIVE_TTL
        ):
            return json.loads(cache_path.read_text()).get("events", [])
        try:
            resp = self._client.get(
                f"{BASE_URL}/{slug}/scoreboard", params={"dates": str(year), "limit": 1000}
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            if cache_path.exists():
                return json.loads(cache_path.read_text()).get("events", [])
            return []
        time.sleep(POLITE_INTERVAL)
        cache_path.write_text(json.dumps(data))
        return data.get("events", [])


def _season_of(kickoff: str) -> str:
    """ESPN ISO date -> our season label; a season runs July to June."""
    year = int(kickoff[:4]) - (int(kickoff[5:7]) < 7)
    return f"{year}-{(year + 1) % 100:02d}"


def _team_id(espn_name: str, name_to_id: dict[str, int]) -> int | None:
    """ESPN writes "Brighton & Hove Albion" and "Newcastle United" where FPL
    writes "Brighton" and "Newcastle". An exact-only lookup dropped every such
    club's European ties without a word (Brighton, all of 2026-27 until 11-10)."""
    if espn_name in name_to_id:
        return name_to_id[espn_name]
    return next(
        (tid for name, tid in name_to_id.items() if espn_name.startswith(name + " ")), None
    )


def _resolve_team_ids(con: duckdb.DuckDBPyConnection, season: str) -> dict[str, int]:
    """ESPN team displayName -> our team_id, for one season."""
    teams = con.execute(
        "SELECT team_id, name FROM teams WHERE season = ?", [season]
    ).df()
    by_name = {str(r["name"]): int(r["team_id"]) for _, r in teams.iterrows()}
    mapping: dict[str, int] = {}
    for espn_name in set(TEAM_NAME_OVERRIDES) | set(by_name):
        target = TEAM_NAME_OVERRIDES.get(espn_name, espn_name)
        if target in by_name:
            mapping[espn_name] = by_name[target]
            continue
        for name, team_id in by_name.items():
            if name.startswith(target) or target.startswith(name):
                mapping[espn_name] = team_id
                break
    return mapping


def _normalise(
    events: list[dict], season: str, competition: str, name_to_id: dict[str, int]
) -> pd.DataFrame:
    """One row per (PL club, fixture). The other 34+ clubs in a competition
    are foreign and deliberately unmapped — that side is just skipped, not an
    error (contrast ingest/odds.py, where every row IS two PL clubs)."""
    rows = []
    for event in events:
        comp = event["competitions"][0]
        finished = bool(comp["status"]["type"].get("completed"))
        competitors = comp["competitors"]
        if len(competitors) != 2:
            continue
        for i, team in enumerate(competitors):
            team_id = _team_id(team["team"]["displayName"], name_to_id)
            if team_id is None:
                continue
            opponent = competitors[1 - i]
            goals_for = team.get("score")
            goals_against = opponent.get("score")
            rows.append({
                "season": season,
                "competition": competition,
                "espn_event_id": str(event["id"]),
                "team_id": team_id,
                "opponent": opponent["team"]["displayName"],
                "was_home": team["homeAway"] == "home",
                "kickoff_time": comp["date"],
                "goals_for": int(goals_for) if finished and goals_for not in (None, "") else None,
                "goals_against": int(goals_against)
                if finished and goals_against not in (None, "") else None,
                "finished": finished,
            })
    out = pd.DataFrame(rows)
    if len(out):
        # naive UTC, like every other kickoff_time: DuckDB shifts an aware
        # datetime into the session's timezone on insert
        out["kickoff_time"] = pd.to_datetime(out["kickoff_time"], utc=True).dt.tz_localize(None)
    return out


def sync_european(
    con: duckdb.DuckDBPyConnection, cache_dir: Path, season: str
) -> dict[str, int]:
    """Loads UCL/UEL/UECL results and scheduled ties for every PL club, for
    every season we hold from FIRST_SEASON up to `season`."""
    written: dict[str, int] = {}
    seasons = [r[0] for r in con.execute(
        "SELECT DISTINCT season FROM teams WHERE season BETWEEN ? AND ? ORDER BY season",
        [FIRST_SEASON, season],
    ).fetchall()]
    ids = {s: _resolve_team_ids(con, s) for s in seasons}
    years = sorted({int(s[:4]) + k for s in seasons for k in (0, 1)})
    with EuropeanClient(cache_dir=cache_dir) as client:
        frames = []
        for slug, competition in COMPETITIONS.items():
            events = [e for y in years for e in client.year(slug, y)]
            written[competition] = 0
            for s in seasons:
                mine = [e for e in events if _season_of(e["competitions"][0]["date"]) == s]
                normalised = _normalise(mine, s, competition, ids[s])
                written[competition] += len(normalised)
                if len(normalised):
                    frames.append(normalised)
        if frames:
            euro_df = pd.concat(frames, ignore_index=True)  # noqa: F841 (replacement scan)
            con.execute(
                "INSERT OR REPLACE INTO european_fixtures BY NAME SELECT * FROM euro_df"
            )
    return written
