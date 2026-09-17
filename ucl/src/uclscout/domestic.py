"""Domestic league results, so strength.fit_elo has more than UCL's ~8-17
matches per club per season to work with.

Source: football-data.co.uk (verified, no auth, no key). Two URL layouts:
  - main leagues: one CSV per season, HomeTeam/AwayTeam/FTHG/FTAG columns.
  - AUT/NOR ("new" layout): one CSV for ALL seasons, Home/Away/HG/AG columns,
    filtered here by a Season column instead of by URL.

football-data.co.uk team names don't match UEFA's (e.g. "Ath Madrid" vs
"Atleti", "Paris SG" vs "Paris"), so results come back keyed by NAME and get
mapped to ids as a separate step. UEFA clubs get their real UEFA id; every
other domestic club (Bayern's opponent Augsburg, say) gets a stable synthetic
id instead of being dropped -- Elo only needs a consistent identity for an
opponent, not a UEFA one, and this is how ClubElo-style fits get their signal:
UCL clubs inherit good ratings from beating up their own league, not just each
other.
"""
from __future__ import annotations

import csv
import io
import re
import time
import unicodedata
import urllib.request
import zlib
from dataclasses import dataclass
from pathlib import Path

from .feeds import Team
from .strength import Result

CACHE_DIR = Path("data/domestic")
_UA = "ucl-scout/0.1 (+https://github.com/)"
_SLEEP = 0.3  # be polite between requests

MAIN_LEAGUES = ["E0", "SP1", "I1", "D1", "F1", "N1", "P1", "T1", "B1", "G1", "SC0"]
EXTRA_LEAGUES = ["AUT", "NOR"]

# football-data.co.uk name -> UEFA team id, for the ones normalised token
# matching gets wrong (different words, not just spelling/accents).
CLUB_ALIASES = {
    "ath madrid": 50124,    # Atleti
    "bayern munich": 50037, # Bayern München
    "man united": 52682,    # Man Utd
    "sp lisbon": 50149,     # Sporting CP
}

# Known unavailable: these clubs' domestic leagues aren't on football-data.co.uk.
# Names as feeds.teams() spells them.
UNAVAILABLE = {"Slavia Praha", "S. Bratislava", "Shakhtar", "Sabah"}


@dataclass(frozen=True)
class NamedResult:
    """A result before club names are resolved to UEFA ids."""
    date: str
    home: str
    away: str
    home_goals: int
    away_goals: int
    season: str


def _get(url: str, cache_name: str) -> str:
    path = CACHE_DIR / cache_name
    if path.exists():
        return path.read_text(encoding="utf-8")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")  # older files aren't utf-8
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    time.sleep(_SLEEP)
    return text


def _iso_date(s: str) -> str:
    """dd/mm/yy or dd/mm/yyyy -> YYYY-MM-DD."""
    d, m, y = s.strip().split("/")
    if len(y) == 2:
        y = ("20" if int(y) < 70 else "19") + y
    return f"{y}-{m.zfill(2)}-{d.zfill(2)}"


def _season_label(season: str) -> str:
    """"2526" -> "2025-26"."""
    return f"20{season[:2]}-{season[2:]}"


def fetch_league(code: str, season: str) -> list[NamedResult]:
    """Fetch one league/season. Handles both the mmz4281 and /new/ layouts."""
    label = _season_label(season)
    if code in EXTRA_LEAGUES:
        text = _get(f"https://football-data.co.uk/new/{code}.csv", f"{code}.csv")
        start, end = f"20{season[:2]}", f"20{season[2:]}"
        wanted = {f"{start}/{end}", end}  # e.g. Austria "2025/2026", Norway "2026"
        rows = [r for r in csv.DictReader(io.StringIO(text)) if r["Season"] in wanted]
        home_k, away_k, hg_k, ag_k = "Home", "Away", "HG", "AG"
    else:
        text = _get(f"https://football-data.co.uk/mmz4281/{season}/{code}.csv",
                     f"{code}_{season}.csv")
        rows = list(csv.DictReader(io.StringIO(text)))
        home_k, away_k, hg_k, ag_k = "HomeTeam", "AwayTeam", "FTHG", "FTAG"

    out = []
    for r in rows:
        hg, ag = r.get(hg_k, ""), r.get(ag_k, "")
        if not hg.strip().isdigit() or not ag.strip().isdigit():
            continue  # unplayed fixture
        out.append(NamedResult(
            date=_iso_date(r["Date"]), home=r[home_k], away=r[away_k],
            home_goals=int(hg), away_goals=int(ag), season=label,
        ))
    return out


