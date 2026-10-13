import numpy as np
import pandas as pd

from fplscout.features.build import anchor_gameweek


def test_second_dgw_fixture_gets_the_first_fixtures_form():
    df = pd.DataFrame({
        "code": [1, 1, 1, 2],
        "season": ["s"] * 4,
        "gw": [5, 5, 6, 5],
        "fixture_id": [10, 11, 12, 10],
        "kickoff_time": pd.to_datetime(["2025-01-01", "2025-01-04", "2025-01-08", "2025-01-01"]),
        "roll5_points": [np.nan, 9.0, 7.0, 3.0],   # 9.0 includes fixture 10's result
    })
    out = anchor_gameweek(df, ["roll5_points"]).sort_values(["code", "fixture_id"])
    got = out["roll5_points"].tolist()
    assert np.isnan(got[0]) and np.isnan(got[1]), "second leg must not see the first's result"
    assert got[2] == 7.0 and got[3] == 3.0
    assert len(out) == len(df)
