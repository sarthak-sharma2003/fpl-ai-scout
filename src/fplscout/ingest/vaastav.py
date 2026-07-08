"""Historical data loader: github.com/vaastav/Fantasy-Premier-League -> DuckDB.

Deviates from the original plan sketch in two ways, both discovered while grounding
against the real repo (see Phase 1 commit message for details):

1. Does NOT use `cleaned_merged_seasons.csv` — it stops at 2023-24 (missing 2024-25
   and 2025-26 entirely as of 2026-07-08), so it can't cover our 5-season window.
   Instead every season is loaded uniformly from its own `teams.csv`,
   `players_raw.csv`, `fixtures.csv`, and `gws/merged_gw.csv`.
2. Does NOT use fuzzy name matching as the primary cross-season player ID mechanism.
   Every season's `players_raw.csv` carries a `code` field — FPL's own persistent
   player identifier, stable across seasons (verified: Salah code=118748 unchanged
   2021-22 through 2025-26; Haaland code=223094 unchanged since his 2022-23 debut).
   `code` is used as the primary join key; name+team fuzzy matching (via stdlib
   `difflib`) is only a fallback for the rare row whose `element` id has no match
   in that season's `players_raw.csv`.

Column drift across seasons is real and expected, not a bug to "fix": xG fields
appear from 2022-23, `starts` from 2023-24, DEFCON fields only from 2025-26 (matches
plan §0.3/§6.3 — DEFCON rows are NULL, not zero, before 2025-26).
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path

import duckdb
import httpx
import pandas as pd

RAW_BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
POLITE_INTERVAL = 0.3  # seconds between raw.githubusercontent requests
DEFAULT_TTL = 7 * 24 * 3600  # historical seasons rarely change once closed

POSITION_MAP = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

# player_gw_history columns we populate from merged_gw.csv when present; anything
# missing for a given season's file becomes NaN -> NULL, which is correct (not 0).
GW_HISTORY_SOURCE_COLUMNS = [
    "minutes",
    "starts",
    "total_points",
    "goals_scored",
    "assists",
    "clean_sheets",
    "goals_conceded",
    "own_goals",
    "penalties_saved",
    "penalties_missed",
    "yellow_cards",
    "red_cards",
    "saves",
    "bonus",
    "bps",
    "influence",
    "creativity",
    "threat",
    "ict_index",
    "expected_goals",
    "expected_assists",
    "expected_goal_involvements",
    "expected_goals_conceded",
    "defensive_contribution",
    "clearances_blocks_interceptions",
    "recoveries",
    "tackles",
    "value",
    "selected",
    "transfers_in",
    "transfers_out",
    "transfers_balance",
]


class TeamNameMismatchError(RuntimeError):
    """A merged_gw.csv team name has no match in that season's teams.csv.

    This must not be silently coerced to NULL team_id — it means either vaastav
    renamed a club or our name-matching assumption broke.
    """


class UnexpectedDuplicateRowsError(RuntimeError):
    """(gw, fixture_id, element_id) duplicates whose non-key columns disagree.

    A handful of *exact* duplicate rows are known to exist in vaastav's merged_gw.csv
    (verified: 2025-26 GW1 fixture 1 element 391 — a 0-minute bench player, byte-for-
    byte identical pair) and are safe to drop. If the duplicate rows carry different
    stats, that's a genuine data conflict that needs a human look, not a silent pick.
    """


def _drop_exact_duplicate_rows(gw: pd.DataFrame, season: str) -> pd.DataFrame:
    key = ["gw", "fixture_id", "element_id"]
    dupe_mask = gw.duplicated(subset=key, keep=False)
    if not dupe_mask.any():
        return gw
    dupes = gw[dupe_mask]
    non_identical = dupes.groupby(key, dropna=False).nunique(dropna=False).gt(1).any(axis=1)
    if non_identical.any():
        raise UnexpectedDuplicateRowsError(
            f"{season}: duplicate (gw, fixture_id, element_id) rows with differing "
            f"stats found — {non_identical[non_identical].index.tolist()}"
        )
    return gw.drop_duplicates(subset=key, keep="first")


@dataclass
class VaastavClient:
    cache_dir: Path
    ttl_seconds: float = DEFAULT_TTL

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._http = httpx.Client(timeout=30.0)
        self._last_request = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> VaastavClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _fetch_csv(self, relpath: str) -> pd.DataFrame:
        cache_path = self.cache_dir / relpath
        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age <= self.ttl_seconds:
                return pd.read_csv(cache_path, low_memory=False)

        elapsed = time.monotonic() - self._last_request
        if elapsed < POLITE_INTERVAL:
            time.sleep(POLITE_INTERVAL - elapsed)
        response = self._http.get(f"{RAW_BASE}/{relpath}")
        self._last_request = time.monotonic()
        response.raise_for_status()

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(response.content)
        return pd.read_csv(io.BytesIO(response.content), low_memory=False)

    def teams(self, season: str) -> pd.DataFrame:
        df = self._fetch_csv(f"{season}/teams.csv")
        out = df[["id", "code", "name", "short_name", "strength"]].copy()
        for col in [
            "strength_overall_home",
            "strength_overall_away",
            "strength_attack_home",
            "strength_attack_away",
            "strength_defence_home",
            "strength_defence_away",
        ]:
            out[col] = df[col] if col in df.columns else pd.NA
        out.insert(0, "season", season)
        out = out.rename(columns={"id": "team_id"})
        return out

    def players_raw(self, season: str) -> pd.DataFrame:
        df = self._fetch_csv(f"{season}/players_raw.csv")
        cols = ["id", "code", "first_name", "second_name", "web_name", "element_type", "team"]
        out = df[cols].copy()
        out.insert(0, "season", season)
        out = out.rename(columns={"id": "element_id", "team": "team_id"})
        return out

    def fixtures(self, season: str) -> pd.DataFrame:
        df = self._fetch_csv(f"{season}/fixtures.csv")
        cols = [
            "id",
            "event",
            "kickoff_time",
            "team_h",
            "team_a",
            "team_h_score",
            "team_a_score",
            "team_h_difficulty",
            "team_a_difficulty",
            "finished",
        ]
        out = df[cols].copy()
        out.insert(0, "season", season)
        out = out.rename(columns={"id": "fixture_id"})
        return out

    def merged_gw(self, season: str) -> pd.DataFrame:
        df = self._fetch_csv(f"{season}/gws/merged_gw.csv")
        out = pd.DataFrame({"season": season, "gw": df["GW"], "fixture_id": df["fixture"]})
        out["element_id"] = df["element"]
        out["team_name"] = df["team"]
        out["opponent_team_id"] = df["opponent_team"]
        out["was_home"] = df["was_home"]
        out["position"] = df["position"]
        out["kickoff_time"] = df["kickoff_time"]
        out["player_name"] = df["name"]
        for col in GW_HISTORY_SOURCE_COLUMNS:
            out[col] = df[col] if col in df.columns else pd.NA
        return out


def _resolve_team_ids(gw: pd.DataFrame, teams: pd.DataFrame, season: str) -> pd.DataFrame:
    name_to_id = dict(zip(teams["name"], teams["team_id"], strict=True))
    unmatched = set(gw["team_name"].unique()) - set(name_to_id)
    if unmatched:
        raise TeamNameMismatchError(f"{season}: team names {unmatched} not found in teams.csv")
    gw = gw.copy()
    gw["team_id"] = gw["team_name"].map(name_to_id)
    return gw.drop(columns=["team_name"])


def _resolve_codes(gw: pd.DataFrame, players: pd.DataFrame, season: str) -> pd.DataFrame:
    """Primary: exact join on (season, element_id) -> code. Fallback: fuzzy name match."""
    id_to_code = dict(zip(players["element_id"], players["code"], strict=True))
    gw = gw.copy()
    gw["code"] = gw["element_id"].map(id_to_code)

    missing = gw[gw["code"].isna()]
    if len(missing) > 0:
        name_to_code = dict(zip(players["web_name"], players["code"], strict=True))
        candidate_names = list(name_to_code)
        for idx, row in missing.iterrows():
            match = get_close_matches(row["player_name"], candidate_names, n=1, cutoff=0.8)
            if match:
                gw.loc[idx, "code"] = name_to_code[match[0]]

    still_missing = gw["code"].isna().sum()
    if still_missing > 0:
        raise ValueError(
            f"{season}: {still_missing} player_gw_history rows have no resolvable code "
            f"(neither exact element_id join nor fuzzy name match). Needs a manual override."
        )
    gw["code"] = gw["code"].astype("int64")
    return gw.drop(columns=["player_name"])


def load_season(con: duckdb.DuckDBPyConnection, client: VaastavClient, season: str) -> dict:
    """Load one season's teams/players/fixtures/gw-history into DuckDB. Idempotent
    (DELETE + INSERT per season, not append) so re-running refresh doesn't duplicate.

    Local DataFrame variables are deliberately named *_df, distinct from any table
    name in the schema: DuckDB's replacement scan resolves a bare identifier in a
    FROM clause against local Python variables only when no catalog table already
    owns that name, so `teams_df` (not `teams`) is what makes `FROM teams_df` find
    the DataFrame instead of silently reading the (empty-delta) `teams` table.
    """
    teams_df = client.teams(season)
    players_raw_df = client.players_raw(season)
    fixtures_df = client.fixtures(season)
    gw_df = client.merged_gw(season)

    gw_df = _drop_exact_duplicate_rows(gw_df, season)
    gw_df = _resolve_team_ids(gw_df, teams_df, season)
    gw_df = _resolve_codes(gw_df, players_raw_df, season)
    gw_df["source"] = "vaastav"

    con.execute("DELETE FROM teams WHERE season = ?", [season])
    con.execute("INSERT INTO teams SELECT * FROM teams_df")

    con.execute("DELETE FROM fixtures WHERE season = ?", [season])
    con.execute("INSERT INTO fixtures SELECT * FROM fixtures_df")

    player_season_df = players_raw_df.copy()
    player_season_df["position"] = player_season_df["element_type"].map(POSITION_MAP)
    player_season_df = player_season_df[
        ["season", "element_id", "code", "team_id", "position", "web_name"]
    ]
    con.execute("DELETE FROM player_season WHERE season = ?", [season])
    con.execute("INSERT INTO player_season SELECT * FROM player_season_df")

    players_dim_df = players_raw_df[["code", "first_name", "second_name", "web_name"]].copy()
    players_dim_df["last_seen_season"] = season
    con.execute(
        """
        INSERT INTO players AS p
        SELECT * FROM players_dim_df
        ON CONFLICT (code) DO UPDATE SET
            first_name = excluded.first_name,
            second_name = excluded.second_name,
            web_name = excluded.web_name,
            last_seen_season = excluded.last_seen_season
        WHERE excluded.last_seen_season >= p.last_seen_season
        """
    )

    gw_history_df = gw_df[
        [
            "season",
            "gw",
            "fixture_id",
            "code",
            "element_id",
            "team_id",
            "opponent_team_id",
            "was_home",
            "position",
            "kickoff_time",
            *GW_HISTORY_SOURCE_COLUMNS,
            "source",
        ]
    ]
    con.execute("DELETE FROM player_gw_history WHERE season = ?", [season])
    con.execute("INSERT INTO player_gw_history SELECT * FROM gw_history_df")

    return {
        "season": season,
        "teams": len(teams_df),
        "players": len(players_raw_df),
        "fixtures": len(fixtures_df),
        "gw_rows": len(gw_history_df),
    }


def load_all_seasons(
    con: duckdb.DuckDBPyConnection,
    cache_dir: Path,
    seasons: list[str] = SEASONS,
    ttl_seconds: float = DEFAULT_TTL,
) -> list[dict]:
    summaries = []
    with VaastavClient(cache_dir=cache_dir, ttl_seconds=ttl_seconds) as client:
        for season in seasons:
            summaries.append(load_season(con, client, season))
    return summaries