def _norm_tokens(name: str) -> set[str]:
    s = name.replace("ø", "o").replace("Ø", "O")  # NFKD doesn't decompose these
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9\s]", " ", s.lower())
    return set(s.split()) - {"fc", "cf", "sc", "afc", "cp", "ac", "club"}


def map_clubs(teams: list[Team], names: set[str]) -> dict[str, int]:
    """football-data club name -> UEFA team id, for the given observed names.

    Matches by normalised token subset (handles extra qualifiers either side,
    e.g. "Paris" vs "Paris SG", "RB Leipzig" vs "Leipzig"), then applies
    CLUB_ALIASES for the pairs that share no tokens at all.
    """
    team_tokens = {t.id: _norm_tokens(t.name) for t in teams}
    out: dict[str, int] = {}
    for name in names:
        if name.strip().lower() in CLUB_ALIASES:
            out[name] = CLUB_ALIASES[name.strip().lower()]
            continue
        nt = _norm_tokens(name)
        for tid, tt in team_tokens.items():
            if nt and tt and (nt <= tt or tt <= nt):
                out[name] = tid
                break
    return out


def club_id(name: str, club_map: dict[str, int]) -> int:
    """The real UEFA id if `name` is one of the 36, else a stable synthetic id.

    Synthetic ids are negative crc32 hashes of the name -- deterministic across
    runs (unlike Python's salted builtin hash()), and never collide with a real
    UEFA id since those are all positive.
    """
    return club_map.get(name, -zlib.crc32(name.encode()))


def _all_named(seasons: tuple[str, ...], leagues: list[str] | None
               ) -> list[NamedResult]:
    leagues = leagues if leagues is not None else MAIN_LEAGUES + EXTRA_LEAGUES
    named: list[NamedResult] = []
    for code in leagues:
        for season in seasons:
            named.extend(fetch_league(code, season))
    return named


def domestic_results(
    teams: list[Team],
    seasons: tuple[str, ...] = ("2526", "2425", "2324", "2223"),
    leagues: list[str] | None = None,
) -> list[Result]:
    """Fetch domestic results and map them onto ids, ready for fit_elo.

    Nothing is dropped: the 36 UCL clubs get their real UEFA id (so fit_elo's
    output lines up with the rest of the pipeline), every other domestic club
    gets a stable synthetic id via club_id(). fit_elo rates them all; only the
    UEFA ids are ever looked up again downstream.
    """
    named = _all_named(seasons, leagues)
    names = {r.home for r in named} | {r.away for r in named}
    club_map = map_clubs(teams, names)
    return sorted(
        (Result(date=r.date, home_id=club_id(r.home, club_map),
                away_id=club_id(r.away, club_map),
                home_goals=r.home_goals, away_goals=r.away_goals,
                season=r.season)
         for r in named),
        key=lambda r: r.date,
    )


def demo():
    from .feeds import teams as fetch_teams

    e0 = fetch_league("E0", "2526")
    assert len(e0) > 100, len(e0)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", e0[0].date), e0[0].date
    assert isinstance(e0[0].home_goals, int)

    ts = fetch_teams()

    # synthetic ids: negative and stable across independent calls
    sid = club_id("Augsburg", {})
    assert sid < 0 and sid == club_id("Augsburg", {}), sid

    named = _all_named(("2526", "2425"), None)
    names = {r.home for r in named} | {r.away for r in named}
    club_map = map_clubs(ts, names)
    matched = set(club_map.values()) & {t.id for t in ts}
    unmatched = {t.name for t in ts if t.id not in matched}
    assert len(matched) >= 30, (len(matched), unmatched)
    assert UNAVAILABLE <= unmatched, unmatched - UNAVAILABLE  # expected gaps

    results = domestic_results(ts)
    assert len(results) >= 5000, len(results)

    uefa_ids = {t.id for t in ts}
    present = uefa_ids & ({r.home_id for r in results} | {r.away_id for r in results})
    assert len(present) >= 30, (len(present), sorted(t.name for t in ts if t.id not in present))

    bayern = 50037
    assert any(bayern in (r.home_id, r.away_id) for r in results), "no Bayern match found"

    print(f"domestic.py self-check OK: {len(results)} matches, "
          f"{len(present)}/{len(ts)} UCL clubs present, "
          f"{len(matched)}/{len(ts)} clubs UEFA-mapped, synthetic ids negative+stable")


if __name__ == "__main__":
    demo()
