from __future__ import annotations

import pandas as pd

from fplscout.models.dataset import minutes_class


def test_minutes_class_buckets():
    minutes = pd.Series([0, 1, 45, 59, 60, 90])
    classes = minutes_class(minutes)
    assert classes.tolist() == [0, 1, 1, 1, 2, 2]
