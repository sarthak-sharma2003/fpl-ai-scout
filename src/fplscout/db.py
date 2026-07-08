"""DuckDB connection + schema for the fpl-ai-scout data layer (plan §Phase 1).

Single file, one schema, shared by ingest/features/decide/backtest/api. Tables not
yet populated by a given phase still exist (empty) so downstream code can assume the
full schema is always present.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

SCHEMA_SQL = """
-- One row per (season, team_id): team dimension, one snapshot per season since
-- strength ratings and names can change season to season.
CREATE TABLE IF NOT EXISTS teams (
    season TEXT NOT NULL,
    team_id INTEGER NOT NULL,
    code INTEGER NOT NULL,
    name TEXT NOT NULL,
    short_name TEXT NOT NULL,
    strength INTEGER,
    strength_overall_home INTEGER,
    strength_overall_away INTEGER,
    strength_attack_home INTEGER,
    strength_attack_away INTEGER,
    strength_defence_home INTEGER,
    strength_defence_away INTEGER,
    PRIMARY KEY (season, team_id)
);

-- Canonical player dimension, one row per persistent FPL `code` (stable across
-- seasons, unlike the per-season numeric `id`). Latest known identity fields.
CREATE TABLE IF NOT EXISTS players (
    code BIGINT PRIMARY KEY,
    first_name TEXT,
    second_name TEXT,
    web_name TEXT,
    last_seen_season TEXT
);

-- (season, element_id) -> code mapping, plus season-scoped attributes (team,
-- position can change within/between seasons).
CREATE TABLE IF NOT EXISTS player_season (
    season TEXT NOT NULL,
    element_id INTEGER NOT NULL,
    code BIGINT NOT NULL,
    team_id INTEGER,
    position TEXT,
    web_name TEXT,
    PRIMARY KEY (season, element_id)
);

CREATE TABLE IF NOT EXISTS fixtures (
    season TEXT NOT NULL,
    fixture_id INTEGER NOT NULL,
    event INTEGER,
    kickoff_time TIMESTAMP,
    team_h INTEGER,
    team_a INTEGER,
    team_h_score INTEGER,
    team_a_score INTEGER,
    team_h_difficulty INTEGER,
    team_a_difficulty INTEGER,
    finished BOOLEAN,
    PRIMARY KEY (season, fixture_id)
);

CREATE TABLE IF NOT EXISTS gameweeks (
    season TEXT NOT NULL,
    event INTEGER NOT NULL,
    deadline_time TIMESTAMP,
    finished BOOLEAN,
    average_entry_score INTEGER,
    PRIMARY KEY (season, event)
);

-- The core long table: one row per player per fixture played (naturally handles
-- double gameweeks — a player gets two rows in the same `gw` with different
-- fixture_id). NO leakage guarantee here; that's enforced at the feature-store
-- layer (Phase 2), which must only read rows with kickoff_time < as-of deadline.
CREATE TABLE IF NOT EXISTS player_gw_history (
    season TEXT NOT NULL,
    gw INTEGER NOT NULL,
    fixture_id INTEGER NOT NULL,
    code BIGINT NOT NULL,
    element_id INTEGER NOT NULL,
    team_id INTEGER,
    opponent_team_id INTEGER,
    was_home BOOLEAN,
    position TEXT,
    kickoff_time TIMESTAMP,
    minutes INTEGER,
    starts INTEGER,
    total_points INTEGER,
    goals_scored INTEGER,
    assists INTEGER,
    clean_sheets INTEGER,
    goals_conceded INTEGER,
    own_goals INTEGER,
    penalties_saved INTEGER,
    penalties_missed INTEGER,
    yellow_cards INTEGER,
    red_cards INTEGER,
    saves INTEGER,
    bonus INTEGER,
    bps INTEGER,
    influence DOUBLE,
    creativity DOUBLE,
    threat DOUBLE,
    ict_index DOUBLE,
    expected_goals DOUBLE,
    expected_assists DOUBLE,
    expected_goal_involvements DOUBLE,
    expected_goals_conceded DOUBLE,
    -- DEFCON: NULL (missing-not-zero) for seasons before 2025-26 — plan §6.3.
    defensive_contribution INTEGER,
    clearances_blocks_interceptions INTEGER,
    recoveries INTEGER,
    tackles INTEGER,
    value INTEGER,
    selected INTEGER,
    transfers_in INTEGER,
    transfers_out INTEGER,
    transfers_balance INTEGER,
    source TEXT NOT NULL,
    PRIMARY KEY (season, code, fixture_id)
);

-- Our own squad state (Phase 4). Empty until squad_state.py writes to it.
CREATE TABLE IF NOT EXISTS our_entry (
    entry_id INTEGER PRIMARY KEY,
    name TEXT,
    bank INTEGER,
    team_value INTEGER,
    free_transfers INTEGER,
    last_synced_gw INTEGER
);

CREATE TABLE IF NOT EXISTS our_picks (
    gw INTEGER NOT NULL,
    code BIGINT NOT NULL,
    position INTEGER,
    multiplier INTEGER,
    is_captain BOOLEAN,
    is_vice_captain BOOLEAN,
    PRIMARY KEY (gw, code)
);

CREATE TABLE IF NOT EXISTS our_transfers (
    gw INTEGER NOT NULL,
    code_in BIGINT NOT NULL,
    code_out BIGINT NOT NULL,
    cost_in INTEGER,
    cost_out INTEGER,
    "time" TIMESTAMP,
    PRIMARY KEY (gw, code_in, code_out)
);

-- Model output (Phase 3). Empty until models/points.py writes to it.
CREATE TABLE IF NOT EXISTS projections (
    season TEXT NOT NULL,
    gw INTEGER NOT NULL,
    code BIGINT NOT NULL,
    model_version TEXT NOT NULL,
    ev_points DOUBLE,
    q10_points DOUBLE,
    q90_points DOUBLE,
    ev_minutes DOUBLE,
    p_appearance DOUBLE,
    p_60_plus DOUBLE,
    p_clean_sheet DOUBLE,
    generated_at TIMESTAMP,
    PRIMARY KEY (season, gw, code, model_version)
);

-- Optimizer output (Phase 4+). Empty until decide/optimizer.py writes to it.
CREATE TABLE IF NOT EXISTS recommendations (
    season TEXT NOT NULL,
    gw INTEGER NOT NULL,
    generated_at TIMESTAMP NOT NULL,
    squad JSON,
    starting_xi JSON,
    captain_code BIGINT,
    vice_captain_code BIGINT,
    transfers JSON,
    hits INTEGER,
    chip TEXT,
    confidence DOUBLE,
    PRIMARY KEY (season, gw, generated_at)
);
"""

TABLES = [
    "teams",
    "players",
    "player_season",
    "fixtures",
    "gameweeks",
    "player_gw_history",
    "our_entry",
    "our_picks",
    "our_transfers",
    "projections",
    "recommendations",
]


def connect(db_path: Path | str) -> duckdb.DuckDBPyConnection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA_SQL)
