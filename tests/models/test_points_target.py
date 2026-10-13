import numpy as np
import pandas as pd

from fplscout.models.points import xpts_target


def _row(**kw):
    base = dict(position="FWD", actual_minutes=90, total_points=6, goals_scored=1,
                assists=0, clean_sheets=0, goals_conceded=2, expected_goals=0.1,
                expected_assists=0.0, expected_goals_conceded=1.5)
    base.update(kw)
    return base


def test_lucky_goal_is_mostly_removed_from_the_target():
    df = pd.DataFrame([_row()])
    # 6 realized = 2 appearance + 4 goal; expectation of the goal is 0.1 * 4
    assert abs(xpts_target(df).iloc[0] - (6 - 4 + 0.4)) < 1e-9


def test_rows_without_xg_keep_realized_points():
    df = pd.DataFrame([_row(expected_goals=np.nan)])
    assert xpts_target(df).iloc[0] == 6


def test_defender_clean_sheet_is_replaced_by_its_probability():
    df = pd.DataFrame([_row(position="DEF", total_points=6, goals_scored=0,
                            clean_sheets=1, goals_conceded=0, expected_goals=0.0,
                            expected_goals_conceded=0.5)])
    expected_cs = np.exp(-0.5) * 4
    # E[floor(G/2)] for Poisson(0.5) ~ 0.0902
    t = xpts_target(df).iloc[0]
    assert abs(t - (6 - 4 + expected_cs - 0.0902)) < 0.01
