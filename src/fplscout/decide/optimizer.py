"""MILP optimizer — plan §7 formulation, §Phase4.

Solves ONE gameweek's squad/XI/captain/transfer decision per call, using
horizon-collapsed EV (`total_ev[p] = sum_t decay^t * ev[p][t]` for t=1..horizon,
computed by the caller before calling optimize()) rather than true multi-period
time-indexed variables — this is the plan's own sanctioned fallback ("solve GW+1
transfers exactly with horizon EVs as terminal values... standard approach, plenty
good") for keeping the solve fast and debuggable. Chip timing across future GWs is
chip_planner.py's job (Phase 5); this module decides the single upcoming GW given a
chip_mode flag, per plan §7's "solver re-runs with mode flags, not extra binaries".

Bench has 4 slots, not the plan's literal 3 (`bench_k[p][t] bench slot k in {1,2,3}`,
weights `[0.25, 0.15, 0.05]`): 15-11 = 4 players sit out, and one of those four must
be the backup goalkeeper (FPL only auto-subs a GK for a GK). The backup GK isn't
competing for autosub priority with the 3 outfield bench spots — a real second
keeper only plays if the starter is unexpectedly out, not by bench-order — so it
gets weight ~0 in the objective while the 3 outfield slots keep the plan's exact
weights.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import pulp

POSITIONS = ["GKP", "DEF", "MID", "FWD"]
SQUAD_COUNTS = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
XI_BOUNDS = {"GKP": (1, 1), "DEF": (3, 5), "MID": (2, 5), "FWD": (1, 3)}
XI_SIZE = 11
SQUAD_SIZE = 15
BENCH_WEIGHTS = (0.25, 0.15, 0.05)  # 3 outfield bench slots, per plan §7
BENCH_GK_WEIGHT = 0.0
DEFAULT_HIT_COST = 4
DEFAULT_MAX_PER_CLUB = 3

CHIP_MODES = {None, "wildcard", "free_hit", "bench_boost", "triple_captain"}


@dataclass
class OptimizerInput:
    projections: pd.DataFrame  # columns: code, position, team_id, price, total_ev
    current_squad: set[int]
    purchase_prices: dict[int, int]  # code -> price paid (same units as `price`)
    bank: int  # tenths of a million, e.g. 1000 == £100.0m
    free_transfers: int
    chip_mode: str | None = None
    hit_cost: int = DEFAULT_HIT_COST
    max_per_club: int = DEFAULT_MAX_PER_CLUB
    bench_weights: tuple[float, float, float] = BENCH_WEIGHTS
    time_limit_seconds: float = 30.0


@dataclass
class OptimizationResult:
    status: str
    squad: set[int] = field(default_factory=set)
    starting_xi: set[int] = field(default_factory=set)
    bench_order: list[int] = field(default_factory=list)  # 3 outfield, best-first
    bench_gk: int | None = None
    captain: int | None = None
    vice_captain: int | None = None
    transfers_in: set[int] = field(default_factory=set)
    transfers_out: set[int] = field(default_factory=set)
    hits: int = 0
    objective_value: float = 0.0


def _selling_price(now_price: int, purchase_price: int) -> int:
    """FPL sell-on rule: 50% of profit, rounded down to nearest 0.1m; a loss is
    sold at current (lower) value, no further penalty."""
    if now_price <= purchase_price:
        return now_price
    profit = now_price - purchase_price
    return purchase_price + profit // 2


def optimize(inp: OptimizerInput) -> OptimizationResult:
    if inp.chip_mode not in CHIP_MODES:
        raise ValueError(f"unknown chip_mode {inp.chip_mode!r}")

    df = inp.projections.set_index("code", drop=False)
    codes = list(df.index)
    ev = df["total_ev"].to_dict()
    price = df["price"].to_dict()
    position = df["position"].to_dict()
    team = df["team_id"].to_dict()

    prob = pulp.LpProblem("fplscout_optimize", pulp.LpMaximize)

    squad = pulp.LpVariable.dicts("squad", codes, cat="Binary")
    xi = pulp.LpVariable.dicts("xi", codes, cat="Binary")
    cap = pulp.LpVariable.dicts("cap", codes, cat="Binary")
    vice = pulp.LpVariable.dicts("vice", codes, cat="Binary")
    bench1 = pulp.LpVariable.dicts("bench1", codes, cat="Binary")
    bench2 = pulp.LpVariable.dicts("bench2", codes, cat="Binary")
    bench3 = pulp.LpVariable.dicts("bench3", codes, cat="Binary")
    buy = pulp.LpVariable.dicts("buy", codes, cat="Binary")
    sell = pulp.LpVariable.dicts("sell", codes, cat="Binary")
    hits = pulp.LpVariable("hits", lowBound=0, cat="Integer")

    cap_multiplier = 3 if inp.chip_mode == "triple_captain" else 2
    # captain term below is ev*(xi+cap), i.e. 1x for playing + cap_multiplier-1x
    # extra for captaincy, so total captain contribution = cap_multiplier * ev.
    cap_extra = cap_multiplier - 1

    if inp.chip_mode == "bench_boost":
        bench_weights = (1.0, 1.0, 1.0)
        bench_gk_weight = 1.0
    else:
        bench_weights = inp.bench_weights
        bench_gk_weight = BENCH_GK_WEIGHT

    bench_gk = {c: squad[c] - xi[c] - bench1[c] - bench2[c] - bench3[c] for c in codes}

    prob += (
        pulp.lpSum(ev[c] * (xi[c] + cap_extra * cap[c]) for c in codes)
        + pulp.lpSum(
            bench_weights[0] * ev[c] * bench1[c]
            + bench_weights[1] * ev[c] * bench2[c]
            + bench_weights[2] * ev[c] * bench3[c]
            for c in codes
        )
        + pulp.lpSum(bench_gk_weight * ev[c] * bench_gk[c] for c in codes)
        - inp.hit_cost * hits
    )

    # squad composition
    prob += pulp.lpSum(squad[c] for c in codes) == SQUAD_SIZE
    for pos, count in SQUAD_COUNTS.items():
        prob += pulp.lpSum(squad[c] for c in codes if position[c] == pos) == count

    # XI: subset of squad, formation-legal
    prob += pulp.lpSum(xi[c] for c in codes) == XI_SIZE
    for c in codes:
        prob += xi[c] <= squad[c]
    for pos, (lo, hi) in XI_BOUNDS.items():
        pos_xi = pulp.lpSum(xi[c] for c in codes if position[c] == pos)
        prob += pos_xi >= lo
        prob += pos_xi <= hi

    # bench slots: distinct, subset of squad minus xi, each player at most one slot
    for c in codes:
        prob += bench1[c] + bench2[c] + bench3[c] <= squad[c] - xi[c]
    prob += pulp.lpSum(bench1[c] for c in codes) == 1
    prob += pulp.lpSum(bench2[c] for c in codes) == 1
    prob += pulp.lpSum(bench3[c] for c in codes) == 1
    for c in codes:
        prob += bench_gk[c] >= 0
        prob += bench_gk[c] <= 1

    # captain / vice: both in XI, distinct
    prob += pulp.lpSum(cap[c] for c in codes) == 1
    prob += pulp.lpSum(vice[c] for c in codes) == 1
    for c in codes:
        prob += cap[c] <= xi[c]
        prob += vice[c] <= xi[c]
        prob += cap[c] + vice[c] <= 1

    # club cap
    clubs = set(team.values())
    for club in clubs:
        prob += (
            pulp.lpSum(squad[c] for c in codes if team[c] == club) <= inp.max_per_club
        )

    # transfer flow: squad[c] - was_owned[c] == buy[c] - sell[c]
    was_owned = {c: 1 if c in inp.current_squad else 0 for c in codes}
    for c in codes:
        prob += squad[c] - was_owned[c] == buy[c] - sell[c]
        if was_owned[c] == 0:
            prob += sell[c] == 0
        else:
            prob += buy[c] == 0

    total_buys = pulp.lpSum(buy[c] for c in codes)
    if inp.chip_mode in ("wildcard", "free_hit"):
        prob += hits == 0
    else:
        prob += hits >= total_buys - inp.free_transfers

    # budget: cost of squad <= bank + proceeds from sold players + value already
    # tied up in retained players (retained players' current price counts as
    # already "spent", so budget constraint is: new spend on bought players <=
    # bank + selling proceeds of departed players).
    sale_value = pulp.lpSum(
        _selling_price(price[c], inp.purchase_prices.get(c, price[c])) * sell[c]
        for c in codes
        if was_owned[c] == 1
    )
    buy_cost = pulp.lpSum(price[c] * buy[c] for c in codes if was_owned[c] == 0)
    prob += buy_cost <= inp.bank + sale_value

    solver = pulp.HiGHS(msg=False, timeLimit=inp.time_limit_seconds)
    prob.solve(solver)
    status = pulp.LpStatus[prob.status]

    if status != "Optimal":
        return OptimizationResult(status=status)

    def _on(var_dict: dict) -> set[int]:
        return {c for c in codes if var_dict[c].value() and var_dict[c].value() > 0.5}

    squad_set = _on(squad)
    xi_set = _on(xi)
    bench1_set = _on(bench1)
    bench2_set = _on(bench2)
    bench3_set = _on(bench3)
    bench_gk_set = {c for c in codes if bench_gk[c].value() and bench_gk[c].value() > 0.5}

    return OptimizationResult(
        status=status,
        squad=squad_set,
        starting_xi=xi_set,
        bench_order=[
            next(iter(bench1_set), None),
            next(iter(bench2_set), None),
            next(iter(bench3_set), None),
        ],
        bench_gk=next(iter(bench_gk_set), None),
        captain=next(iter(_on(cap)), None),
        vice_captain=next(iter(_on(vice)), None),
        transfers_in=_on(buy),
        transfers_out=_on(sell),
        hits=int(round(hits.value() or 0)),
        objective_value=pulp.value(prob.objective) or 0.0,
    )


@dataclass
class AlternativeMove:
    out_code: int
    in_code: int
    position: str
    net_ev: float  # ev gain minus hit cost if this swap would need one
    would_need_hit: bool


def top_alternative_moves(
    projections: pd.DataFrame,
    current_squad: set[int],
    purchase_prices: dict[int, int],
    bank: int,
    free_transfers: int,
    hit_cost: int = DEFAULT_HIT_COST,
    n: int = 5,
) -> list[AlternativeMove]:
    """Single-swap suggestions ranked by net EV — "Palmer IN +8.4" per plan §7,
    the human-readable margin on top of the optimizer's actual (possibly
    multi-swap) recommendation. Deliberately simple enumeration, not another
    MILP solve: for each (owned player, same-position replacement) pair that
    fits the budget, net_ev = ev gain, minus a hit cost if this single swap
    would exceed the free transfers available. This ranks *individual* swaps in
    isolation (their combination is what optimize() actually solves for), which
    is what the UI mock's per-move EV delta shows.
    """
    df = projections.set_index("code", drop=False)
    owned = df.loc[df.index.isin(current_squad)]
    candidates = df.loc[~df.index.isin(current_squad)]

    moves: list[AlternativeMove] = []
    for out_code, out_row in owned.iterrows():
        position = out_row["position"]
        purchase_price = purchase_prices.get(out_code, out_row["price"])
        sale_value = _selling_price(int(out_row["price"]), int(purchase_price))
        available_budget = bank + sale_value
        same_position = candidates[candidates["position"] == position]
        affordable = same_position[same_position["price"] <= available_budget]
        for in_code, in_row in affordable.iterrows():
            ev_gain = float(in_row["total_ev"] - out_row["total_ev"])
            would_need_hit = free_transfers < 1
            net_ev = ev_gain - (hit_cost if would_need_hit else 0)
            moves.append(
                AlternativeMove(
                    out_code=out_code,
                    in_code=in_code,
                    position=position,
                    net_ev=net_ev,
                    would_need_hit=would_need_hit,
                )
            )
    moves.sort(key=lambda m: m.net_ev, reverse=True)
    return moves[:n]
