from __future__ import annotations

import pandas as pd

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
