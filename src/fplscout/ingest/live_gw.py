"""Live per-GW loader: FPL API (bootstrap-static + fixtures + element-summary) ->
DuckDB, for whatever season the live API currently represents.

Only used once that season is beyond vaastav's historical coverage (see cli.py's
`refresh` — VAASTAV_SEASONS gates this) so today (still 2025-26, vaastav-covered)
it's simply never called; the day the API flips to 2026-27, this becomes the sole
writer for that season with zero vaastav dependency, per issue #2.

Mirrors ingest/vaastav.py's DELETE+INSERT-per-season idempotency and column
semantics (reuses vaastav.POSITION_MAP / GW_HISTORY_SOURCE_COLUMNS directly rather
than redefining them) so `player_gw_history` rows are schema-identical regardless
of source.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from fplscout.ingest.fpl_api import FplApiClient
from fplscout.ingest.schemas import BootstrapStatic, Fixture
from fplscout.ingest.vaastav import GW_HISTORY_SOURCE_COLUMNS, POSITION_MAP

ASSISTANT_MANAGER_ELEMENT_TYPE = 5

GW_HISTORY_LIVE_COLUMNS = [
    "season", "gw", "fixture_id", "code", "element_id", "team_id",
    "opponent_team_id", "was_home", "position", "kickoff_time", "fpl_xp",
    *GW_HISTORY_SOURCE_COLUMNS,
    "source",
]


def derive_current_season(bootstrap: BootstrapStatic) -> str:
    """e.g. "2026-27", from the earliest event deadline's year — the season
    always starts in August, so its start-year identifies it unambiguously."""
    start_year = min(e.deadline_time for e in bootstrap.events).year
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def _sync_teams(con: duckdb.DuckDBPyConnection, bootstrap: BootstrapStatic, season: str) -> int:
    teams_df = pd.DataFrame(
        [
            {
                "season": season,
                "team_id": t.id,
                "code": t.code,
                "name": t.name,
                "short_name": t.short_name,
                "strength": t.strength,
                "strength_overall_home": t.strength_overall_home,
                "strength_overall_away": t.strength_overall_away,
                "strength_attack_home": t.strength_attack_home,
                "strength_attack_away": t.strength_attack_away,
                "strength_defence_home": t.strength_defence_home,
                "strength_defence_away": t.strength_defence_away,
            }
            for t in bootstrap.teams
        ]
    )
    con.execute("DELETE FROM teams WHERE season = ?", [season])
    con.execute("INSERT INTO teams BY NAME SELECT * FROM teams_df")
    return len(teams_df)


def _sync_fixtures(con: duckdb.DuckDBPyConnection, fixtures: list[Fixture], season: str) -> int:
    fixtures_df = pd.DataFrame(
        [
            {
                "season": season,
                "fixture_id": f.id,
                "event": f.event,
                "kickoff_time": f.kickoff_time,
                "team_h": f.team_h,
                "team_a": f.team_a,
                "team_h_score": f.team_h_score,
                "team_a_score": f.team_a_score,
                "team_h_difficulty": f.team_h_difficulty,
                "team_a_difficulty": f.team_a_difficulty,
                "finished": f.finished,
            }
            for f in fixtures
        ]
    )
    con.execute("DELETE FROM fixtures WHERE season = ?", [season])
    con.execute("INSERT INTO fixtures BY NAME SELECT * FROM fixtures_df")
    return len(fixtures_df)


def _sync_players_dim(
    con: duckdb.DuckDBPyConnection, bootstrap: BootstrapStatic, season: str
) -> None:
    """Same upsert vaastav.py's players dimension does, sourced from bootstrap
    instead of players_raw.csv — needed so a 26/27 signing vaastav never scraped
    still gets a name (see issue #2: zero vaastav dependency)."""
    players_dim_df = pd.DataFrame(
        [
            {
                "code": e.code,
                "first_name": e.first_name,
                "second_name": e.second_name,
                "web_name": e.web_name,
            }
            for e in bootstrap.elements
            if e.element_type != ASSISTANT_MANAGER_ELEMENT_TYPE
        ]
    )
    players_dim_df["last_seen_season"] = season
    con.execute(
        """
        INSERT INTO players AS p
        BY NAME SELECT * FROM players_dim_df
        ON CONFLICT (code) DO UPDATE SET
            first_name = excluded.first_name,
            second_name = excluded.second_name,
            web_name = excluded.web_name,
            last_seen_season = excluded.last_seen_season
        WHERE p.last_seen_season IS NULL
           OR excluded.last_seen_season >= p.last_seen_season
        """
    )


def sync_current_season(
    con: duckdb.DuckDBPyConnection,
    client: FplApiClient,
    bootstrap: BootstrapStatic,
    fixtures: list[Fixture],
    season: str,
) -> dict:
    """Populates teams/fixtures/players for `season` from the already-fetched
    bootstrap/fixtures payloads, then fetches each player's element-summary and
    writes their FINISHED-gameweek rows to player_gw_history. DELETE+INSERT per
    season, so re-running is idempotent (row counts unchanged given unchanged
    upstream data — element-summary responses are served from the client's
    normal file cache, no special-casing needed here)."""
    n_teams = _sync_teams(con, bootstrap, season)
    n_fixtures = _sync_fixtures(con, fixtures, season)
    _sync_players_dim(con, bootstrap, season)

    fixture_finished = {f.id: f.finished for f in fixtures}
    fixture_teams = {f.id: (f.team_h, f.team_a) for f in fixtures}
    players = [e for e in bootstrap.elements if e.element_type != ASSISTANT_MANAGER_ELEMENT_TYPE]
    id_to_code = {e.id: e.code for e in players}
    id_to_position = {e.id: POSITION_MAP[e.element_type] for e in players}

    rows = []
    for element in players:
        summary = client.element_summary(element.id)
        for h in summary.history:
            if not fixture_finished.get(h.fixture, False):
                continue  # never ingest a partial/live gameweek row
            team_h, team_a = fixture_teams[h.fixture]
            rows.append(
                {
                    "season": season,
                    "gw": h.round,
                    "fixture_id": h.fixture,
                    "code": id_to_code[h.element],
                    "element_id": h.element,
                    "team_id": team_h if h.was_home else team_a,
                    "opponent_team_id": h.opponent_team,
                    "was_home": h.was_home,
                    "position": id_to_position[h.element],
                    "kickoff_time": h.kickoff_time,
                    "fpl_xp": None,  # not exposed by element-summary; not a model feature anyway
                    "minutes": h.minutes,
                    "starts": h.starts,
                    "total_points": h.total_points,
                    "goals_scored": h.goals_scored,
                    "assists": h.assists,
                    "clean_sheets": h.clean_sheets,
                    "goals_conceded": h.goals_conceded,
                    "own_goals": h.own_goals,
                    "penalties_saved": h.penalties_saved,
                    "penalties_missed": h.penalties_missed,
                    "yellow_cards": h.yellow_cards,
                    "red_cards": h.red_cards,
                    "saves": h.saves,
                    "bonus": h.bonus,
                    "bps": h.bps,
                    "influence": float(h.influence),
                    "creativity": float(h.creativity),
                    "threat": float(h.threat),
                    "ict_index": float(h.ict_index),
                    "expected_goals": float(h.expected_goals),
                    "expected_assists": float(h.expected_assists),
                    "expected_goal_involvements": float(h.expected_goal_involvements),
                    "expected_goals_conceded": float(h.expected_goals_conceded),
                    "defensive_contribution": h.defensive_contribution,
                    "clearances_blocks_interceptions": h.clearances_blocks_interceptions,
                    "recoveries": h.recoveries,
                    "tackles": h.tackles,
                    "value": h.value,
                    "selected": h.selected,
                    "transfers_in": h.transfers_in,
                    "transfers_out": h.transfers_out,
                    "transfers_balance": h.transfers_balance,
                    "source": "fpl_api",
                }
            )

    gw_history_df = pd.DataFrame(rows, columns=GW_HISTORY_LIVE_COLUMNS)
    con.execute("DELETE FROM player_gw_history WHERE season = ?", [season])
    con.execute("INSERT INTO player_gw_history BY NAME SELECT * FROM gw_history_df")

    return {
        "season": season,
        "teams": n_teams,
        "fixtures": n_fixtures,
        "players": len(players),
        "gw_rows": len(gw_history_df),
    }
