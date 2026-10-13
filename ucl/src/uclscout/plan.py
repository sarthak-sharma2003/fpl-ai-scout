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

* The rules change by round (docs/GAPS.md #7): budget, max per club and the
  free-transfer allowance are per-matchday values from `knockout`, and nothing
  rolls over in the knockouts. A club with no fixture on a matchday (a top-8
  bye at MD9-10, or out) is in the horizon at zero points rather than absent,
  so the planner can still own him for the rounds he does play.
"""
from __future__ import annotations

import itertools
from collections.abc import Mapping
from dataclasses import dataclass, field

import pulp

from . import knockout
from .knockout import FREE_PER_MATCHDAY, MAX_CARRY, UNLIMITED_MATCHDAYS
from .optimize import (
    SQUAD_SHAPE,
    SQUAD_SIZE,
    XI_MAX,
    XI_MIN,
    XI_SIZE,
    Squad,
    _feasible_gate,
)
from .optimize import optimize as pick_squad
from .project import Minutes, MinutesSource, Projection

MAX_BANKED = FREE_PER_MATCHDAY + MAX_CARRY
HIT_COST = 4.0
# Bar above the nominal hit cost. Top-5000 managers almost never take hits.
HIT_DECISION_BAR = 6.0
# UNLIMITED_MATCHDAYS (1, 9, 11): everyone already gets unlimited transfers, so
# chips are barred there. Defined with the other round rules in `knockout`.
# Later matchdays are less certain; discount them rather than pretend otherwise.
HORIZON_DECAY = 0.90
POOL_SIZE = 130          # candidates kept for the multi-period solve
# Every match kicks off together: no manual subs and no captain switch.
SIMULTANEOUS_MATCHDAYS = frozenset({8, 17})
# A free transfer is not a free action: it has option value, and spending one
# for a fraction of a point is how a planner talks itself into churn. The first
# run of this sold Kane at MD3 and bought him back at MD4. This is the bar a
# transfer must clear even when it costs no points.
# Raised from 0.35 (2026-10-13): planning the real MD2 squad at 0.35 sold Kane
# at MD3 and bought him back at MD5, chasing opponent-strength swings.
CHURN_PENALTY = 1.0
# What holding the wildcard is worth beyond anything the plan can see.
#
# Until 2026-10 the horizon ended at the last PUBLISHED fixtures (MD8 until the
# knockout draw), so this number stood in for everything after it. On the real
# MD2 squad the wildcard bought only +6.5 projected points over MD2-8 vs two
# free transfers, and 10 kept it in hand.
#
# The horizon now runs to the final, with the undrawn knockouts projected at
# P(club still in) x a generic opponent (`pipeline.horizon`), and
# `pipeline.build_plan` prices the wildcard's best knockout use explicitly: the
# bar for spending it earlier is max(WILDCARD_HOLD_VALUE, that gain). So this
# is now only the RESIDUAL a deterministic plan cannot see, and it stays at 10
# rather than shrinking, because the computed knockout gain is biased LOW:
#   (1) before the draw a knockout matchday is P(alive) x a generic opponent,
#       so every club's players move together from round to round and the
#       free rebuilds at MD9/MD11 already buy the best squad for the rest.
#       Measured on the real MD2 squad, from MD2 and again from MD8: the
#       wildcard's best knockout use gains exactly 0.0 points. Its real value
#       -- the round after a draw, when half the squad is out and 3-5 free
#       transfers cannot replace them -- only becomes visible once that
#       round is drawn, and then this bar is computed, not assumed;
#   (2) it is insurance against injuries and suspensions, which no point
#       projection contains.
# So the bar in the league phase is unchanged at 10. Not tuned on the replay:
# the replay has no chips.
WILDCARD_HOLD_VALUE = 10.0


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
    xi: list[Projection] = field(default_factory=list)
    squad: list[Projection] = field(default_factory=list)   # all 15 after moves
    # a free rebuild inside the plan (MD9/MD11); not a fresh plan's opening
    unlimited: bool = False
    # projected against a generic opponent: the bracket was not drawn yet
    generic: bool = False

    @property
    def banking(self) -> bool:
        return not self.buys and not self.chip

    def describe(self) -> str:
        if self.chip:
            return f"MD{self.matchday}: PLAY {self.chip.upper()}"
        tag = " (bracket not drawn)" if self.generic else ""
        if self.banking:
            free = ("unlimited" if self.unlimited
                    else f"{self.free_available} free available")
            return f"MD{self.matchday}: bank ({free}){tag}"
        if self.unlimited:
            return (f"MD{self.matchday}: rebuild with unlimited transfers, "
                    f"{len(self.buys)} changes{tag}")
        moves = ", ".join(f"{s.player.name} -> {b.player.name}"
                          for s, b in zip(self.sells, self.buys))
        tail = f"  [-{self.hits * HIT_COST:.0f} hit]" if self.hits else ""
        return f"MD{self.matchday}: {moves}{tail}{tag}"


@dataclass
class Plan:
    actions: list[MatchdayAction]
    opening_squad: Squad | None
    limitless: tuple[int, float] | None      # (matchday, points gained)
    total_points: float
    budget: float = 100.0
    note: str = ""                           # set when a fallback was needed
    # the solver's own objective: decayed points net of hits, churn and the
    # wildcard bar -- what `pipeline.build_plan` compares plans on
    objective: float = 0.0
    # Every chip is checked on every run and the verdict published, "hold"
    # included: the manager should see that it was looked at, and by how much
    # it missed. chip -> {advice, matchday, gain, bar, ...}; see
    # `pipeline.build_plan`.
    chip_check: dict[str, dict] = field(default_factory=dict)
    # the squad to pick when Limitless is advised for the plan's first matchday
    limitless_squad: Squad | None = None

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


_POS_ORDER = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}


def by_position(ps: list[Projection]) -> list[Projection]:
    """Goalkeepers first, then defenders, midfielders, forwards; best first
    within each. The squad shape is fixed, so a matchday sells as many of each
    position as it buys -- sorting both lists this way pairs every sale with a
    purchase in the same position, which is the only swap the game allows
    (zipping them unsorted printed "Fermín López (MID) -> Bartra (DEF)")."""
    return sorted(ps, key=lambda p: (_POS_ORDER.get(p.player.pos, 9), -p.points))


def _per_md(v, md: int):
    """A rule given either as one value or as a per-matchday mapping."""
    return v[md] if isinstance(v, Mapping) else v


def no_fixture(p: Projection) -> Projection:
    """A zero projection for a matchday on which his club does not play."""
    return Projection(p.player,
                      Minutes(0.0, MinutesSource.OBSERVED_UCL, "no fixture"),
                      p.points_per90, 1.0, 0.0, None, None, None)


def _dead(p: Projection) -> bool:
    return p.points <= 0 and p.minutes.expected <= 0


def horizon_complete(md: int) -> bool:
    """Does a horizon ending at `md` see everything a squad there affects?

    True at the final, and on the eve of a free rebuild (MD8, MD10): the squad
    is rebuilt at no cost next matchday, so nothing after it depends on it.
    """
    return md >= knockout.FINAL_MD or (md + 1) in UNLIMITED_MATCHDAYS


def plan_horizon(
    horizon: dict[int, list[Projection]],
    *,
    current_squad: set[int] | None = None,
    budget: float | Mapping[int, float] = 100.0,
    max_per_club: int | Mapping[int, int] = 3,
    min_start: float = 70.0,
    allow_wildcard: bool = True,
    initial_free: int = FREE_PER_MATCHDAY,
    hit_cost: float = HIT_DECISION_BAR,
    pool_size: int = POOL_SIZE,
    time_limit: int = 120,
    decay_from: int | None = None,
    wildcard_hold: float = WILDCARD_HOLD_VALUE,
) -> Plan:
    """Solve the squad path across the horizon.

    With `current_squad` empty this is a fresh build (MD1, or any unlimited
    window). Otherwise it plans transfers off what you already own.
    `budget` and `max_per_club` may be per-matchday mappings (they change by
    round). `decay_from` is the matchday the horizon decay counts from, when
    this horizon is one segment of a longer plan.
    """
    mds = sorted(horizon)
    if not mds:
        raise ValueError("empty horizon")
    current_squad = current_squad or set()
    fresh = not current_squad
    decay_from = mds[0] if decay_from is None else decay_from

    pool_ids = _pool(horizon, pool_size, current_squad)
    by_md = {md: {p.player.id: p for p in horizon[md]} for md in mds}
    # Every pool player gets a projection on every matchday. One missing from a
    # matchday has no fixture there (a top-8 club skips the play-offs, an
    # eliminated one plays no more): he is worth zero, not unownable. In the
    # league phase every club plays every matchday, so nothing is filled.
    seen: dict[int, Projection] = {}
    for md in mds:
        for i, p in by_md[md].items():
            seen.setdefault(i, p)
    pool_ids = [i for i in pool_ids if i in seen]
    for md in mds:
        for i in pool_ids:
            if i not in by_md[md]:
                by_md[md][i] = no_fixture(seen[i])
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
        return by_md[md][i].points * (HORIZON_DECAY ** (md - decay_from))

    m += (
        pulp.lpSum(pts(i, md) * start[i][md] + pts(i, md) * capt[i][md]
                   for i in pool_ids for md in mds)
        - pulp.lpSum(hit_cost * hits[md] for md in mds)
        - pulp.lpSum(wildcard_hold * wc[md] for md in mds)
        # every transfer pays the churn bar, including "free" ones (the opening
        # build of a fresh squad is not a transfer)
        - pulp.lpSum(CHURN_PENALTY * buy[i][md]
                     for i in pool_ids for md in (mds[1:] if fresh else mds))
    )

    # The XI gate relaxes per matchday exactly as the single-matchday optimizer's
    # does: a hard 70-minute gate on a horizon where nobody has 70 observed
    # minutes forces the solver to buy its way to a legal XI with hits.
    gate = {md: _feasible_gate(horizon[md], min_start)[0] for md in mds}
    for md in mds:
        m += pulp.lpSum(own[i][md] for i in pool_ids) == SQUAD_SIZE
        m += pulp.lpSum(start[i][md] for i in pool_ids) == XI_SIZE
        m += pulp.lpSum(capt[i][md] for i in pool_ids) == 1
        m += pulp.lpSum(meta[i].player.value * own[i][md]
                        for i in pool_ids) <= _per_md(budget, md)

        for pos, n in SQUAD_SHAPE.items():
            g = [i for i in pool_ids if meta[i].player.pos == pos]
            m += pulp.lpSum(own[i][md] for i in g) == n
            m += pulp.lpSum(start[i][md] for i in g) >= XI_MIN[pos]
            m += pulp.lpSum(start[i][md] for i in g) <= XI_MAX[pos]

        cap = _per_md(max_per_club, md)
        for club in {meta[i].player.team_id for i in pool_ids}:
            g = [i for i in pool_ids if meta[i].player.team_id == club]
            m += pulp.lpSum(own[i][md] for i in g) <= cap

        for i in pool_ids:
            p = by_md[md][i]
            m += start[i][md] <= own[i][md]
            m += capt[i][md] <= start[i][md]
            # In the knockouts a squad can be left holding more players with
            # no game than its transfers can replace (5 free before a QF with
            # half the field gone). The real game lets you field them for
            # zero, so the plan may too -- it never prefers to, as they score
            # nothing. In the league phase the gate stays strict, as before.
            dead_ok = knockout.is_knockout(md) and _dead(p)
            if not dead_ok and (p.minutes.start_equiv < gate[md]
                                or not p.player.available):
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
            # no phantom buy-and-sell of the same player, and you cannot sell
            # someone you do not own (free on a wildcard otherwise)
            m += buy[i][first] + sell[i][first] <= 1
            if not opening:
                m += sell[i][first] == 0
        for prev, md in itertools.pairwise(mds):
            m += own[i][md] == own[i][prev] + buy[i][md] - sell[i][md]
            m += buy[i][md] + sell[i][md] <= 1

    big = SQUAD_SIZE + 1
    unlimited_at = {md for md in mds if md in UNLIMITED_MATCHDAYS}
    if fresh:
        unlimited_at.add(mds[0])
    # Free transfers on each matchday. The first one's is what the manager
    # actually holds (2, or 3 with one banked) in the league phase; later
    # league matchdays follow the carry variable; a knockout matchday has its
    # own fixed allowance and nothing carries into it.
    avail = {}
    for k, md in enumerate(mds):
        if md in unlimited_at:
            continue
        base = knockout.free_transfers(md) or FREE_PER_MATCHDAY
        if not knockout.carries(md):
            avail[md] = base
        elif k == 0:
            avail[md] = initial_free
        else:
            avail[md] = base + carry[md]
    for k, md in enumerate(mds):
        used = pulp.lpSum(buy[i][md] for i in pool_ids)
        if md in UNLIMITED_MATCHDAYS or not allow_wildcard:
            m += wc[md] == 0
        if md in unlimited_at:
            m += hits[md] == 0
            m += carry[md] == 0
            continue
        m += hits[md] >= used - avail[md] - big * wc[md]
        # carry only what went unused last matchday, and at most one
        if k and knockout.carries(md):
            prev = mds[k - 1]
            if prev in unlimited_at:
                # nothing carries out of an unlimited window: the opening build
                # (or a chip) is not spending free transfers to be banked
                m += carry[md] == 0
            else:
                # Unused = available - bought, floored at 0. A hit or a
                # wildcard at `prev` makes (available - bought) negative; the
                # old `carry <= available - bought` then demanded carry <= -1,
                # so ANY hit or wildcard before the final matchday was
                # infeasible -- and a squad needing three moves at once could
                # not be planned at all (MD2 2026-27: "plan infeasible").
                # Adding hits[prev] restores the floor (hits >= the overspend),
                # and a wildcard's unlimited buys carry nothing.
                m += carry[md] <= (avail[prev]
                                   - pulp.lpSum(buy[i][prev] for i in pool_ids)
                                   + hits[prev] + big * wc[prev])
                m += carry[md] <= 1 - wc[prev]
        elif k:
            m += carry[md] == 0
    # A wildcard on the LAST matchday of a horizon that stops short has no
    # future to pay off, so the solver parks it there for free. A horizon that
    # runs to the final, or to the eve of a free rebuild, sees the wildcard's
    # whole effect, so there it is priced like anywhere else.
    if len(mds) > 1 and not horizon_complete(mds[-1]):
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
        # an opening build reports the allowance it hands on, as it always
        # did; a rebuild inside the plan reports the squad size (unlimited)
        free = (initial_free if fresh and k == 0
                else avail.get(md, SQUAD_SIZE))
        if not isinstance(free, int):
            free = round(pulp.value(free) or FREE_PER_MATCHDAY)
        actions.append(MatchdayAction(
            matchday=md,
            buys=by_position([by_md[md][i] for i in pool_ids
                              if on(buy[i][md]) and not (fresh and k == 0)]),
            sells=by_position([by_md[md][i] for i in pool_ids if on(sell[i][md])]),
            chip="wildcard" if on(wc[md]) else None,
            captain=cap,
            free_available=free,
            hits=int(hits[md].value() or 0),
            xi_points=got,
            xi=sorted(xi, key=lambda p: -p.points),
            squad=[by_md[md][i] for i in pool_ids if on(own[i][md])],
            unlimited=md in unlimited_at and not (fresh and k == 0),
        ))

    opening = None
    if fresh:
        opening_ids = {i for i in pool_ids if on(own[i][first])}
        opening = _squad_from(opening_ids, horizon[first], actions[0].captain)
    return Plan(actions=actions, opening_squad=opening, limitless=None,
                total_points=total,
                objective=float(pulp.value(m.objective) or 0.0))


def _squad_from(ids: set[int], projs: list[Projection],
                captain: Projection | None) -> Squad:
    picks = [p for p in projs if p.player.id in ids]
    xi = sorted(picks, key=lambda p: -p.points)[:XI_SIZE]
    cap = captain or max(xi, key=lambda p: p.points)
    return Squad(picks=picks, xi=xi, captain=cap,
                 cost=sum(p.player.value for p in picks),
                 xi_points=sum(p.points for p in xi))


def held_xi_points(owned: list[Projection], min_start: float = 70.0) -> float:
    """Best XI plus captain from players you own, fielding zeros where forced.

    Greedy is exact here: each position's minimum from its best players, then
    the remaining places from the best of the rest under each maximum. Unlike
    `optimize` it needs no 15 available players -- an injured or eliminated
    owner simply does not score. (Requiring 15 is why Limitless came back
    empty for the MD2 squad: one injured bench defender sank every matchday.)
    """
    live = [p for p in owned if p.player.available and not _dead(p)]
    if not live:
        return 0.0
    gate = _feasible_gate(live, min_start)[0]
    ok = sorted((p for p in live if p.minutes.start_equiv >= gate),
                key=lambda p: -p.points)
    xi: list[Projection] = []
    for pos, n in XI_MIN.items():
        xi += [p for p in ok if p.player.pos == pos][:n]
    taken = {p.player.id for p in xi}
    count = {pos: sum(1 for p in xi if p.player.pos == pos) for pos in XI_MAX}
    for p in ok:
        if len(xi) >= XI_SIZE:
            break
        if p.player.id in taken or count[p.player.pos] >= XI_MAX[p.player.pos]:
            continue
        xi.append(p)
        taken.add(p.player.id)
        count[p.player.pos] += 1
    caps = [p for p in xi if p.can_captain] or xi
    return sum(p.points for p in xi) + max(p.points for p in caps)


LIMITLESS_CANDIDATES = 40     # per position; a Limitless 15 never needs more
# Limitless is not advised below this gain. It used to be advised wherever the
# gain was largest, however small -- but Limitless is once a season, and the
# P(alive)-weighted knockout projection is flat (a fresh rebuild at MD9/MD11
# IS the unlimited squad there, gain ~0), so it cannot see the drawn-bracket
# matchdays where the chip pays. Same bar, same reasoning as
# WILDCARD_HOLD_VALUE. On the real MD2 squad the best league-phase gain is
# +4.2 (MD2): below the bar, so the advice is to hold it.
LIMITLESS_HOLD_VALUE = WILDCARD_HOLD_VALUE


def limitless_gains(
    horizon: dict[int, list[Projection]],
    squad_by_md: dict[int, set[int]],
    *,
    budget: float | Mapping[int, float] = 100.0,
    max_per_club: int | Mapping[int, int] = 3,
    min_start: float = 70.0,
) -> dict[int, tuple[float, Squad]]:
    """What Limitless would gain on each matchday it is legal on, and the
    squad it would pick there (matchday -> (gain, squad)).

    Decoupled from the path: Limitless reverts, so it changes nothing else.
    The gain is the unlimited-budget squad's XI + captain over the best XI the
    planned squad of that matchday can field.
    """
    out: dict[int, tuple[float, Squad]] = {}
    for md, projs in horizon.items():
        if md in UNLIMITED_MATCHDAYS:
            continue
        held = squad_by_md.get(md, set())
        if len(held) != SQUAD_SIZE:
            continue
        owned = [p for p in projs if p.player.id in held]
        cands = []
        for pos in SQUAD_SHAPE:
            cands += sorted((p for p in projs if p.player.pos == pos),
                            key=lambda p: -p.points)[:LIMITLESS_CANDIDATES]
        try:
            rich = pick_squad(cands, budget=_per_md(budget, md),
                              max_per_club=_per_md(max_per_club, md),
                              min_start=min_start, unlimited_budget=True)
        except (ValueError, RuntimeError):
            continue
        out[md] = (rich.total_points - held_xi_points(owned, min_start), rich)
    return out


def best_limitless(gains: dict[int, tuple[float, Squad]], *,
                   decay_from: int | None = None,
                   min_gain: float = LIMITLESS_HOLD_VALUE
                   ) -> tuple[int, float] | None:
    """The matchday to play Limitless on, or None to hold it.

    Matchdays are compared on the gain discounted like the planner discounts
    points (a gain six matchdays out is less certain than one next week); the
    gain returned is the undiscounted one at the chosen matchday. Only
    matchdays gaining at least `min_gain` qualify.
    """
    if not gains:
        return None
    decay_from = min(gains) if decay_from is None else decay_from
    best: tuple[float, int, float] | None = None
    for md, (gain, _) in gains.items():
        if gain < min_gain:
            continue
        score = gain * HORIZON_DECAY ** (md - decay_from)
        if best is None or score > best[0]:
            best = (score, md, gain)
    return None if best is None else (best[1], best[2])


def limitless_matchday(
    horizon: dict[int, list[Projection]],
    squad_by_md: dict[int, set[int]],
    *,
    budget: float | Mapping[int, float] = 100.0,
    max_per_club: int | Mapping[int, int] = 3,
    min_start: float = 70.0,
    decay_from: int | None = None,
    min_gain: float = LIMITLESS_HOLD_VALUE,
) -> tuple[int, float]:
    """Best matchday to play Limitless, and what it gains there (see
    `limitless_gains`, `best_limitless`). Raises ValueError when no matchday
    gains `min_gain`: hold the chip.
    """
    if not horizon:
        raise ValueError("no legal Limitless matchday")
    gains = limitless_gains(horizon, squad_by_md, budget=budget,
                            max_per_club=max_per_club, min_start=min_start)
    best = best_limitless(gains, decay_from=min(horizon) if decay_from is None
                          else decay_from, min_gain=min_gain)
    if best is None:
        raise ValueError("no legal Limitless matchday")
    return best


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

    Except on MD8 and MD17: every match kicks off at once, so there is no
    "after day 1" -- no manual subs and no captain switch. The bench order and
    the armband set at the deadline are final.
    """
    if matchday in SIMULTANEOUS_MATCHDAYS or len(gamedays) == 1:
        return [
            (f"MD{matchday}: every match kicks off at the same time — no "
             "manual subs and no captain switch once it starts."),
            ("Set the armband and the bench ORDER before the deadline: "
             "auto-subs are the only substitutions this matchday."),
        ]
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
    md, gain = limitless_matchday(horizon, squads, budget=100.0, min_gain=0.0)
    assert md in mds and gain >= 0, (md, gain)
    try:                                   # below the bar: hold the chip
        limitless_matchday(horizon, squads, budget=100.0, min_gain=gain + 1)
        raise AssertionError("Limitless advised below its bar")
    except ValueError:
        pass

    pairs = rotation_pairs(horizon, "GK", top_n=3)
    assert pairs and all(v >= 0 for *_, v in pairs)

    cl = matchday_checklist(1, ["2026-09-08", "2026-09-09", "2026-09-10"], 2)
    assert any("auto-subs" in c for c in cl)
    assert any("same time" in c for c in matchday_checklist(8, ["2027-01-27"], 2))

    # Knockouts: clubs 3 and 4 go out after MD12 (no fixture at MD13) and the
    # held squad is stacked with both, so only 7 owners still play. Per-round
    # rules must hold: 4 per club at the R16, 5 at the QF, and 5 free
    # transfers into MD13 with nothing carried from MD12.
    out = (3, 4)
    ko = {}
    for kmd in (12, 13):
        ko[kmd] = [
            Projection(pl, Minutes(85, MinutesSource.OBSERVED_UCL), 5.0, 1.0,
                       3.0 + (pl.id % 5) * 0.3 + (2.0 if pl.team_id in out else 0),
                       1, True, "2027-04-07")
            for pl in players if not (kmd == 13 and pl.team_id in out)]
    held: set[int] = set()
    for pos, n in SQUAD_SHAPE.items():   # clubs 3, 4: one player per position
        g = sorted((p for p in ko[12] if p.player.pos == pos),
                   key=lambda p: p.player.team_id not in out)
        held |= {p.player.id for p in g[:n]}
    assert len(held) == SQUAD_SIZE, len(held)
    kp = plan_horizon(ko, current_squad=held, budget={12: 200.0, 13: 200.0},
                      max_per_club={12: 4, 13: 5}, initial_free=3,
                      allow_wildcard=False, time_limit=25)
    a12, a13 = kp.actions
    for a, cap in ((a12, 4), (a13, 5)):
        clubs = [p.player.team_id for p in a.squad]
        assert max(clubs.count(c) for c in set(clubs)) <= cap, (a.matchday, clubs)
    # MD12 is a knockout matchday: its allowance is the rule's 3, not the
    # squad file's carried count
    assert a12.free_available == 3, a12.free_available
    assert a13.free_available == 5 and len(a13.buys) <= 5 + a13.hits
    assert len(a13.xi) == XI_SIZE
    assert len(a13.sells) >= 4, a13.describe()
    assert all(s.player.team_id in out for s in a13.sells), a13.describe()
    assert all(p.points > 0 for p in a13.xi), "a playing owner was available"

    print(f"plan.py self-check OK — {len(plan.actions)} matchdays planned, "
          f"limitless MD{md} (+{gain:.1f}), {len(pairs)} rotation pairs; "
          f"knockout rules hold (MD13 sold {len(a13.sells)} eliminated)")


if __name__ == "__main__":
    demo()
