from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fplscout.models.dataset import CATEGORICAL_COLUMNS, FEATURE_COLUMNS


@pytest.fixture
def synthetic_dataset():
    """A synthetic dataset shaped like models.dataset.load_dataset()'s output:
    minutes and points are made to correlate with roll5_minutes/roll5_points so the
    models have real signal to learn, rather than pure noise."""
    rng = np.random.default_rng(42)
    n = 400
    positions = rng.choice(["GKP", "DEF", "MID", "FWD"], size=n)
    roll5_minutes = rng.uniform(0, 90, size=n)
    roll5_points = rng.uniform(0, 12, size=n)

    numeric_cols = [c for c in FEATURE_COLUMNS if c not in CATEGORICAL_COLUMNS]
    df = pd.DataFrame({col: rng.normal(size=n) for col in numeric_cols})
    df["position"] = positions
    df["price_band"] = rng.choice(["budget", "low_mid", "mid", "premium", "elite"], size=n)
    df["roll5_minutes"] = roll5_minutes
    df["roll5_points"] = roll5_points
    df["is_dgw"] = rng.choice([True, False], size=n)
    df["promoted_team"] = rng.choice([True, False], size=n)

    for col in CATEGORICAL_COLUMNS:
        df[col] = df[col].astype("category")

    # targets correlated with the rolling features above, plus noise
    df["actual_minutes"] = np.clip(
        roll5_minutes + rng.normal(0, 15, size=n), 0, 90
    ).round().astype(int)
    df["total_points"] = np.clip(
        roll5_points * (df["actual_minutes"] >= 60) + rng.normal(0, 1, size=n), 0, None
    )
    df["fpl_xp"] = df["total_points"] + rng.normal(0, 1, size=n)
    df["season"] = "2099-00"
    df["gw"] = rng.integers(1, 39, size=n)
    df["fixture_id"] = np.arange(n)
    df["code"] = np.arange(n)
    return df
