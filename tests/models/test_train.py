from __future__ import annotations

import numpy as np
import pandas as pd

from fplscout.models.train import _rmse, _spearman, train_test_split_by_season


def test_train_test_split_by_season():
    seasons = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
    train, holdout = train_test_split_by_season(seasons, "2025-26")
    assert train == ["2021-22", "2022-23", "2023-24", "2024-25"]
    assert holdout == "2025-26"


def test_rmse_perfect_prediction_is_zero():
    y = pd.Series([1.0, 2.0, 3.0])
    assert _rmse(y, y) == 0.0


def test_rmse_ignores_nan_rows():
    y_true = pd.Series([1.0, 2.0, np.nan])
    y_pred = pd.Series([1.0, 2.0, 5.0])
    assert _rmse(y_true, y_pred) == 0.0


def test_spearman_perfect_rank_correlation():
    y_true = pd.Series([1.0, 2.0, 3.0, 4.0])
    y_pred = pd.Series([10.0, 20.0, 30.0, 40.0])
    assert _spearman(y_true, y_pred) == 1.0


def test_spearman_too_few_points_is_nan():
    y_true = pd.Series([1.0])
    y_pred = pd.Series([1.0])
    assert np.isnan(_spearman(y_true, y_pred))
