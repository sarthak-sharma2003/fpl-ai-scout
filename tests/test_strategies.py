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
    })


def _uni():
    return pd.DataFrame({"code": [1, 2], "price": [100, 50], "total_ev": [1.0, 1.0]})


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
