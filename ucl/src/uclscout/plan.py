"""Multi-matchday planning: transfers, banking, chips, rotation.

All 8 league-phase fixtures for all 36 clubs are published before MD1, so the
league phase is ONE optimisation over a squad path -- not eight greedy weekly
picks. That is the whole reason this file exists.

MODELLING NOTES, and why each is shaped this way:

* Transfers. 2 free per matchday, at most 1 carries, so you can never hold more
  than 3. Extra transfers cost 4. Research on 24/25 found only 14.4% of the
  top-5000 took ANY hit all season, so `hit_cost` defaults above the nominal 4
  to reflect that a hit usually signals a squad-building error, not an edge.

* Wildcard stays INSIDE the MILP: it grants unlimited free transfers at one
  matchday and the squad persists, so it genuinely reshapes the path.

* Limitless is DECOUPLED and solved separately. It lasts one matchday and then
  the squad reverts, so it cannot affect the path at all -- its value is just
  (best unlimited-budget XI) - (best XI from the squad you already hold) at that
  matchday. Solving it outside the MILP costs nothing in accuracy and removes a
  whole set of binaries. This is the single biggest tractability win here.

* ponytail: prices are held constant across the horizon. UCL prices move on
  performance at each deadline, but forecasting them is a second-order effect on
  squad choice and would need its own model. Revisit only if backtesting shows
  budget drift materially changing picks.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import pulp

from .optimize import SQUAD_SHAPE, SQUAD_SIZE, XI_MAX, XI_MIN, XI_SIZE, Squad
from .optimize import optimize as pick_squad
from .project import Projection

FREE_PER_MATCHDAY = 2
MAX_CARRY = 1
MAX_BANKED = FREE_PER_MATCHDAY + MAX_CARRY
HIT_COST = 4.0
# Bar above the nominal hit cost. Top-5000 managers almost never take hits.
HIT_DECISION_BAR = 6.0
# Matchdays where everyone already gets unlimited transfers, so chips are barred.
UNLIMITED_MATCHDAYS = frozenset({1, 9, 11})
# Later matchdays are less certain; discount them rather than pretend otherwise.
HORIZON_DECAY = 0.90
POOL_SIZE = 130          # candidates kept for the multi-period solve
# A free transfer is not a free action: it has option value, and spending one
# for a fraction of a point is how a planner talks itself into churn. The first
# run of this sold Kane at MD3 and bought him back at MD4. This is the bar a
# transfer must clear even when it costs no points.
CHURN_PENALTY = 0.35


@dataclass
class MatchdayAction:
    matchday: int
    buys: list[Projection] = field(default_factory=list)
    sells: list[Projection] = field(default_factory=list)
    chip: str | None = None
    captain: Projection | None = None
    free_available: int = 0
    hits: int = 0
    xi_points: float = 0.0

    @property
    def banking(self) -> bool:
        return not self.buys and not self.chip

    def describe(self) -> str:
        if self.chip:
            return f"MD{self.matchday}: PLAY {self.chip.upper()}"
        if self.banking:
            return (f"MD{self.matchday}: bank "
                    f"({self.free_available} free available)")
        moves = ", ".join(f"{s.player.name} -> {b.player.name}"
                          for s, b in zip(self.sells, self.buys))
        tail = f"  [-{self.hits * HIT_COST:.0f} hit]" if self.hits else ""
        return f"MD{self.matchday}: {moves}{tail}"


@dataclass
class Plan:
    actions: list[MatchdayAction]
    opening_squad: Squad | None
    limitless: tuple[int, float] | None      # (matchday, points gained)
    total_points: float

    def summary(self) -> str:
        lines = [a.describe() for a in self.actions]
        if self.limitless:
            md, gain = self.limitless
            lines.append(f"MD{md}: PLAY LIMITLESS  (+{gain:.1f} pts vs holding)")
        return "\n".join(lines)


def build_horizon(
    project_fn,
    matchdays: list[int],
) -> dict[int, list[Projection]]:
    """dict of matchday -> projections. `project_fn(md)` does the work."""
    return {md: project_fn(md) for md in matchdays}


def _pool(horizon: dict[int, list[Projection]], size: int,
          keep: set[int]) -> list[int]:
    """Restrict to plausible players, keeping anyone already owned.

    ponytail: a full 1,163-player multi-period MILP is not solvable in
    reasonable time. Ranking by total horizon points and keeping the top ~130
    cannot lose the optimum by anything material, because a player outside the
    top 130 over 8 matchdays is not making a 15-man squad.
    """
    total: dict[int, float] = {}
    for projs in horizon.values():
        for p in projs:
            total[p.player.id] = total.get(p.player.id, 0.0) + p.points
    ranked = sorted(total, key=lambda pid: -total[pid])
    chosen = list(dict.fromkeys(list(keep) + ranked))[:max(size, len(keep))]
    return chosen


def plan_horizon(
    horizon: dict[int, list[Projection]],
    *,
    current_squad: set[int] | None = None,
    budget: float = 100.0,
    max_per_club: int = 3,
    min_start: float = 70.0,
    allow_wildcard: bool = True,
    hit_cost: float = HIT_DECISION_BAR,
    pool_size: int = POOL_SIZE,
    time_limit: int = 120,
) -> Plan:
    """Solve the squad path across the horizon.

    With `current_squad` empty this is a fresh build (MD1, or any unlimited
    window). Otherwise it plans transfers off what you already own.
    """
    mds = sorted(horizon)
    if not mds:
        raise ValueError("empty horizon")
    current_squad = current_squad or set()
    fresh = not current_squad

    pool_ids = _pool(horizon, pool_size, current_squad)
    by_md = {md: {p.player.id: p for p in horizon[md]} for md in mds}
    # A player must be projectable in every matchday to enter the MILP cleanly.
    pool_ids = [i for i in pool_ids if all(i in by_md[md] for md in mds)]
    meta = {i: by_md[mds[0]][i] for i in pool_ids}

    m = pulp.LpProblem("ucl_plan", pulp.LpMaximize)
    own = pulp.LpVariable.dicts("own", (pool_ids, mds), cat="Binary")
    start = pulp.LpVariable.dicts("st", (pool_ids, mds), cat="Binary")
    capt = pulp.LpVariable.dicts("cp", (pool_ids, mds), cat="Binary")
    buy = pulp.LpVariable.dicts("buy", (pool_ids, mds), cat="Binary")
    sell = pulp.LpVariable.dicts("sell", (pool_ids, mds), cat="Binary")
    hits = pulp.LpVariable.dicts("hit", mds, lowBound=0, cat="Integer")
    carry = pulp.LpVariable.dicts("carry", mds, cat="Binary")
    wc = pulp.LpVariable.dicts("wc", mds, cat="Binary")

    def pts(i, md):
        return by_md[md][i].points * (HORIZON_DECAY ** (md - mds[0]))

    m += (
        pulp.lpSum(pts(i, md) * start[i][md] + pts(i, md) * capt[i][md]
                   for i in pool_ids for md in mds)
        - pulp.lpSum(hit_cost * hits[md] for md in mds)
        # every transfer pays the churn bar, including "free" ones
        - pulp.lpSum(CHURN_PENALTY * buy[i][md]
                     for i in pool_ids for md in mds[1:])
    )

    for md in mds:
        m += pulp.lpSum(own[i][md] for i in pool_ids) == SQUAD_SIZE
        m += pulp.lpSum(start[i][md] for i in pool_ids) == XI_SIZE
        m += pulp.lpSum(capt[i][md] for i in pool_ids) == 1
        m += pulp.lpSum(meta[i].player.value * own[i][md]
                        for i in pool_ids) <= budget

        for pos, n in SQUAD_SHAPE.items():
            g = [i for i in pool_ids if meta[i].player.pos == pos]
            m += pulp.lpSum(own[i][md] for i in g) == n
            m += pulp.lpSum(start[i][md] for i in g) >= XI_MIN[pos]
            m += pulp.lpSum(start[i][md] for i in g) <= XI_MAX[pos]

        for club in {meta[i].player.team_id for i in pool_ids}:
            g = [i for i in pool_ids if meta[i].player.team_id == club]
            m += pulp.lpSum(own[i][md] for i in g) <= max_per_club

        for i in pool_ids:
            p = by_md[md][i]
            m += start[i][md] <= own[i][md]
            m += capt[i][md] <= start[i][md]
            if p.minutes.expected < min_start or not p.player.available:
                m += start[i][md] == 0
            if not p.can_captain:
                m += capt[i][md] == 0

    # squad continuity and transfer accounting
    first = mds[0]
    for i in pool_ids:
        opening = 1 if i in current_squad else 0
        if fresh:
            m += buy[i][first] == own[i][first]
            m += sell[i][first] == 0
        else:
            m += own[i][first] == opening + buy[i][first] - sell[i][first]
        for prev, md in itertools.pairwise(mds):
            m += own[i][md] == own[i][prev] + buy[i][md] - sell[i][md]
            m += buy[i][md] + sell[i][md] <= 1

    big = SQUAD_SIZE + 1
    unlimited_at = {md for md in mds if md in UNLIMITED_MATCHDAYS}
    if fresh:
        unlimited_at.add(mds[0])
    for k, md in enumerate(mds):
        used = pulp.lpSum(buy[i][md] for i in pool_ids)
        unlimited = md in unlimited_at
        if md in UNLIMITED_MATCHDAYS or not allow_wildcard:
            m += wc[md] == 0
        if unlimited:
            m += hits[md] == 0
            m += carry[md] == 0
            continue
        available = FREE_PER_MATCHDAY + (carry[md] if k else 0)
        m += hits[md] >= used - available - big * wc[md]
        # carry only what went unused last matchday, and at most one
        if k:
            prev = mds[k - 1]
            if prev in unlimited_at:
                # nothing carries out of an unlimited window: the opening build
                # (or a chip) is not spending free transfers to be banked
                m += carry[md] == 0
            else:
                prev_avail = FREE_PER_MATCHDAY + (carry[prev] if k > 1 else 0)
                m += carry[md] <= prev_avail - pulp.lpSum(buy[i][prev]
                                                          for i in pool_ids)
    # A wildcard on the LAST matchday of the horizon has no future to pay off,
    # so the solver parks it there for free. It is also genuinely near-worthless
    # in the real game: MD9 and MD11 hand out unlimited transfers anyway, so a
    # wildcard immediately before one buys almost nothing.
    if len(mds) > 1:
        m += wc[mds[-1]] == 0
    m += pulp.lpSum(wc[md] for md in mds) <= (1 if allow_wildcard else 0)

    solver = pulp.PULP_CBC_CMD(msg=0, timeLimit=time_limit)
    status = m.solve(solver)
    if pulp.LpStatus[status] not in {"Optimal", "Not Solved", "Undefined"}:
        raise RuntimeError(f"plan infeasible: {pulp.LpStatus[status]}")

    def on(var) -> bool:
        v = var.value()
        return v is not None and v > 0.5

    actions, total = [], 0.0
    for k, md in enumerate(mds):
        xi = [by_md[md][i] for i in pool_ids if on(start[i][md])]
        cap = next((by_md[md][i] for i in pool_ids if on(capt[i][md])), None)
        got = sum(p.points for p in xi) + (cap.points if cap else 0.0)
        total += got
        actions.append(MatchdayAction(
            matchday=md,
            buys=[by_md[md][i] for i in pool_ids
                  if on(buy[i][md]) and not (fresh and k == 0)],
            sells=[by_md[md][i] for i in pool_ids if on(sell[i][md])],
            chip="wildcard" if on(wc[md]) else None,
            captain=cap,
            free_available=int(FREE_PER_MATCHDAY + (carry[md].value() or 0)),
            hits=int(hits[md].value() or 0),
            xi_points=got,
        ))

    opening = None
    if fresh:
        opening_ids = {i for i in pool_ids if on(own[i][first])}
        opening = _squad_from(opening_ids, horizon[first], actions[0].captain)
    return Plan(actions=actions, opening_squad=opening, limitless=None,
                total_points=total)


def _squad_from(ids: set[int], projs: list[Projection],
                captain: Projection | None) -> Squad:
    picks = [p for p in projs if p.player.id in ids]
    xi = sorted(picks, key=lambda p: -p.points)[:XI_SIZE]
    cap = captain or max(xi, key=lambda p: p.points)
    return Squad(picks=picks, xi=xi, captain=cap,
                 cost=sum(p.player.value for p in picks),
                 xi_points=sum(p.points for p in xi))


def limitless_matchday(
    horizon: dict[int, list[Projection]],
    squad_by_md: dict[int, set[int]],
    *,
    budget: float = 100.0,
    max_per_club: int = 3,
    min_start: float = 70.0,
) -> tuple[int, float]:
    """Best matchday to play Limitless, and what it gains.

    Decoupled from the path: Limitless reverts, so it changes nothing else.
    """
    best_md, best_gain = None, float("-inf")
    for md, projs in horizon.items():
        if md in UNLIMITED_MATCHDAYS:
            continue
        held = squad_by_md.get(md, set())
        if len(held) != SQUAD_SIZE:
            continue
        owned = [p for p in projs if p.player.id in held]
        try:
            base = pick_squad(owned, budget=float("inf"),
                              max_per_club=SQUAD_SIZE, min_start=min_start,
                              locked=held)
            rich = pick_squad(projs, budget=budget, max_per_club=max_per_club,
                              min_start=min_start, unlimited_budget=True)
        except (ValueError, RuntimeError):
            continue
        gain = rich.total_points - base.total_points
        if gain > best_gain:
            best_md, best_gain = md, gain
    if best_md is None:
        raise ValueError("no legal Limitless matchday")
    return best_md, best_gain


def rotation_pairs(
    horizon: dict[int, list[Projection]],
    position: str = "GK",
    top_n: int = 8,
) -> list[tuple[Projection, Projection, float]]:
    """Find cheap pairs whose fixtures alternate, so one is always favourable.

    Value is the sum over matchdays of max(a, b) minus the better solo player --
    i.e. what rotating actually buys you over just owning the best one.
    """
    mds = sorted(horizon)
    cands: dict[int, list[float]] = {}
    meta: dict[int, Projection] = {}
    for md in mds:
        for p in horizon[md]:
            if p.player.pos != position:
                continue
            cands.setdefault(p.player.id, []).append(p.points)
            meta.setdefault(p.player.id, p)
    ids = [i for i, v in cands.items() if len(v) == len(mds)]
    ids.sort(key=lambda i: -sum(cands[i]))
    ids = ids[: top_n * 4]

    out = []
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            ia, ib = ids[a], ids[b]
            rot = sum(max(x, y) for x, y in zip(cands[ia], cands[ib]))
            solo = max(sum(cands[ia]), sum(cands[ib]))
            out.append((meta[ia], meta[ib], rot - solo))
    out.sort(key=lambda t: -t[2])
    return out[:top_n]


def matchday_checklist(matchday: int, gamedays: list[str],
                       subs_allowed: int) -> list[str]:
    """The operational reminders that actually separate top managers.

    From 24/25 data: top-5000 managers switched captain mid-matchday 44.6% of
    the time vs 6.1% for the average manager, and made manual subs 96.2% vs
    15.0%. These are the highest-leverage habits in the game and they cost
    nothing but attention.
    """
    out = [
        (f"MD{matchday} spans {len(gamedays)} days ({', '.join(gamedays)}) "
        f"— up to {subs_allowed} manual subs allowed."),
        ("After day 1, reassess: move the armband to an unstarted player if your "
        "captain blanked. Top-5000 do this 44.6% of the time; average 6.1%."),
        "Sub out anyone who did not play, before the next day kicks off.",
    ]
    if len(gamedays) > 1:
        out.append("WARNING: any manual change — a sub, a captain switch, even "
                   "reordering the bench — disables auto-subs for this whole "
                   "matchday, and reverting does not restore them.")
    return out


def demo():
    """Self-check on a synthetic 3-matchday horizon."""
    from .feeds import Player
    from .project import Minutes, MinutesSource

    def mk(i, pos, val, team):
        return Player(id=i, name=f"P{i}", team_id=team, team=f"T{team}",
                      pos=pos, value=val, status="", selected_pct=1,
                      total_points=0, minutes=900, goals=0, assists=0,
                      clean_sheets=0, saves=0, recoveries=0, motm=0,
                      as_of_matchday=1)

    players, pid = [], 0
    for pos, n in (("GK", 5), ("DEF", 12), ("MID", 12), ("FWD", 8)):
        for k in range(n):
            pid += 1
            players.append(mk(pid, pos, 4.0 + (k % 6) * 0.5, team=pid % 10))

    mds = [2, 3, 4]
    horizon = {}
    for md in mds:
        horizon[md] = [
            Projection(pl, Minutes(85, MinutesSource.OBSERVED_UCL),
                       5.0, 1.0,
                       # give one player a spike at MD3 so a transfer is worth it
                       8.0 if (pl.id == 20 and md == 3) else 3.0 + (pl.id % 5) * 0.3,
                       opponent_id=1, home=True, kickoff_day=f"2026-10-0{md}")
            for pl in players
        ]

    plan = plan_horizon(horizon, budget=100.0, min_start=70, time_limit=25)
    assert len(plan.actions) == 3
    for a in plan.actions:
        assert a.hits >= 0
        assert len(a.buys) == len(a.sells) or a.chip, (a.matchday, a.buys)
    # transfers beyond the free allowance must be recorded as hits
    for a in plan.actions[1:]:
        if not a.chip:
            assert len(a.buys) <= a.free_available + a.hits

    def legal_15(projs):
        out = []
        for pos, n in SQUAD_SHAPE.items():
            out += [p.player.id for p in projs if p.player.pos == pos][:n]
        return set(out)

    squads = {md: legal_15(horizon[md]) for md in mds}
    assert all(len(v) == SQUAD_SIZE for v in squads.values())
    md, gain = limitless_matchday(horizon, squads, budget=100.0)
    assert md in mds and gain >= 0, (md, gain)

    pairs = rotation_pairs(horizon, "GK", top_n=3)
    assert pairs and all(v >= 0 for *_, v in pairs)

    cl = matchday_checklist(1, ["2026-09-08", "2026-09-09", "2026-09-10"], 2)
    assert any("auto-subs" in c for c in cl)

    print(f"plan.py self-check OK — {len(plan.actions)} matchdays planned, "
          f"limitless MD{md} (+{gain:.1f}), {len(pairs)} rotation pairs")


if __name__ == "__main__":
    demo()
