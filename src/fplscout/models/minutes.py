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

LGB_PARAMS = {
    "objective": "multiclass",
    "num_class": 3,
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 50,
    "verbose": -1,
}
NUM_BOOST_ROUND = 200


def train(train_df: pd.DataFrame) -> lgb.Booster:
    X = train_df[FEATURE_COLUMNS]
    y = minutes_class(train_df["actual_minutes"])
    dataset = lgb.Dataset(X, label=y, categorical_feature=CATEGORICAL_COLUMNS, free_raw_data=False)
    return lgb.train(LGB_PARAMS, dataset, num_boost_round=NUM_BOOST_ROUND)


def predict_proba(booster: lgb.Booster, df: pd.DataFrame) -> np.ndarray:
    """Returns an (n, 3) array: columns are P(0), P(1-59), P(60+)."""
    X = df[FEATURE_COLUMNS]
    return booster.predict(X)


def expected_minutes(proba: np.ndarray) -> np.ndarray:
    """Point estimate: midpoints of each bucket (0, 30, 90), weighted by P."""
    midpoints = np.array([0.0, 30.0, 90.0])
    return proba @ midpoints
