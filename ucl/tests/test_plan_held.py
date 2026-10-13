"""Planning off a squad you already own (the normal in-season case)."""
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
