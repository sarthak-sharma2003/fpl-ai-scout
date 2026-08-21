from __future__ import annotations

import pandas as pd
import pytest

from fplscout.models.horizon import _team_fixtures_long


def test_team_fixtures_long_has_one_row_per_team_per_fixture():
    fixtures = pd.DataFrame(
        {
            "season": ["2099-00"] * 2,
            "event": [1, 1],
            "fixture_id": [1, 2],
            "kickoff_time": pd.to_datetime(["2099-08-01", "2099-08-02"]),
            "team_h": [1, 3],
            "team_a": [2, 4],
            "team_h_difficulty": [3, 2],
            "team_a_difficulty": [4, 5],
        }
    )
    long = _team_fixtures_long(fixtures)
    assert len(long) == 4  # 2 fixtures x 2 teams each
    team1_row = long[(long["team_id"] == 1) & (long["fixture_id"] == 1)].iloc[0]
    assert team1_row["opponent_team_id"] == 2
    assert bool(team1_row["was_home_target"]) is True
    assert team1_row["target_fdr"] == 3

    team2_row = long[(long["team_id"] == 2) & (long["fixture_id"] == 1)].iloc[0]
    assert team2_row["opponent_team_id"] == 1
    assert bool(team2_row["was_home_target"]) is False
    assert team2_row["target_fdr"] == 4


def test_team_fixtures_long_computes_rest_days_between_own_fixtures():
    fixtures = pd.DataFrame(
        {
            "season": ["2099-00"] * 2,
            "event": [1, 2],
            "fixture_id": [1, 2],
            "kickoff_time": pd.to_datetime(["2099-08-01", "2099-08-08"]),
            "team_h": [1, 1],
            "team_a": [2, 3],
            "team_h_difficulty": [3, 3],
            "team_a_difficulty": [3, 3],
        }
    )
    long = _team_fixtures_long(fixtures)
    team1_gw1 = long[(long["team_id"] == 1) & (long["gw"] == 1)].iloc[0]
    team1_gw2 = long[(long["team_id"] == 1) & (long["gw"] == 2)].iloc[0]
    assert pd.isna(team1_gw1["target_rest_days"])  # no prior fixture
    assert team1_gw2["target_rest_days"] == 7.0  # Aug 1 -> Aug 8


def test_return_gw_overrides_the_fade_but_never_gameweek_zero():
    """The availability fade assumed every unavailable player is fully fit 4
    gameweeks out. Where FPL publishes a return date we now use it — but not at
    h=0, where `chance_of_playing_next_round` is FPL's own judgement about this
    week and strictly better than a date."""
    import numpy as np

    factor0 = np.array([0.0, 0.0, 0.75, 0.0])  # gone, long-out, doubtful, unknown
    return_gw = {1: 999, 2: 6, 3: 1}  # code 4 absent: no date published
    codes = pd.Series([1, 2, 3, 4])

    def fade(h, target_gw):
        f = factor0 + (1 - factor0) * min(1.0, h / 4)
        if h > 0:
            back = codes.map(return_gw)
            f = np.where((back > target_gw).fillna(False).to_numpy(), factor0, f)
            f = np.where((back <= target_gw).fillna(False).to_numpy(), 1.0, f)
        return f

    # h=0 is untouched: the doubtful player keeps his live 75%, not a date's 1.0
    assert list(fade(0, 1)) == [0.0, 0.0, 0.75, 0.0]
    # h=4, gameweek 5: departed still out, long-term out still out, returnee back
    at_gw5 = fade(4, 5)
    assert at_gw5[0] == 0.0, "a player who left the league must not be resurrected"
    assert at_gw5[1] == 0.0, "back in GW6 means still out in GW5"
    assert at_gw5[2] == 1.0
    # no published date -> old linear fade survives, absence is not availability
    assert at_gw5[3] == 1.0
    assert fade(2, 3)[3] == pytest.approx(0.5)
