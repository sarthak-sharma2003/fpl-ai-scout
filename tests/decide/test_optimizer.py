"""Golden-case tests — plan §Phase4 DoD: hand-built universes where the optimal
squad is known by hand; hit-taking threshold; formation flex."""

from __future__ import annotations

import pandas as pd
import pytest

from fplscout.decide.optimizer import (
    DEFAULT_HIT_COST,
    OptimizerInput,
    optimize,
    top_alternative_moves,
)


def _player(code, position, team_id, price, ev):
    return {"code": code, "position": position, "team_id": team_id, "price": price, "total_ev": ev}


@pytest.fixture
def draft_universe() -> pd.DataFrame:
    """20 players, spread across enough clubs that the 3-per-club cap never binds.
    Each position's players are strictly ranked by (price, ev) so "best XI within
    budget" is checkable by inspection: within a position, higher price always
    means higher ev (no dominated options), so the optimal squad is exactly "the
    cheapest combination that can't be improved by swapping in a higher tier"."""
    rows = [
        # GKP: pick 2 of 3
        _player(1, "GKP", 1, 40, 20),
        _player(2, "GKP", 2, 50, 30),
        _player(3, "GKP", 3, 45, 22),
        # DEF: pick 5 of 6
        _player(11, "DEF", 4, 40, 15),
        _player(12, "DEF", 5, 42, 17),
        _player(13, "DEF", 6, 45, 19),
        _player(14, "DEF", 7, 50, 23),
        _player(15, "DEF", 8, 55, 26),
        _player(16, "DEF", 9, 60, 29),
        # MID: pick 5 of 7
        _player(21, "MID", 10, 45, 20),
        _player(22, "MID", 11, 50, 25),
        _player(23, "MID", 12, 55, 29),
        _player(24, "MID", 13, 60, 33),
        _player(25, "MID", 14, 65, 37),
        _player(26, "MID", 15, 70, 40),
        _player(27, "MID", 16, 75, 43),
        # FWD: pick 3 of 4
        _player(31, "FWD", 17, 50, 20),
        _player(32, "FWD", 18, 60, 28),
        _player(33, "FWD", 19, 70, 35),
        _player(34, "FWD", 20, 80, 41),
    ]
    return pd.DataFrame(rows)


def test_initial_draft_within_budget_maximizes_ev(draft_universe):
    """No current squad (fresh draft), generous budget, wildcard mode (no hit
    cost) — the optimizer should pick a full legal 15 that maximizes EV subject
    to the budget, and never overspend."""
    inp = OptimizerInput(
        projections=draft_universe,
        current_squad=set(),
        purchase_prices={},
        bank=1000,  # £100.0m
        free_transfers=0,
        chip_mode="wildcard",
    )
    result = optimize(inp)
    assert result.status == "Optimal"
    assert len(result.squad) == 15
    assert len(result.starting_xi) == 11

    positions = draft_universe.set_index("code")["position"]
    counts = positions.loc[list(result.squad)].value_counts()
    assert counts["GKP"] == 2
    assert counts["DEF"] == 5
    assert counts["MID"] == 5
    assert counts["FWD"] == 3

    prices = draft_universe.set_index("code")["price"]
    total_spend = prices.loc[list(result.squad)].sum()
    assert total_spend <= 1000

    # every position's ev is strictly increasing with price in this universe, so
    # the optimal squad under a non-binding budget must include the single
    # highest-ev player at each position (e.g. the best GKP, best FWD).
    assert 2 in result.squad  # best GKP (ev=30)
    assert 34 in result.squad  # best FWD (ev=41)
    assert 27 in result.squad  # best MID (ev=43)
    assert 16 in result.squad  # best DEF (ev=29)


def test_budget_constraint_forces_cheaper_squad(draft_universe):
    """Same universe, budget too tight for the top tier at every position —
    optimizer must still return a feasible, legal squad within budget."""
    # cheapest possible legal squad in this universe costs 772 (2 cheapest GKP +
    # 5 cheapest DEF + 5 cheapest MID + 3 cheapest FWD); the priciest all-top-tier
    # squad costs ~955. 850 sits strictly between the two, so it's tight without
    # being infeasible.
    inp = OptimizerInput(
        projections=draft_universe,
        current_squad=set(),
        purchase_prices={},
        bank=850,
        free_transfers=0,
        chip_mode="wildcard",
    )
    result = optimize(inp)
    assert result.status == "Optimal"
    prices = draft_universe.set_index("code")["price"]
    assert prices.loc[list(result.squad)].sum() <= 850
    assert len(result.squad) == 15


