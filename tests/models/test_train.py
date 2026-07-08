from __future__ import annotations

import numpy as np
import pandas as pd

from fplscout.models.train import (
    _beats,
    _compare_columns,
    _rmse,
    _spearman,
    route_predictions,
    train_test_split_by_season,
)


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


def test_route_predictions_picks_full_where_valid_independent_otherwise():
    full = pd.DataFrame(
        {"ev_points": [10.0, 20.0, 30.0], "q10_points": [5.0] * 3, "q90_points": [15.0] * 3}
    )
    independent = pd.DataFrame(
        {"ev_points": [1.0, 2.0, 3.0], "q10_points": [0.5] * 3, "q90_points": [1.5] * 3}
    )
    mask = pd.Series([True, False, True])
    routed = route_predictions(full, independent, mask)
    assert routed["ev_points"].tolist() == [10.0, 2.0, 30.0]


def test_route_predictions_preserves_index_alignment():
    full = pd.DataFrame(
        {"ev_points": [10.0, 20.0], "q10_points": [5.0, 5.0], "q90_points": [15.0, 15.0]},
        index=[7, 9],
    )
    independent = pd.DataFrame(
        {"ev_points": [1.0, 2.0], "q10_points": [0.5, 0.5], "q90_points": [1.5, 1.5]},
        index=[7, 9],
    )
    mask = pd.Series([False, True], index=[7, 9])
    routed = route_predictions(full, independent, mask)
    assert routed.loc[7, "ev_points"] == 1.0
    assert routed.loc[9, "ev_points"] == 20.0


def test_compare_columns_returns_one_bundle_per_predictor():
    df = pd.DataFrame(
        {
            "gw": [1, 1, 2, 2],
            "actual": [1.0, 2.0, 3.0, 4.0],
            "pred_a": [1.0, 2.0, 3.0, 4.0],
            "pred_b": [4.0, 3.0, 2.0, 1.0],
        }
    )
    result = _compare_columns(df, "actual", {"a": "pred_a", "b": "pred_b"})
    assert set(result.keys()) == {"a", "b"}
    assert result["a"]["rmse"] == 0.0


def test_beats_true_when_challenger_has_higher_spearman():
    challenger = {"mean_per_gw_spearman": 0.5}
    baseline = {"mean_per_gw_spearman": 0.3}
    assert _beats(challenger, baseline) is True
    assert _beats(baseline, challenger) is False


def test_beats_true_when_baseline_has_no_valid_gameweeks():
    challenger = {"mean_per_gw_spearman": 0.1}
    baseline = {"mean_per_gw_spearman": float("nan")}
    assert _beats(challenger, baseline) is True
