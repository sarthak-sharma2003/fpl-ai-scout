"""Minutes model: P(0 min), P(1-59), P(60+) — plan §Phase3.1, "the single
highest-leverage model in FPL" (§6.1).

One global LightGBM multiclass model (not split by position) — position is a
categorical input feature instead. Simplification vs. training 4 separate models;
defensible since rotation patterns share most of their signal (recent minutes/starts
trend, fixture congestion) across positions, and it keeps the training harness simple
for a v1. Revisit if the validation report shows position-specific bias.
"""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd

from fplscout.models.dataset import CATEGORICAL_COLUMNS, FEATURE_COLUMNS, minutes_class

# Regularised 2026-10-13 (was lr 0.05, leaf 50, no bagging/L2, 200 rounds).
# The transfer advice flipped between runs a few hours apart on near-identical
# data, and the minutes model was the moving part: retrained on a 2% resample
# of its training rows, a player's P(60+) moved by up to 0.27 (p95 0.06).
# These settings halve that (p95 0.033, max 0.12) AND fit better: holdout
# log-loss 0.4067 -> 0.4030 (2023-24) and 0.4460 -> 0.4427 (2024-25), better
# among regulars too. Replay, 2 seeds x 2022-23..2025-26: +0.87 +- 0.76 pts/GW
# (run 38369784506 vs 38367051085) -- positive, not significant on its own.
LGB_PARAMS = {
    "deterministic": True, "force_row_wise": True, "num_threads": 4,
    "objective": "multiclass",
    "num_class": 3,
    "learning_rate": 0.03,
    "num_leaves": 31,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 5.0,
    "seed": 7,
    "verbose": -1,
}
NUM_BOOST_ROUND = 250


def train(train_df: pd.DataFrame) -> lgb.Booster:
    X = train_df[FEATURE_COLUMNS]
    y = minutes_class(train_df["actual_minutes"])
    dataset = lgb.Dataset(X, label=y, categorical_feature=CATEGORICAL_COLUMNS, free_raw_data=False)
    # A leaf needs 200 rows on real training sets (>= ~25k rows a season); a
    # tiny one (tests, a first-season smoke run) could not split at all.
    params = {**LGB_PARAMS,
              "min_data_in_leaf": min(LGB_PARAMS["min_data_in_leaf"],
                                      max(20, len(train_df) // 100))}
    return lgb.train(params, dataset, num_boost_round=NUM_BOOST_ROUND)


def predict_proba(booster: lgb.Booster, df: pd.DataFrame) -> np.ndarray:
    """Returns an (n, 3) array: columns are P(0), P(1-59), P(60+)."""
    X = df[FEATURE_COLUMNS]
    return booster.predict(X)


def expected_minutes(proba: np.ndarray) -> np.ndarray:
    """Point estimate: midpoints of each bucket (0, 30, 90), weighted by P."""
    midpoints = np.array([0.0, 30.0, 90.0])
    return proba @ midpoints


def apply_availability(proba: np.ndarray, factor: np.ndarray) -> np.ndarray:
    """Inference-time-only overlay: scales P(1-59)/P(60+) by a per-row live
    availability factor (1.0 = fully expected to play, 0.0 = ruled out),
    folding the removed mass back into P(0). factor of all-ones is a no-op.

    Never call this on training data — live status/chance_of_playing is a
    single "now" snapshot, not per-GW history, and would leak (see
    features/build.py docstring)."""
    factor = np.asarray(factor, dtype=float).reshape(-1, 1)
    out = proba.copy()
    out[:, 1:] = proba[:, 1:] * factor
    out[:, 0] = 1.0 - out[:, 1] - out[:, 2]
    return out