def test_captain_and_vice_are_distinct_and_in_starting_xi(draft_universe):
    inp = OptimizerInput(
        projections=draft_universe,
        current_squad=set(),
        purchase_prices={},
        bank=1000,
        free_transfers=0,
        chip_mode="wildcard",
    )
    result = optimize(inp)
    assert result.captain != result.vice_captain
    assert result.captain in result.starting_xi
    assert result.vice_captain in result.starting_xi
    # captain should be the highest-ev player in the XI
    ev = draft_universe.set_index("code")["total_ev"]
    best_in_xi = ev.loc[list(result.starting_xi)].idxmax()
    assert result.captain == best_in_xi


def _hit_test_squad():
    """Includes a dominant, unambiguous captain (MID #21, ev=100) far above
    every other player, so captaincy never shifts when the FWD slot changes —
    without this anchor, swapping in a marginally-better forward can *also*
    become the new captain, and the 2x captain multiplier compounds the base ev
    gain enough to justify a hit that the base gain alone wouldn't (this was a
    real bug in an earlier version of this test, not a hypothetical: a 3-point
    base gain plus a captaincy reassignment was clearing the 4-point hit cost)."""
    return [
        _player(1, "GKP", 1, 40, 20), _player(2, "GKP", 2, 40, 15),
        _player(11, "DEF", 3, 40, 15), _player(12, "DEF", 4, 40, 15),
        _player(13, "DEF", 5, 40, 15), _player(14, "DEF", 6, 40, 15),
        _player(15, "DEF", 7, 40, 15),
        _player(21, "MID", 8, 40, 100),  # dominant captain anchor
        _player(22, "MID", 9, 40, 20),
        _player(23, "MID", 10, 40, 20), _player(24, "MID", 11, 40, 20),
        _player(25, "MID", 12, 40, 20),
        _player(31, "FWD", 13, 40, 25), _player(32, "FWD", 14, 40, 25),
        _player(33, "FWD", 15, 40, 25),
    ]


def test_hit_not_taken_when_ev_gain_below_hit_cost():
    """One swap available with ev gain of 3 (< hit_cost=4) — optimizer must NOT
    take a hit for it when free_transfers=0."""
    squad15 = _hit_test_squad()
    # candidate replacement for player 33 (ev=25): ev=28, gain of 3
    candidate = _player(99, "FWD", 16, 40, 28)
    universe = pd.DataFrame(squad15 + [candidate])
    current_squad = {p["code"] for p in squad15}

    inp = OptimizerInput(
        projections=universe,
        current_squad=current_squad,
        purchase_prices={p["code"]: p["price"] for p in squad15},
        bank=0,
        free_transfers=0,
        chip_mode=None,
    )
    result = optimize(inp)
    assert result.status == "Optimal"
    assert result.captain == 21  # anchor unaffected by the FWD swap decision
    assert result.hits == 0
    assert 99 not in result.squad


def test_hit_taken_when_ev_gain_clearly_exceeds_hit_cost():
    """Same setup, but the candidate's ev gain is 6 (> hit_cost=4) — optimizer
    should take the -4 hit for a net +2."""
    squad15 = _hit_test_squad()
    candidate = _player(99, "FWD", 16, 40, 31)  # gain of 6 over player 33
    universe = pd.DataFrame(squad15 + [candidate])
    current_squad = {p["code"] for p in squad15}

    inp = OptimizerInput(
        projections=universe,
        current_squad=current_squad,
        purchase_prices={p["code"]: p["price"] for p in squad15},
        bank=0,
        free_transfers=0,
        chip_mode=None,
    )
    result = optimize(inp)
    assert result.status == "Optimal"
    assert result.captain == 21
    assert result.hits == 1
    assert 99 in result.squad
    # players 31/32/33 all have identical ev=25 in this fixture, so any one of
    # them is an equally optimal swap-out target — assert one was dropped, not
    # a specific one.
    assert len(result.transfers_out & {31, 32, 33}) == 1


def test_free_transfer_used_without_hit_when_ev_gain_is_small_but_positive():
    """Same small ev-gain-of-3 candidate, but free_transfers=1 this time —
    should swap in the better player with zero hit cost."""
    squad15 = _hit_test_squad()
    candidate = _player(99, "FWD", 16, 40, 28)
    universe = pd.DataFrame(squad15 + [candidate])
    current_squad = {p["code"] for p in squad15}

    inp = OptimizerInput(
        projections=universe,
        current_squad=current_squad,
        purchase_prices={p["code"]: p["price"] for p in squad15},
        bank=0,
        free_transfers=1,
        chip_mode=None,
    )
    result = optimize(inp)
    assert result.status == "Optimal"
    assert result.hits == 0
    assert 99 in result.squad


