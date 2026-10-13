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


def test_plan_chips_one_chip_per_gw_maximising_total():
    # The live GW6 bug: every chip's best week was GW8. FH keeps GW8 (biggest
    # loss if moved), WC takes its runner-up, TC its runner-up.
    windows = [
        ChipWindow(chip="3xc", start_gw=1, stop_gw=19),
        ChipWindow(chip="freehit", start_gw=2, stop_gw=19),
        ChipWindow(chip="wildcard", start_gw=2, stop_gw=19),
    ]
    ev_lookup = {
        ("3xc", 8): 7.6, ("3xc", 9): 7.0,
        ("freehit", 8): 20.5, ("freehit", 9): 10.0,
        ("wildcard", 8): 12.0, ("wildcard", 10): 11.0,
    }
    recs = {r.chip: r.gw for r in plan_chips(windows, ev_lookup)}
    assert recs == {"freehit": 8, "3xc": 9, "wildcard": 10}


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


def _universe(owned_ev: float, pool_ev: float) -> tuple[pd.DataFrame, set[int]]:
    """15 owned players at `owned_ev` and 15 more at `pool_ev`, all one price,
    every player at his own club."""
    rows, owned, code = [], set(), 0
    for ev, own in ((owned_ev, True), (pool_ev, False)):
        for pos, n in (("GKP", 2), ("DEF", 5), ("MID", 5), ("FWD", 3)):
            for _ in range(n):
                code += 1
                rows.append(_player(code, pos, code, 40, ev))
                if own:
                    owned.add(code)
    return pd.DataFrame(rows), owned


def _wc_input(owned: set[int]) -> OptimizerInput:
    # the live decision's transfer rules (simulator.DECISION_HIT_COST etc.):
    # with free hits the no-wildcard path would rebuild in week one anyway
    return OptimizerInput(
        projections=None, current_squad=owned,
        purchase_prices=dict.fromkeys(owned, 40), bank=0, free_transfers=1,
        hit_cost=6, transfer_penalty=1.5, max_hits=1,
    )


def test_simulated_wildcard_beats_one_transfer_a_week_on_a_broken_squad():
    from fplscout.decide.chip_planner import simulate_wildcard_gains

    universe, owned = _universe(owned_ev=1.0, pool_ev=5.0)
    frames = {gw: universe for gw in (7, 8, 9)}
    gains = simulate_wildcard_gains(frames, _wc_input(owned), decay=1.0)
    assert set(gains) == {7, 8, 9}
    # rebuilding now beats rebuilding later, which beats never
    assert gains[7] > gains[8] > gains[9] > 0
    # the no-wildcard path still upgrades a player a week: the wildcard's
    # week-one edge is the 14 players it fixes early, not all 15 x 3 weeks
    assert gains[7] < 15 * 4.0 * 3


def test_simulated_wildcard_is_worthless_on_the_best_squad():
    from fplscout.decide.chip_planner import simulate_wildcard_gains

    universe, owned = _universe(owned_ev=5.0, pool_ev=1.0)
    gains = simulate_wildcard_gains({7: universe, 8: universe}, _wc_input(owned), decay=1.0)
    assert all(abs(g) < 1e-6 for g in gains.values())


def test_chip_verdicts_play_only_when_planned_now_and_over_the_bar():
    from fplscout.decide.chip_planner import chip_verdicts

    windows = [ChipWindow("3xc", 1, 19), ChipWindow("bboost", 1, 19),
               ChipWindow("3xc", 20, 38)]
    ev = {("3xc", 7): 9.0, ("3xc", 8): 5.0, ("bboost", 7): 10.0, ("bboost", 9): 16.0}
    plan = plan_chips(windows[:2], ev)
    v = chip_verdicts(7, windows, ev, plan, bars={"3xc": 8.0, "bboost": 15.0})
    assert v[("3xc", 1, 19)]["advice"] == "play"
    assert v[("bboost", 1, 19)]["advice"] == "planned"
    assert v[("bboost", 1, 19)]["planned_gw"] == 9
    assert v[("3xc", 20, 38)]["advice"] == "not_yet"
    # below the bar this week: hold, whatever the plan says
    v = chip_verdicts(7, windows, ev, plan, bars={"3xc": 10.0, "bboost": 15.0})
    assert v[("3xc", 1, 19)]["advice"] == "hold"


def test_chip_bars_fall_as_the_window_runs_out():
    from fplscout.decide.chip_planner import chip_verdicts

    windows = [ChipWindow("3xc", 1, 19), ChipWindow("bboost", 1, 19)]
    ev = {("3xc", 18): 3.0, ("bboost", 19): 4.0}
    plan = plan_chips(windows, ev)
    bars = {"3xc": 8.0, "bboost": 15.0}
    # two chips, two weeks left: no spare week, so no bar -- play them
    v = chip_verdicts(18, windows, ev, plan, bars=bars)
    assert v[("3xc", 1, 19)]["bar"] == 0.0
    assert v[("3xc", 1, 19)]["advice"] == "play"
    assert v[("bboost", 1, 19)]["advice"] == "planned"
    # plenty of weeks left: the full bar applies
    assert chip_verdicts(7, windows, ev, plan, bars=bars)[("3xc", 1, 19)]["bar"] == 8.0
