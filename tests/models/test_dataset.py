from __future__ import annotations

import numpy as np
import pandas as pd

from fplscout.models.dataset import _null_out_corrupted_xp_gameweeks, minutes_class


def test_minutes_class_buckets():
    minutes = pd.Series([0, 1, 45, 59, 60, 90])
    classes = minutes_class(minutes)
    assert classes.tolist() == [0, 1, 1, 1, 2, 2]


def test_null_out_corrupted_xp_gameweeks_nulls_all_zero_groups():
    df = pd.DataFrame(
        {
            "season": ["2025-26"] * 4 + ["2025-26"] * 2,
            "gw": [7, 7, 7, 7, 1, 1],
            "fpl_xp": [0.0, 0.0, 0.0, 0.0, 1.5, 0.0],
        }
    )
    result = _null_out_corrupted_xp_gameweeks(df)
    # GW7 is entirely zero -> nulled out
    assert result.loc[result["gw"] == 7, "fpl_xp"].isna().all()
    # GW1 has a real nonzero value alongside a genuine zero -> left alone
    assert result.loc[result["gw"] == 1, "fpl_xp"].tolist() == [1.5, 0.0]


def test_null_out_corrupted_xp_gameweeks_leaves_real_data_untouched():
    df = pd.DataFrame(
        {"season": ["2024-25"] * 3, "gw": [1, 1, 1], "fpl_xp": [1.2, 3.4, 0.5]}
    )
    result = _null_out_corrupted_xp_gameweeks(df)
    assert not result["fpl_xp"].isna().any()
    assert np.allclose(result["fpl_xp"], df["fpl_xp"])