def test_transfer_penalty_blocks_marginal_free_transfer():
    """Same ev-gain-of-3 candidate and a free transfer available — but with
    transfer_penalty=4 the banked FT's option value outweighs the gain, so the
    optimizer should hold."""
    squad15 = _hit_test_squad()
    candidate = _player(99, "FWD", 16, 40, 28)
    universe = pd.DataFrame(squad15 + [candidate])
    current_squad = {p["code"] for p in squad15}

    inp = OptimizerInput(
        projections=universe,
        current_squad=current_squad,
        purchase_prices={p["code"]: p["price"] for p in squad15},
        bank=0,
        free_transfers=1,
        chip_mode=None,
        transfer_penalty=4.0,
    )
    result = optimize(inp)
    assert result.status == "Optimal"
    assert 99 not in result.squad
    assert result.transfers_in == set()


def test_max_hits_caps_paid_transfers():
    """Two candidates each with ev gain 10 (>> hit_cost) and zero free
    transfers: uncapped the optimizer takes 2 hits, with max_hits=1 it may take
    only one."""
    squad15 = _hit_test_squad()
    candidates = [_player(98, "FWD", 16, 40, 35), _player(99, "FWD", 17, 40, 35)]
    universe = pd.DataFrame(squad15 + candidates)
    current_squad = {p["code"] for p in squad15}

    base = dict(
        projections=universe,
        current_squad=current_squad,
        purchase_prices={p["code"]: p["price"] for p in squad15},
        bank=0,
        free_transfers=0,
        chip_mode=None,
    )
    uncapped = optimize(OptimizerInput(**base))
    assert uncapped.status == "Optimal"
    assert uncapped.hits == 2

    capped = optimize(OptimizerInput(**base, max_hits=1))
    assert capped.status == "Optimal"
    assert capped.hits == 1
    assert len(capped.transfers_in & {98, 99}) == 1


def test_formation_flexes_when_fifth_midfielder_outprojects_third_forward():
    """A fixed 15-man squad (2 GKP/5 DEF/5 MID/3 FWD) where the 5th midfielder
    has higher ev than the weakest forward — the XI should start all 5
    midfielders and only 2 forwards (a 3-5-2-shaped XI), not force a 4-4-2/
    4-3-3 default. No transfers needed (current_squad == full legal squad)."""
    squad = [
        _player(1, "GKP", 1, 50, 30), _player(2, "GKP", 2, 40, 20),
        _player(11, "DEF", 3, 50, 20), _player(12, "DEF", 4, 50, 20),
        _player(13, "DEF", 5, 50, 20), _player(14, "DEF", 6, 45, 15),
        _player(15, "DEF", 7, 45, 15),
        _player(21, "MID", 8, 60, 30), _player(22, "MID", 9, 60, 30),
        _player(23, "MID", 10, 60, 30), _player(24, "MID", 11, 60, 30),
        _player(25, "MID", 12, 60, 30),  # 5th mid, ev=30 — outprojects FWD #3
        _player(31, "FWD", 13, 60, 32), _player(32, "FWD", 14, 60, 32),
        _player(33, "FWD", 15, 55, 18),  # weakest forward, ev=18 < mid #5's 30
    ]
    universe = pd.DataFrame(squad)
    current_squad = {p["code"] for p in squad}

    inp = OptimizerInput(
        projections=universe,
        current_squad=current_squad,
        purchase_prices={p["code"]: p["price"] for p in squad},
        bank=0,
        free_transfers=0,
        chip_mode=None,
    )
    result = optimize(inp)
    assert result.status == "Optimal"
    assert result.hits == 0

    positions = universe.set_index("code")["position"]
    xi_positions = positions.loc[list(result.starting_xi)].value_counts()
    assert xi_positions["MID"] == 5
    assert xi_positions["FWD"] == 2
    assert 33 not in result.starting_xi  # weakest forward benched
    assert 25 in result.starting_xi  # 5th mid starts instead


