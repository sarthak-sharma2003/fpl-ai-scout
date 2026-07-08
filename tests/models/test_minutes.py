from __future__ import annotations

import numpy as np

from fplscout.models import minutes


def test_train_and_predict_proba_shape_and_sums_to_one(synthetic_dataset):
    model = minutes.train(synthetic_dataset)
    proba = minutes.predict_proba(model, synthetic_dataset)
    assert proba.shape == (len(synthetic_dataset), 3)
    assert np.allclose(proba.sum(axis=1), 1.0, atol=1e-6)
    assert (proba >= 0).all()


def test_expected_minutes_within_bounds(synthetic_dataset):
    model = minutes.train(synthetic_dataset)
    proba = minutes.predict_proba(model, synthetic_dataset)
    exp_min = minutes.expected_minutes(proba)
    assert (exp_min >= 0).all()
    assert (exp_min <= 90).all()


def test_higher_roll5_minutes_predicts_higher_p60(synthetic_dataset):
    model = minutes.train(synthetic_dataset)
    low = synthetic_dataset.copy()
    low["roll5_minutes"] = 5.0
    high = synthetic_dataset.copy()
    high["roll5_minutes"] = 85.0
    p60_low = minutes.predict_proba(model, low)[:, 2].mean()
    p60_high = minutes.predict_proba(model, high)[:, 2].mean()
    assert p60_high > p60_low
