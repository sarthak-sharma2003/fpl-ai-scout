from __future__ import annotations

import pandas as pd

from fplscout.publish import _confidence, _player_card, build_rules


def test_confidence_decays_smoothly_not_floored_at_zero():
    # Real FPL quantile spreads routinely exceed EV itself (ev=6, q90-q10=10 is
    # a normal starter) — the naive "1 - spread" formula floored at 0 for every
    # player in practice; this checks the replacement formula differentiates.
    tight = pd.DataFrame(
        {"code": [1], "ev_points": [8.0], "q10_points": [6.0], "q90_points": [10.0]}
    )
    wide = pd.DataFrame(
        {"code": [1], "ev_points": [8.0], "q10_points": [1.0], "q90_points": [16.0]}
    )
    c_tight = _confidence(tight, {1})
    c_wide = _confidence(wide, {1})
    assert c_tight > c_wide
    assert 0 < c_wide < 100
    assert 0 < c_tight < 100


def test_confidence_empty_xi_is_zero():
    ref = pd.DataFrame({"code": [1], "ev_points": [8.0], "q10_points": [6.0], "q90_points": [10.0]})
    assert _confidence(ref, set()) == 0.0


def test_player_card_rounds_price_to_tenths_of_a_million():
    row = pd.Series(
        {"code": 123, "web_name": "Salah", "team_short": "LIV", "position": "MID",
         "price": 140, "ev_points": 6.2}
    )
    card = _player_card(row)
    assert card == {
        "code": 123, "name": "Salah", "team": "LIV", "position": "MID",
        "price": 14.0, "ev": 6.2,
    }


def test_player_card_handles_missing_ev(tmp_path):
    row = pd.Series(
        {"code": 1, "web_name": "X", "team_short": "AAA", "position": "GKP",
         "price": 40, "ev_points": float("nan")}
    )
    card = _player_card(row)
    assert card["ev"] is None


def test_build_rules_reads_yaml_seed(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "rules:\n"
        "  - id: r1\n"
        "    title: Title\n"
        "    body: \"  Some body text.  \"\n"
        "    enabled: false\n"
    )
    rules = build_rules(rules_path)
    assert rules == [{"id": "r1", "title": "Title", "body": "Some body text.", "enabled": False}]


def test_build_rules_empty_file(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text("rules: []\n")
    assert build_rules(rules_path) == []
