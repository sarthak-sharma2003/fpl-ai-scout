"""euro_days_before / euro_days_after: the club's nearest European tie either
side of a league kickoff, within the window, same club and season only."""

import pandas as pd

from fplscout.features.build import add_euro_gaps


def test_gaps_to_nearest_european_tie_each_side():
    league = pd.DataFrame({
        "season": ["2026-27"] * 3,
        "team_id": [6, 6, 7],
        "kickoff_time": pd.to_datetime(["2026-11-08 14:00", "2026-11-28 15:00",
                                        "2026-11-08 14:00"]),
    })
    euro = pd.DataFrame({
        "season": ["2026-27"] * 3,
        "team_id": [6, 6, 6],
        "kickoff_time": pd.to_datetime(["2026-11-05 20:00", "2026-11-11 20:00",
                                        "2025-11-05 20:00"]),
    })
    out = add_euro_gaps(league, euro)
    # Thursday night -> Sunday 2pm, then Wednesday night
    assert round(out.loc[0, "euro_days_before"], 2) == 2.75
    assert round(out.loc[0, "euro_days_after"], 2) == 3.25
    # 17 days on: outside the window either side
    assert pd.isna(out.loc[1, "euro_days_before"]) and pd.isna(out.loc[1, "euro_days_after"])
    # another club the same day: not in Europe
    assert pd.isna(out.loc[2, "euro_days_before"])
    assert add_euro_gaps(league, euro.iloc[:0])["euro_days_after"].isna().all()
