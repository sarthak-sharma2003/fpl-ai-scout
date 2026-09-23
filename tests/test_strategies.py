import pandas as pd

from fplscout.backtest.strategies import make_rules


def _hist():
    # code 1 starts every week; code 2 benched in GW2 then starts GW3 (the
    # decision week, which a leak-free rule must not see).
    return pd.DataFrame({
        "gw": [1, 1, 2, 2, 3, 3],
        "code": [1, 2, 1, 2, 1, 2],
        "starts": [1, 1, 1, 0, 1, 1],
        "minutes": [90, 90, 90, 0, 90, 90],
        "total_points": [2, 2, 6, 0, 2, 20],
        "selected": [10, 5, 10, 5, 10, 5],
        "transfers_balance": [1, -1, 1, -1, 1, 1],
        "team_id": [1, 2, 1, 2, 1, 2],
        "position": ["MID", "FWD"] * 3,
        "goals_scored": [1, 0, 1, 0, 0, 5],
        "xgi": [0.9, 0.1, 0.8, 0.0, 0.1, 3.0],
        "was_home": [True, False, False, True, False, True],
    })


def _uni():
    return pd.DataFrame({"code": [1, 2], "price": [100, 50], "total_ev": [1.0, 1.0],
                         "cap_ev": [5.0, 1.0], "team_id": [1, 2], "position": ["MID", "FWD"]})


def test_rules_only_see_past_gameweeks():
    rules = make_rules(_hist())
    uni = rules["started_last"](3, _uni())
    assert uni["buy_ok"].tolist() == [True, False]
    assert uni["start_ok"].tolist() == [True, False]
    assert "buy_ok" not in rules["started_last"](1, _uni())  # no history at GW1
    assert rules["nailed3"](3, _uni())["buy_ok"].tolist() == [False, False]  # only 2 GWs
    assert rules["momentum"](3, _uni())["buy_ok"].tolist() == [True, False]
    assert rules["form_ev"](3, _uni())["cap_ev"].tolist() == [2.0, 0.5]  # (2+6)/4, (2+0)/4


def test_rules_combine_with_and():
    rules = make_rules(_hist())
    uni = rules["momentum"](3, rules["template"](3, _uni()))
    assert uni["buy_ok"].tolist() == [True, False]


def test_round_two_rules(monkeypatch):
    import fplscout.backtest.strategies as st
    monkeypatch.setattr(st, "XGI_TOP_N", 1)
    monkeypatch.setattr(st, "TOP_ATTACK_TEAMS", 1)
    rules = make_rules(_hist())
    # GW3's huge xGI/goals for code 2 must not leak into the GW3 decision
    assert rules["xgi_top"](3, _uni())["buy_ok"].tolist() == [True, False]
    assert rules["top_attack_teams"](3, _uni())["buy_ok"].tolist() == [True, False]
    # home captain reads only the decision GW's venue: code 2 is home in GW3
    ranked = rules["home_captain"](3, _uni())["cap_rank"].tolist()
    assert ranked[1] > ranked[0]
