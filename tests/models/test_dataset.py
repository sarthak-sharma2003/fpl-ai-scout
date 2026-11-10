from __future__ import annotations

import pandas as pd

from fplscout.models.dataset import minutes_class


def test_minutes_class_buckets():
    minutes = pd.Series([0, 1, 45, 59, 60, 90])
    classes = minutes_class(minutes)
    assert classes.tolist() == [0, 1, 1, 1, 2, 2]


def test_load_dataset_row_order_is_fixed():
    """Seeded bagging samples rows by position: an unordered query made two
    trainings on the same database disagree by up to 0.61 EV on a player."""
    import inspect

    from fplscout.models import dataset

    assert "ORDER BY f.season, f.gw, f.fixture_id, f.code" in inspect.getsource(
        dataset.load_dataset
    )
