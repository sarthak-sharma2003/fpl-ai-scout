"""Shared training-matrix assembly for minutes.py / points.py.

Not in the plan's literal file list for §3, but factors out season-filtering and
feature-column selection that both models need identically — avoids duplicating the
same DuckDB query/merge logic in two places.

`fpl_xp` (vaastav's `xP` column) is deliberately NOT loaded here. vaastav's own
README documents it as scraped from bootstrap-static's `ep_this` *after* each
gameweek ends, with an empirically observed same-GW correlation to actual points
the README itself flags as "unusually high for a genuinely pre-match feature" —
i.e. materially post-match-contaminated for historical data, not just occasionally
missing. An earlier pass here had a `_null_out_corrupted_xp_gameweeks` function
that treated this as a coverage/missing-data problem (nulling out all-zero
gameweeks); that was solving the wrong problem — the column needed removing
entirely for historical training, not partial cleanup. See models/points.py
docstring for the full history and models/train.py for what replaces it.
"""

from __future__ import annotations

import duckdb
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

TARGET_COLUMNS = ["total_points", "minutes"]


def load_dataset(con: duckdb.DuckDBPyConnection, seasons: list[str]) -> pd.DataFrame:
    """features JOIN player_gw_history (for targets), restricted to `seasons`."""
    placeholders = ", ".join(["?"] * len(seasons))
    df = con.execute(
        f"""
        SELECT f.*, h.total_points, h.minutes AS actual_minutes
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
    return df


def minutes_class(minutes: pd.Series) -> pd.Series:
    """0 = didn't play, 1 = played 1-59, 2 = played 60+."""
    return pd.cut(
        minutes, bins=[-1, 0, 59, 200], labels=[0, 1, 2], right=True
    ).astype(int)
