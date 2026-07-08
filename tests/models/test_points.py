from __future__ import annotations

import numpy as np
import pandas as pd

from fplscout.models import points


def _with_model_features(df):
    df = df.copy()
    rng = np.random.default_rng(0)
    n = len(df)
    df["mins_p0"] = rng.uniform(0, 1, n)
    df["mins_p1_59"] = rng.uniform(0, 1, n)
    df["mins_p60_plus"] = rng.uniform(0, 1, n)
    df["expected_minutes"] = rng.uniform(0, 90, n)
    df["team_xg_for"] = rng.uniform(0, 3, n)
    df["team_xg_against"] = rng.uniform(0, 3, n)
    df["clean_sheet_prob"] = rng.uniform(0, 1, n)
    return df


def test_train_produces_a_model_per_position_with_enough_data(synthetic_dataset):
    df = _with_model_features(synthetic_dataset)
    models = points.train(df)
    counts = df["position"].value_counts()
    for position in points.POSITIONS:
        if counts.get(position, 0) >= 50:
            assert position in models
            assert set(models[position]) == {"mean", "q10", "q90"}


def test_predict_returns_ev_and_quantiles_in_reasonable_order(synthetic_dataset):
    df = _with_model_features(synthetic_dataset)
    models = points.train(df)
    preds = points.predict(models, df)
    covered = preds[preds["position"].isin(models)]
    assert not covered["ev_points"].isna().any()
    # q10 should generally be <= q90 (quantile regressors trained independently,
    # so allow a small violation rate rather than requiring it row-for-row)
    violations = (covered["q10_points"] > covered["q90_points"]).mean()
    assert violations < 0.1


def test_predict_leaves_unmodeled_positions_as_nan(synthetic_dataset):
    df = _with_model_features(synthetic_dataset)
    # force GKP down to too little data to train (< 50 rows)
    non_gkp = df[df["position"] != "GKP"]
    few_gkp = df[df["position"] == "GKP"].head(5)
    df = pd.concat([non_gkp, few_gkp], ignore_index=True)

    models = points.train(df)
    assert "GKP" not in models
    preds = points.predict(models, df)
    gkp_preds = preds[preds["position"] == "GKP"]
    assert gkp_preds["ev_points"].isna().all()


def test_independent_variant_excludes_fpl_xp(synthetic_dataset):
    df = _with_model_features(synthetic_dataset)
    assert "fpl_xp" not in points.INDEPENDENT_FEATURE_COLUMNS
    assert "fpl_xp" in points.FULL_FEATURE_COLUMNS
    models = points.train(df, feature_columns=points.INDEPENDENT_FEATURE_COLUMNS)
    preds = points.predict(models, df, feature_columns=points.INDEPENDENT_FEATURE_COLUMNS)
    assert not preds["ev_points"].isna().all()


def test_feature_gain_by_column_shape(synthetic_dataset):
    df = _with_model_features(synthetic_dataset)
    models = points.train(df)
    gain = points.feature_gain_by_column(models)
    assert set(gain.columns) == {"position", "feature", "gain", "gain_share"}
    for position in models:
        sub = gain[gain["position"] == position]
        assert abs(sub["gain_share"].sum() - 1.0) < 1e-6
