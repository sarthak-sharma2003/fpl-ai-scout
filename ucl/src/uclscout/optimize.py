"""Single-matchday squad selection as a MILP.

Picks 15, an XI, and a captain under UCL's real constraints. Two rules here are
not in the rulebook and exist because of specific failures:

  * A player whose expected minutes are IMPUTED cannot captain, and cannot take
    an XI place ahead of an equally-projected player with observed minutes.
    (See project.py -- this is the Gordon guard.)
  * `min_start` gates the XI on expected minutes outright. Early-season minutes
    uncertainty dominates EV error, so it is a constraint, not an objective term.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pulp

from .project import Projection

SQUAD_SHAPE = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
XI_MIN = {"GK": 1, "DEF": 3, "MID": 2, "FWD": 1}
XI_MAX = {"GK": 1, "DEF": 5, "MID": 5, "FWD": 3}
SQUAD_SIZE, XI_SIZE = 15, 11

BENCH_WEIGHT = 0.15      # bench matters a little: manual subs are real here
IMPUTED_PENALTY = 0.85   # discount projections built on guessed minutes


@dataclass
class Squad:
    picks: list[Projection]
    xi: list[Projection]
    captain: Projection
    cost: float
    xi_points: float
    min_start_used: float = 0.0
    note: str = ""

    @property
    def bench(self) -> list[Projection]:
        ids = {p.player.id for p in self.xi}
        return [p for p in self.picks if p.player.id not in ids]

    @property
    def total_points(self) -> float:
        """XI plus the captain's doubled contribution."""
        return self.xi_points + self.captain.points

    def by_day(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.xi:
            out[p.kickoff_day or "?"] = out.get(p.kickoff_day or "?", 0) + 1
        return dict(sorted(out.items()))


def _effective(p: Projection) -> float:
    """Projection discounted when it rests on imputed minutes."""
    return p.points * (1.0 if p.minutes.trusted else IMPUTED_PENALTY)


def _feasible_gate(P: list[Projection], min_start: float) -> tuple[float, str]:
    """The minutes gate, lowered only as far as a legal XI requires.

    A guard that can make the problem unsolvable is worse than no guard. After
    UEFA's MD1 stat reset nothing cleared 70 minutes, so zero keepers and zero
    defenders were eligible and the solver returned Infeasible (docs/GAPS.md
    P0). Relax in steps and SAY SO, rather than dropping the rule silently.
    """
    for gate in (min_start, 60.0, 45.0, 30.0, 0.0):
        if gate > min_start:
            continue
        pool = [p for p in P if p.minutes.expected >= gate]
        if len(pool) < XI_SIZE:
            continue
        if all(sum(1 for p in pool if p.player.pos == pos) >= n
               for pos, n in XI_MIN.items()):
            if gate == min_start:
                return gate, ""
            return gate, (f"minutes gate relaxed {min_start:.0f} -> {gate:.0f} "
                          "min: too few players clear it for a legal XI")
    return 0.0, f"minutes gate dropped from {min_start:.0f}: no XI clears it"


def optimize(
    projections: list[Projection],
    *,
    budget: float = 100.0,
    max_per_club: int = 3,
    min_start: float = 70.0,
    locked: set[int] | None = None,
    banned: set[int] | None = None,
    unlimited_budget: bool = False,
) -> Squad:
    """Best legal 15 / XI / captain for one matchday.

    `unlimited_budget` models the Limitless chip.
    """
    P = [p for p in projections if p.player.available]
    if len(P) < SQUAD_SIZE:
        raise ValueError(f"only {len(P)} available players")
    locked, banned = locked or set(), banned or set()
    idx = range(len(P))

    min_start, gate_note = _feasible_gate(P, min_start)
    # The Gordon guard bars imputed minutes from the armband, but if NOTHING
    # has observed minutes it bars every player and the solve is infeasible.
    captain_guard = any(p.can_captain and p.minutes.expected >= min_start
                        for p in P)
    if not captain_guard:
        gate_note = " ".join(filter(None, [
            gate_note, "captain guard dropped: no player has observed minutes"]))

    m = pulp.LpProblem("ucl_squad", pulp.LpMaximize)
    sq = pulp.LpVariable.dicts("sq", idx, cat="Binary")
    st = pulp.LpVariable.dicts("st", idx, cat="Binary")
    cp = pulp.LpVariable.dicts("cp", idx, cat="Binary")

    eff = {i: _effective(P[i]) for i in idx}
    m += pulp.lpSum(
        BENCH_WEIGHT * eff[i] * sq[i]
        + (1 - BENCH_WEIGHT) * eff[i] * st[i]
        + eff[i] * cp[i]
        for i in idx
    )

    m += pulp.lpSum(sq[i] for i in idx) == SQUAD_SIZE
    m += pulp.lpSum(st[i] for i in idx) == XI_SIZE
    m += pulp.lpSum(cp[i] for i in idx) == 1
    # An infinite budget means "no cap" (Limitless, or picking from a squad you
    # already own). PuLP rejects an infinite RHS, so drop the constraint.
    if not unlimited_budget and math.isfinite(budget):
        m += pulp.lpSum(P[i].player.value * sq[i] for i in idx) <= budget

    for i in idx:
        m += st[i] <= sq[i]
        m += cp[i] <= st[i]
        if P[i].minutes.expected < min_start:
            m += st[i] == 0                       # the minutes gate
        if captain_guard and not P[i].can_captain:
            m += cp[i] == 0                       # the Gordon guard
        if P[i].player.id in banned:
            m += sq[i] == 0
        if P[i].player.id in locked:
            m += sq[i] == 1

    for pos, n in SQUAD_SHAPE.items():
        g = [i for i in idx if P[i].player.pos == pos]
        m += pulp.lpSum(sq[i] for i in g) == n
        m += pulp.lpSum(st[i] for i in g) >= XI_MIN[pos]
        m += pulp.lpSum(st[i] for i in g) <= XI_MAX[pos]

    for club in {P[i].player.team_id for i in idx}:
        g = [i for i in idx if P[i].player.team_id == club]
        m += pulp.lpSum(sq[i] for i in g) <= max_per_club

    if m.solve(pulp.PULP_CBC_CMD(msg=0)) != pulp.LpStatusOptimal:
        raise RuntimeError(f"infeasible: {pulp.LpStatus[m.status]}")

    picks = [P[i] for i in idx if sq[i].value() > 0.5]
    xi = [P[i] for i in idx if st[i].value() > 0.5]
    capt = next(P[i] for i in idx if cp[i].value() > 0.5)
    return Squad(
        picks=sorted(picks, key=lambda p: (p.player.pos, -p.points)),
        xi=sorted(xi, key=lambda p: (p.player.pos, -p.points)),
        captain=capt,
        cost=sum(p.player.value for p in picks),
        xi_points=sum(p.points for p in xi),
        min_start_used=min_start,
        note=gate_note,
    )


def best_xi_from(squad_ids: set[int], projections: list[Projection],
                 min_start: float = 0.0) -> Squad:
    """Best XI and captain from a squad you already own (no transfers)."""
    owned = [p for p in projections if p.player.id in squad_ids]
    if len(owned) != SQUAD_SIZE:
        raise ValueError(f"expected {SQUAD_SIZE} owned, got {len(owned)}")
    return optimize(owned, budget=float("inf"), max_per_club=SQUAD_SIZE,
                    min_start=min_start, locked=squad_ids)


def demo():
    """Self-check: constraints hold, and both guards actually bind."""
    from .feeds import Player
    from .project import Minutes, MinutesSource

    def mk(i, pos, val, pts, mins, team, trusted=True):
        pl = Player(id=i, name=f"P{i}", team_id=team, team=f"T{team}", pos=pos,
                    value=val, status="", selected_pct=1, total_points=0,
                    minutes=0, goals=0, assists=0, clean_sheets=0, saves=0,
                    recoveries=0, motm=0, as_of_matchday=1)
        src = (MinutesSource.OBSERVED_UCL if trusted
               else MinutesSource.IMPUTED_PRICE)
        return Projection(pl, Minutes(mins, src), pts / max(mins, 1) * 90, 1.0,
                          pts, opponent_id=99, home=True, kickoff_day="09/09")

    pool, pid = [], 0
    for pos, n in (("GK", 6), ("DEF", 14), ("MID", 14), ("FWD", 9)):
        for k in range(n):
            pid += 1
            pool.append(mk(pid, pos, 4.0 + k * 0.5, 3.0 + k * 0.4, 85,
                           team=pid % 12))
    # A monster projection built on imputed minutes: must never wear the armband.
    pid += 1
    trap = mk(pid, "MID", 5.0, 50.0, 85, team=99, trusted=False)
    pool.append(trap)
    # A monster with real minutes but too few to start: must not make the XI.
    pid += 1
    benched = mk(pid, "FWD", 5.0, 40.0, 30, team=98)
    pool.append(benched)

    s = optimize(pool, budget=100.0, max_per_club=3, min_start=70)
    assert len(s.picks) == 15 and len(s.xi) == 11
    assert s.cost <= 100.0 + 1e-6, s.cost
    assert {p.player.id for p in s.xi} <= {p.player.id for p in s.picks}
    counts = {pos: sum(1 for p in s.picks if p.player.pos == pos)
              for pos in SQUAD_SHAPE}
    assert counts == SQUAD_SHAPE, counts
    assert s.captain.player.id != trap.player.id, "imputed minutes captained!"
    assert benched.player.id not in {p.player.id for p in s.xi}, "gate leaked"
    for club in {p.player.team_id for p in s.picks}:
        assert sum(1 for p in s.picks if p.player.team_id == club) <= 3

    # The MD1 stat-reset shape: everything imputed, nothing near the gate.
    # The gate must degrade to keep a legal XI, not return Infeasible.
    reset_pool = []
    for pos, n in (("GK", 4), ("DEF", 8), ("MID", 8), ("FWD", 5)):
        for k in range(n):
            pid += 1
            reset_pool.append(mk(pid, pos, 4.5 + k * 0.3, 2.0 + k * 0.2, 20,
                                 team=pid % 9, trusted=False))
    r = optimize(reset_pool, budget=100.0, max_per_club=3, min_start=70)
    assert len(r.xi) == XI_SIZE and len(r.picks) == SQUAD_SIZE
    assert r.min_start_used < 70, r.min_start_used
    assert "relaxed" in r.note or "dropped" in r.note, r.note

    # Limitless: dropping the budget cap cannot make the squad worse.
    rich = optimize(pool, budget=100.0, max_per_club=3, min_start=70,
                    unlimited_budget=True)
    assert rich.total_points >= s.total_points - 1e-6

    print(f"optimize.py self-check OK — XI {s.xi_points:.1f} + capt "
          f"{s.captain.points:.1f}, cost {s.cost:.1f}; guards bind")


if __name__ == "__main__":
    demo()
