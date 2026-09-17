"""Midweek European results: ESPN scoreboard -> DuckDB `european_fixtures`.

Why this source: `rest_days` (features/build.py) is computed only from a
player's previous PREMIER LEAGUE kickoff, so a club that played a Tuesday
Champions League/Europa League/Conference League tie between two league
games looks fully rested when it wasn't. ESPN's public scoreboard JSON (no
key, no auth) covers all three UEFA club competitions in one consistent
shape and updates live during matches.

Not wired into any feature or model yet — this just lands the data in
DuckDB. `rest_days` still only counts Premier League fixtures.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
}

POLITE_INTERVAL = 0.3
LIVE_TTL = 3 * 3600  # scores move during live midweek matches
WINDOW_DAYS = 10  # +/- this many days around today, each nightly run


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

    def _day(self, slug: str, day: str) -> list[dict]:
        """Events on one calendar day (`day` = YYYYMMDD). ESPN's `dates` param
        only accepts a single day here — a range (`YYYYMMDD-YYYYMMDD`) returns
        HTTP 400 on every one of these three competitions, verified live, so
        this must not be "optimised" back into one ranged request."""
        cache_path = self.cache_dir / f"{slug}_{day}.json"
        if cache_path.exists() and time.time() - cache_path.stat().st_mtime < LIVE_TTL:
            return json.loads(cache_path.read_text()).get("events", [])
        try:
            resp = self._client.get(
                f"{BASE_URL}/{slug}/scoreboard", params={"dates": day}
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

    def scoreboard(self, slug: str) -> list[dict]:
        """Events for `slug` across the +/- WINDOW_DAYS window around today.
        Never raises — a dead European feed must not take the nightly
        pipeline down; a failed day just contributes no events."""
        today = datetime.now(UTC).date()
        events = []
        for offset in range(-WINDOW_DAYS, WINDOW_DAYS + 1):
            day = (today + timedelta(days=offset)).strftime("%Y%m%d")
            events.extend(self._day(slug, day))
        return events


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
            team_id = name_to_id.get(team["team"]["displayName"])
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
        out["kickoff_time"] = pd.to_datetime(out["kickoff_time"], utc=True)
    return out


def sync_european(
    con: duckdb.DuckDBPyConnection, cache_dir: Path, season: str
) -> dict[str, int]:
    """Loads UCL/UEL/UECL results and upcoming ties for every current PL club."""
    written: dict[str, int] = {}
    name_to_id = _resolve_team_ids(con, season)
    with EuropeanClient(cache_dir=cache_dir) as client:
        frames = []
        for slug, competition in COMPETITIONS.items():
            events = client.scoreboard(slug)
            normalised = _normalise(events, season, competition, name_to_id)
            written[competition] = len(normalised)
            if len(normalised):
                frames.append(normalised)
        if frames:
            euro_df = pd.concat(frames, ignore_index=True)  # noqa: F841 (replacement scan)
            con.execute(
                "INSERT OR REPLACE INTO european_fixtures BY NAME SELECT * FROM euro_df"
            )
    return written
