"""Planning off a squad you already own (the normal in-season case)."""
from dataclasses import replace

from uclscout.feeds import Player
from uclscout.optimize import SQUAD_SHAPE
from uclscout.plan import plan_horizon
from uclscout.project import Minutes, MinutesSource, Projection


def _player(i, pos, val, team):
    return Player(id=i, name=f"P{i}", team_id=team, team=f"T{team}", pos=pos,
                  value=val, status="", selected_pct=1, total_points=0,
                  minutes=900, goals=0, assists=0, clean_sheets=0, saves=0,
                  recoveries=0, motm=0, as_of_matchday=2)


def _horizon(players, mds, zero=()):
    out = {}
    for md in mds:
        out[md] = [
            Projection(p, Minutes(0.0 if p.id in zero else 85.0,
                                  MinutesSource.OBSERVED_UCL),
                       5.0, 1.0, 0.0 if p.id in zero else 3.0 + (p.id % 7) * 0.4,
                       1, True, "2026-10-13")
            for p in players
        ]
    return out


def _pool():
    players, pid = [], 0
    for pos, n in (("GK", 5), ("DEF", 14), ("MID", 14), ("FWD", 9)):
        for k in range(n):
            pid += 1
            players.append(_player(pid, pos, 4.5 + (k % 4) * 0.5, team=pid % 12))
    return players


def _held(players):
    held = []
    for pos, n in SQUAD_SHAPE.items():
        held += [p.id for p in players if p.pos == pos][:n]
    return set(held)


def test_held_squad_moves_are_paired_and_within_free_transfers():
    players = _pool()
    held = _held(players)
    injured = next(i for i in sorted(held)
                   if next(p for p in players if p.id == i).pos == "MID")
    plan = plan_horizon(_horizon(players, [2, 3], zero={injured}),
                        current_squad=held, budget=200.0, time_limit=20)
    first = plan.actions[0]
    assert plan.opening_squad is None
    assert len(first.squad) == 15
    assert len(first.buys) == len(first.sells)
    assert {s.player.id for s in first.sells} <= held
    # the zero-projection owned player never starts
    assert injured not in {p.player.id for p in first.xi}
    assert len(first.xi) == 11 and first.captain in first.xi


def test_no_free_squad_slots():
    """Owning 15 means the plan can only change the squad by selling."""
    players = _pool()
    held = _held(players)
    plan = plan_horizon(_horizon(players, [2]), current_squad=held,
                        budget=200.0, time_limit=20)
    a = plan.actions[0]
    after = (held - {s.player.id for s in a.sells}) | {b.player.id for b in a.buys}
    assert after == {p.player.id for p in a.squad}


def test_three_forced_moves_take_a_hit_instead_of_infeasible():
    """Three owned players can never start; two free transfers. The plan must
    pay a hit (or wildcard) -- before the carry fix it was 'Infeasible'."""
    players = _pool()
    held = _held(players)
    defs = sorted(i for i in held if next(p for p in players if p.id == i).pos == "DEF")
    dead = set(defs[:3])        # only 2 playable DEF left; XI needs 3
    plan = plan_horizon(_horizon(players, [2, 3, 4], zero=dead),
                        current_squad=held, budget=200.0, time_limit=30)
    first = plan.actions[0]
    assert len(first.xi) == 11
    assert len(first.buys) >= 1
    for a in plan.actions:
        assert len(a.squad) == 15
        assert a.chip or len(a.buys) <= a.free_available + a.hits


def test_hit_before_last_matchday_is_allowed():
    players = _pool()
    held = _held(players)
    # every owned DEF and MID is dead: far more than 2 moves are needed at MD2
    dead = {i for i in held
            if next(p for p in players if p.id == i).pos in ("DEF", "MID")}
    plan = plan_horizon(_horizon(players, [2, 3, 4, 5], zero=dead),
                        current_squad=held, budget=200.0, time_limit=30,
                        allow_wildcard=False)
    first = plan.actions[0]
    assert first.hits >= 1 and len(first.xi) == 11


def _boost(h, team, mds, extra):
    """Make one club's players worth `extra` more on the given matchdays."""
    for md in mds:
        h[md] = [p if p.player.team_id != team else
                 replace(p, points=p.points + extra) for p in h[md]]


def test_budget_and_club_cap_are_per_matchday():
    """A tight budget at MD8 and a looser one at MD9 (EUR100m -> 105m); 3 per
    club in the league phase, 4 at the play-offs. Each matchday obeys its own.
    Club 1 (four players, one per position) is far better than the rest."""
    players = [replace(p, value=4.5) if p.team_id == 1 else p for p in _pool()]
    h = _horizon(players, [8, 9])
    _boost(h, 1, [8, 9], 6.0)
    plan = plan_horizon(h, budget={8: 75.0, 9: 80.0},
                        max_per_club={8: 3, 9: 4}, time_limit=30)
    md8, md9 = plan.actions
    for a, cap, budget in ((md8, 3, 75.0), (md9, 4, 80.0)):
        clubs = [p.player.team_id for p in a.squad]
        assert clubs.count(1) == cap, (a.matchday, clubs.count(1))
        assert sum(p.player.value for p in a.squad) <= budget + 1e-6


def test_club_with_no_fixture_is_owned_at_zero_not_dropped():
    """A club with no MD12 fixture is absent from MD12's projections. It must
    stay ownable -- here it is the best buy for MD13 -- and score 0 at MD12."""
    players = _pool()
    h = _horizon(players, [12, 13])
    h[12] = [p for p in h[12] if p.player.team_id != 1]
    _boost(h, 1, [13], 6.0)
    plan = plan_horizon(h, budget=200.0, max_per_club={12: 4, 13: 5},
                        time_limit=30)
    md12, md13 = plan.actions
    assert sum(p.player.team_id == 1 for p in md13.squad) >= 3
    assert all(p.player.team_id != 1 or p.points == 0 for p in md12.squad)
    assert len(md12.xi) == 11 and md13.free_available == 5


def test_limitless_is_checked_on_every_matchday_and_held_below_its_bar():
    from uclscout.plan import best_limitless, limitless_gains
    players = _pool()
    held = _held(players)
    mds = [2, 3, 4]
    h = _horizon(players, mds)
    gains = limitless_gains(h, {md: held for md in mds})
    # every legal matchday is evaluated, gains are non-negative
    assert sorted(gains) == mds
    assert all(g >= -1e-6 for g, _ in gains.values())
    top = best_limitless(gains, min_gain=float("-inf"))
    assert top is not None and top[0] in mds
    # the advice is to hold whenever no matchday clears the bar
    assert best_limitless(gains, min_gain=top[1] + 1.0) is None
    # equal gains: the nearer matchday wins (later ones are discounted)
    flat = {md: (5.0, None) for md in mds}
    assert best_limitless(flat, min_gain=0.0) == (2, 5.0)
