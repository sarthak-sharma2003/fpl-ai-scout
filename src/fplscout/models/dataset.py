"""Shared training-matrix assembly for minutes.py / points.py.

Not in the plan's literal file list for §3, but factors out season-filtering and
feature-column selection that both models need identically — avoids duplicating the
same DuckDB query/merge logic in two places.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "roll3_points", "roll5_points", "roll10_points",
    "roll3_minutes", "roll5_minutes", "roll10_minutes",
    "roll3_xg", "roll5_xg", "roll10_xg",
    "roll3_xa", "roll5_xa", "roll10_xa",
    "roll3_xgi", "roll5_xgi", "roll10_xgi",
    "roll3_xgc", "roll5_xgc", "roll10_xgc",
    "roll3_bps", "roll5_bps", "roll10_bps",
    "roll3_saves", "roll5_saves", "roll10_saves",
    "roll3_goals_conceded", "roll5_goals_conceded", "roll10_goals_conceded",
    "roll3_defensive_contribution", "roll5_defensive_contribution",
    "roll10_defensive_contribution",
    "roll3_cbit", "roll5_cbit", "roll10_cbit",
    "roll3_recoveries", "roll5_recoveries", "roll10_recoveries",
    "roll3_tackles", "roll5_tackles", "roll10_tackles",
    "roll5_xg_per90", "roll5_xa_per90", "roll5_xgi_per90", "roll5_bps_per90",
    "roll5_xg_share", "roll5_xa_share", "roll5_xgi_share",
    "fdr", "opponent_strength", "rest_days", "is_dgw",
    "team_roll5_goals_for", "team_roll5_goals_against",
    "roll5_started_share",
    "value", "price_band", "promoted_team", "position",
]

CATEGORICAL_COLUMNS = ["position", "price_band"]

TARGET_COLUMNS = ["total_points", "minutes", "fpl_xp"]


def _null_out_corrupted_xp_gameweeks(df: pd.DataFrame) -> pd.DataFrame:
    """vaastav's `xP` column is entirely 0.0 for large stretches of 2025-26 (e.g.
    GW7, GW10-23, GW25-28, GW30-37 — 83% of the season's rows) — a real upstream
    data-quality gap, not FPL genuinely predicting zero expected points for an
    entire gameweek (surrounding gameweeks and prior seasons run ~1.0-1.5 mean).
    Any (season, gw) group whose mean fpl_xp is exactly 0 gets nulled out so it
    reads as missing — LightGBM handles NaN natively — rather than a false
    "nothing will happen this week" signal fed to the model, and so the xP
    baseline in the validation report is scored only on gameweeks it actually has
    data for instead of being dragged down by a data gap that has nothing to do
    with FPL's prediction quality.
    """
    df = df.copy()
    group_mean = df.groupby(["season", "gw"])["fpl_xp"].transform("mean")
    df.loc[group_mean == 0, "fpl_xp"] = np.nan
    return df


def load_dataset(con: duckdb.DuckDBPyConnection, seasons: list[str]) -> pd.DataFrame:
    """features JOIN player_gw_history (for targets), restricted to `seasons`."""
    placeholders = ", ".join(["?"] * len(seasons))
    df = con.execute(
        f"""
        SELECT f.*, h.total_points, h.minutes AS actual_minutes, h.fpl_xp
        FROM features f
        JOIN player_gw_history h
          ON f.season = h.season AND f.code = h.code AND f.fixture_id = h.fixture_id
        WHERE f.season IN ({placeholders})
        """,
        seasons,
    ).df()
    df["is_dgw"] = df["is_dgw"].astype(bool)
    df["promoted_team"] = df["promoted_team"].astype(bool)
    for col in CATEGORICAL_COLUMNS:
        df[col] = df[col].astype("category")
    df = _null_out_corrupted_xp_gameweeks(df)
    return df


def minutes_class(minutes: pd.Series) -> pd.Series:
    """0 = didn't play, 1 = played 1-59, 2 = played 60+."""
    return pd.cut(
        minutes, bins=[-1, 0, 59, 200], labels=[0, 1, 2], right=True
    ).astype(int)