def test_bench_boost_values_full_bench_in_objective():
    """With bench_boost, bench players should contribute their full ev to the
    objective rather than the downweighted autosub-priority weights."""
    squad = [
        _player(1, "GKP", 1, 40, 10), _player(2, "GKP", 2, 40, 50),  # backup GK huge ev
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
    base_inp = dict(
        projections=universe,
        current_squad=current_squad,
        purchase_prices={p["code"]: p["price"] for p in squad},
        bank=0,
        free_transfers=0,
    )
    normal = optimize(OptimizerInput(**base_inp, chip_mode=None))
    boosted = optimize(OptimizerInput(**base_inp, chip_mode="bench_boost"))
    assert normal.status == "Optimal"
    assert boosted.status == "Optimal"
    # backup GK (ev=50) sitting on the bench should push boosted's objective
    # well above normal's, since bench_boost values it at full weight instead
    # of ~0.
    assert boosted.objective_value > normal.objective_value + 20


def test_triple_captain_triples_captain_contribution():
    squad = [
        _player(1, "GKP", 1, 40, 20), _player(2, "GKP", 2, 40, 10),
        _player(11, "DEF", 3, 40, 15), _player(12, "DEF", 4, 40, 15),
        _player(13, "DEF", 5, 40, 15), _player(14, "DEF", 6, 40, 15),
        _player(15, "DEF", 7, 40, 15),
        _player(21, "MID", 8, 40, 20), _player(22, "MID", 9, 40, 20),
        _player(23, "MID", 10, 40, 20), _player(24, "MID", 11, 40, 20),
        _player(25, "MID", 12, 40, 20),
        _player(31, "FWD", 13, 40, 50),  # standout captain pick
        _player(32, "FWD", 14, 40, 25), _player(33, "FWD", 15, 40, 25),
    ]
    universe = pd.DataFrame(squad)
    current_squad = {p["code"] for p in squad}
    base_inp = dict(
        projections=universe,
        current_squad=current_squad,
        purchase_prices={p["code"]: p["price"] for p in squad},
        bank=0,
        free_transfers=0,
    )
    normal = optimize(OptimizerInput(**base_inp, chip_mode=None))
    triple = optimize(OptimizerInput(**base_inp, chip_mode="triple_captain"))
    assert normal.captain == 31
    assert triple.captain == 31
    # normal: +1x captain bonus (50); triple: +2x captain bonus (100) -> +50 more
    assert triple.objective_value - normal.objective_value == pytest.approx(50, abs=0.01)


def test_max_per_club_enforced(draft_universe):
    # stack 4 high-ev players onto the same club to force the cap to bind
    stacked = draft_universe.copy()
    stacked.loc[stacked["code"].isin([2, 16, 27, 34]), "team_id"] = 99
    inp = OptimizerInput(
        projections=stacked,
        current_squad=set(),
        purchase_prices={},
        bank=1000,
        free_transfers=0,
        chip_mode="wildcard",
        max_per_club=3,
    )
    result = optimize(inp)
    assert result.status == "Optimal"
    team_of = stacked.set_index("code")["team_id"]
    club_counts = team_of.loc[list(result.squad)].value_counts()
    assert club_counts.max() <= 3


def test_top_alternative_moves_ranked_by_net_ev_and_same_position():
    squad15 = _hit_test_squad()
    weak_candidate = _player(97, "FWD", 16, 40, 26)  # gain of 1
    strong_candidate = _player(98, "FWD", 17, 40, 40)  # gain of 15
    universe = pd.DataFrame(squad15 + [weak_candidate, strong_candidate])
    current_squad = {p["code"] for p in squad15}
    purchase_prices = {p["code"]: p["price"] for p in squad15}

    moves = top_alternative_moves(
        projections=universe,
        current_squad=current_squad,
        purchase_prices=purchase_prices,
        bank=0,
        free_transfers=1,
        n=5,
    )
    assert len(moves) <= 5
    assert all(m.position == m.position for m in moves)  # every move is same-position
    assert moves[0].in_code == 98  # best swap ranked first
    assert moves[0].net_ev == pytest.approx(15.0)
    assert moves[0].would_need_hit is False  # 1 free transfer available


def test_top_alternative_moves_flags_hit_when_no_free_transfers():
    squad15 = _hit_test_squad()
    candidate = _player(98, "FWD", 17, 40, 40)
    universe = pd.DataFrame(squad15 + [candidate])
    current_squad = {p["code"] for p in squad15}
    purchase_prices = {p["code"]: p["price"] for p in squad15}

    moves = top_alternative_moves(
        projections=universe,
        current_squad=current_squad,
        purchase_prices=purchase_prices,
        bank=0,
        free_transfers=0,
        n=5,
    )
    assert moves[0].would_need_hit is True
    assert moves[0].net_ev == pytest.approx(15.0 - DEFAULT_HIT_COST)
