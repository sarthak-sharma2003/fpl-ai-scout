from __future__ import annotations

import pandas as pd

from fplscout.models.team_goals import fit


def _make_fixtures():
    """Team A (code 1) is clearly stronger than Team B (code 2) and Team C (code 3):
    scores more, concedes less, across many repeated meetings so the MLE has a
    real signal to converge on. Home teams also score one extra goal on average
    (4-0 at home vs. 3-0 away for the same matchup) so there's genuine home-
    advantage signal for the model to separate from raw team strength — with
    identical home/away scorelines the home-advantage parameter is unidentifiable
    from this data, which was the bug in an earlier version of this fixture."""
    rows = []
    fid = 0
    kickoff = pd.Timestamp("2023-01-01")
    matchups = [(1, 2, 4, 0), (2, 1, 0, 3), (1, 3, 3, 0), (3, 1, 0, 2), (2, 3, 2, 1)]
    for _ in range(15):
        for team_h, team_a, gh, ga in matchups:
            fid += 1
            rows.append(
                {
                    "season": "2023-24",
                    "fixture_id": fid,
                    "kickoff_time": kickoff,
                    "team_h": team_h,
                    "team_a": team_a,
                    "team_h_score": gh,
                    "team_a_score": ga,
                }
            )
            kickoff += pd.Timedelta(days=3)
    return pd.DataFrame(rows)


def _make_teams():
    # team_id == code here for simplicity (single-season synthetic data); team_id 4
    # (code 4) never appears in fixtures -> simulates a promoted team.
    return pd.DataFrame(
        {
            "season": ["2023-24"] * 4,
            "team_id": [1, 2, 3, 4],
            "code": [1, 2, 3, 4],
        }
    )


def test_stronger_team_has_higher_attack_and_better_defense():
    model = fit(_make_fixtures(), _make_teams())
    assert model.attack[1] > model.attack[2]
    assert model.attack[1] > model.attack[3]
    # lower defense param = fewer goals conceded = better defense
    assert model.defense[1] < model.defense[2]


def test_expected_goals_favor_stronger_home_team():
    model = fit(_make_fixtures(), _make_teams())
    lam_strong_home, mu_weak_away = model.expected_goals(1, 2)
    lam_weak_home, mu_strong_away = model.expected_goals(2, 1)
    assert lam_strong_home > mu_strong_away
    assert lam_strong_home > lam_weak_home


def test_promoted_team_uses_fallback_not_league_average():
    model = fit(_make_fixtures(), _make_teams())
    assert 4 not in model.attack
    lam, mu = model.expected_goals(1, 4)  # strong team at home vs "promoted" team 4
    # fallback should make team 4 concede a lot to the strong team
    assert lam > 1.5


def test_clean_sheet_prob_in_valid_range():
    model = fit(_make_fixtures(), _make_teams())
    cs_home, cs_away = model.clean_sheet_prob(1, 2)
    assert 0 <= cs_home <= 1
    assert 0 <= cs_away <= 1
