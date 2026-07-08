from __future__ import annotations

import pandas as pd
import pytest

from fplscout.decide.chip_planner import (
    ChipRecommendation,
    ChipWindow,
    bench_boost_ev,
    bgw_teams,
    chip_alert,
    chip_windows_from_bootstrap_chips,
    dgw_bgw_counts,
    evaluate_chip_windows,
    free_hit_ev,
    plan_chips,
    triple_captain_ev,
    wildcard_ev,
)
from fplscout.decide.optimizer import OptimizationResult, OptimizerInput


def test_dgw_bgw_counts_identifies_double_gameweek():
    fixtures = pd.DataFrame(
        {
            "season": ["2025-26"] * 3,
            "event": [10, 10, 10],
            "team_h": [1, 1, 3],
            "team_a": [2, 4, 4],
        }
    )
    counts = dgw_bgw_counts(fixtures)
    team1_row = counts[(counts["team_id"] == 1) & (counts["gw"] == 10)].iloc[0]
    assert team1_row["n_fixtures"] == 2
    assert team1_row["is_dgw"]
    team2_row = counts[(counts["team_id"] == 2) & (counts["gw"] == 10)].iloc[0]
    assert team2_row["n_fixtures"] == 1
    assert not team2_row["is_dgw"]


def test_bgw_teams_finds_teams_with_no_fixture():
    fixtures = pd.DataFrame(
        {"season": ["2025-26"] * 2, "event": [11, 11], "team_h": [1, 3], "team_a": [2, 4]}
    )
    result = bgw_teams(fixtures, "2025-26", 11, all_team_ids={1, 2, 3, 4, 5, 6})
    assert result == {5, 6}


def test_chip_windows_from_bootstrap_chips():
    class FakeChip:
        def __init__(self, name, start, stop):
            self.name = name
            self.start_event = start
            self.stop_event = stop

    chips = [FakeChip("wildcard", 2, 19), FakeChip("bboost", 1, 19)]
    windows = chip_windows_from_bootstrap_chips(chips)
    assert windows[0] == ChipWindow(chip="wildcard", start_gw=2, stop_gw=19)
    assert windows[1] == ChipWindow(chip="bboost", start_gw=1, stop_gw=19)


def test_bench_boost_ev_sums_all_bench_players_including_gk():
    result = OptimizationResult(
        status="Optimal", bench_order=[10, 11, 12], bench_gk=13,
    )
    ev_by_code = {10: 5.0, 11: 3.0, 12: 1.0, 13: 2.0}
    assert bench_boost_ev(result, ev_by_code) == pytest.approx(11.0)


def test_triple_captain_ev_is_the_extra_multiple_not_total():
    result = OptimizationResult(status="Optimal", captain=42)
    ev_by_code = {42: 9.0}
    assert triple_captain_ev(result, ev_by_code) == pytest.approx(9.0)


def test_triple_captain_ev_zero_when_no_captain():
    result = OptimizationResult(status="Optimal", captain=None)
    assert triple_captain_ev(result, {}) == 0.0


def test_free_hit_ev_is_the_delta():
    fh = OptimizationResult(status="Optimal", objective_value=80.0)
    normal = OptimizationResult(status="Optimal", objective_value=65.0)
    assert free_hit_ev(fh, normal) == pytest.approx(15.0)


def test_wildcard_ev_is_the_delta():
    wc = OptimizationResult(status="Optimal", objective_value=100.0)
    normal = OptimizationResult(status="Optimal", objective_value=90.0)
    assert wildcard_ev(wc, normal) == pytest.approx(10.0)


def test_plan_chips_picks_best_gw_within_window():
    windows = [ChipWindow(chip="3xc", start_gw=1, stop_gw=5)]
    ev_lookup = {
        ("3xc", 1): 5.0,
        ("3xc", 2): 12.0,  # best
        ("3xc", 3): 8.0,
        ("3xc", 6): 99.0,  # outside window, must be ignored
    }
    recs = plan_chips(windows, ev_lookup)
    assert len(recs) == 1
    assert recs[0].chip == "3xc"
    assert recs[0].gw == 2
    assert recs[0].ev == 12.0


def test_plan_chips_skips_window_with_no_candidates():
    windows = [ChipWindow(chip="wildcard", start_gw=20, stop_gw=25)]
    recs = plan_chips(windows, {("wildcard", 5): 10.0})
    assert recs == []


def test_chip_alert_fires_at_planned_gw():
    planned = ChipRecommendation(chip="3xc", gw=12, ev=14.1)
    assert chip_alert(current_gw=12, current_week_ev=1.0, planned=planned) is True


def test_chip_alert_fires_early_when_current_week_beats_planned():
    planned = ChipRecommendation(chip="3xc", gw=12, ev=14.1)
    assert chip_alert(current_gw=8, current_week_ev=20.0, planned=planned) is True


def test_chip_alert_does_not_fire_when_current_week_is_worse():
    planned = ChipRecommendation(chip="3xc", gw=12, ev=14.1)
    assert chip_alert(current_gw=8, current_week_ev=5.0, planned=planned) is False


def test_chip_alert_false_after_window_missed():
    planned = ChipRecommendation(chip="3xc", gw=12, ev=14.1)
    assert chip_alert(current_gw=15, current_week_ev=999.0, planned=planned) is False


def _player(code, position, team_id, price, ev):
    return {
        "code": code, "position": position, "team_id": team_id,
        "price": price, "total_ev": ev,
    }


def test_evaluate_chip_windows_never_recommends_outside_valid_gw():
    squad = [
        _player(1, "GKP", 1, 40, 20), _player(2, "GKP", 2, 40, 50),
        _player(11, "DEF", 3, 40, 15), _player(12, "DEF", 4, 40, 15),
        _player(13, "DEF", 5, 40, 15), _player(14, "DEF", 6, 40, 15),
        _player(15, "DEF", 7, 40, 15),
        _player(21, "MID", 8, 40, 20), _player(22, "MID", 9, 40, 20),
        _player(23, "MID", 10, 40, 20), _player(24, "MID", 11, 40, 20),
        _player(25, "MID", 12, 40, 20),
        _player(31, "FWD", 13, 40, 25), _player(32, "FWD", 14, 40, 25),
        _player(33, "FWD", 15, 40, 25),
    ]
    universe = pd.DataFrame(squad)
    current_squad = {p["code"] for p in squad}
    base_input = OptimizerInput(
        projections=universe,
        current_squad=current_squad,
        purchase_prices={p["code"]: p["price"] for p in squad},
        bank=0,
        free_transfers=1,
    )
    windows = [ChipWindow(chip="bboost", start_gw=5, stop_gw=6)]
    # only gw 5 has projections available -> gw 6 must be skipped, not crash
    projections_by_gw = {5: universe}
    ev_by_chip_gw = evaluate_chip_windows(windows, projections_by_gw, base_input)
    assert all(gw == 5 for (_, gw) in ev_by_chip_gw)

    recs = plan_chips(windows, ev_by_chip_gw)
    assert len(recs) == 1
    assert recs[0].gw == 5
