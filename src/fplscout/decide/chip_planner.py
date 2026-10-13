"""Chip planner — plan §Phase5.

DGW/BGW detection (plan §6.7: "trivial — count fixtures per team per GW") plus four
chip EV estimators and a scheduler. Chip *windows* (which GWs each chip is valid in)
are read from the live API's `chips` array at call time, never hardcoded — the plan
explicitly warns rules can change again at the 26/27 reset (§0.2), and grounding
against the live 25/26 API on 2026-07-08 already showed bench boost/triple captain
are valid from GW1 while wildcard/free hit only from GW2, a distinction hardcoding
would have missed.

This module is split into two layers:
- Pure EV/scheduling functions (bench_boost_ev, triple_captain_ev, free_hit_ev,
  wildcard_ev, plan_chips, chip_alert) — no optimizer calls, fully unit-testable.
- evaluate_chip_windows(), which DOES drive the optimizer across every feasible
  candidate GW in a chip's window. That's the expensive part (one solve per
  candidate GW per chip) and is exercised for real by Phase 6's backtest, which
  already has the per-GW projection loop this needs to reuse.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from fplscout.decide.optimizer import (
    XI_BOUNDS,
    XI_SIZE,
    OptimizationResult,
    OptimizerInput,
    _selling_price,
    optimize,
)

# A wildcard's value is the squad you keep afterwards, so it is scored over this
# many weeks from the candidate GW. Fixed, so every candidate is judged on the
# same span (a truncated span would favour early weeks by construction).
# Kept for the single-solve comparator `wildcard_ev`; evaluate_chip_windows now
# prices the wildcard by simulating both paths (simulate_wildcard_gains).
WILDCARD_LOOKAHEAD = 4

# FPL since 2024-25: free transfers bank up to five, and a wildcard or free hit
# keeps the ones you had banked.
MAX_BANKED_FT = 5
REAL_HIT_COST = 4.0

# What a chip has to be worth before the weekly check says "play it now".
# Judgement calls, NOT swept constants (the replay has no chip decisions to tune
# them on): each sits at what a good-but-not-exceptional week for that chip is
# worth in this model's units, so the advice waits for a real opportunity
# instead of firing on the first legal week. The wildcard's matches
# cli.WILDCARD_MIN_GAIN. They drop to zero once the chips still unused need
# every remaining week of their window (see chip_verdicts), because an expired
# chip is worth nothing.
PLAY_BARS = {"wildcard": 15.0, "freehit": 12.0, "bboost": 15.0, "3xc": 8.0}


def dgw_bgw_counts(fixtures: pd.DataFrame) -> pd.DataFrame:
    """One row per (season, gw, team_id) with its fixture count that gameweek.
    is_dgw = count >= 2; is_bgw is NOT derivable from this table alone (a team
    with zero fixtures that GW has no row at all) — compute it by comparing
    against the full set of teams for that season instead (see bgw_teams())."""
    long = pd.concat(
        [
            fixtures[["season", "event", "team_h"]].rename(columns={"team_h": "team_id"}),
            fixtures[["season", "event", "team_a"]].rename(columns={"team_a": "team_id"}),
        ],
        ignore_index=True,
    ).rename(columns={"event": "gw"})
    counts = long.groupby(["season", "gw", "team_id"]).size().reset_index(name="n_fixtures")
    counts["is_dgw"] = counts["n_fixtures"] >= 2
    return counts


def bgw_teams(fixtures: pd.DataFrame, season: str, gw: int, all_team_ids: set[int]) -> set[int]:
    """Teams with zero fixtures in (season, gw)."""
    counts = dgw_bgw_counts(fixtures)
    playing = set(
        counts.loc[(counts["season"] == season) & (counts["gw"] == gw), "team_id"]
    )
    return all_team_ids - playing


@dataclass
class ChipWindow:
    chip: str  # "wildcard" | "freehit" | "bboost" | "3xc"
    start_gw: int
    stop_gw: int


def chip_windows_from_bootstrap_chips(chips: list) -> list[ChipWindow]:
    """chips: BootstrapStatic.chips (or any object with .name/.start_event/.stop_event).
    Read at call time from the live API, never hardcoded — see module docstring."""
    return [
        ChipWindow(chip=c.name, start_gw=c.start_event, stop_gw=c.stop_event) for c in chips
    ]


def bench_boost_ev(result: OptimizationResult, ev_by_code: dict[int, float]) -> float:
    """EV = full projected points of the entire bench (3 outfield + backup GK) —
    what bench_boost mode's objective already values at full weight, extracted
    here as a human-readable number for the report/alert."""
    bench_codes = [c for c in result.bench_order if c is not None]
    if result.bench_gk is not None:
        bench_codes.append(result.bench_gk)
    return sum(ev_by_code.get(c, 0.0) for c in bench_codes)


def triple_captain_ev(result: OptimizationResult, ev_by_code: dict[int, float]) -> float:
    """EV = the *extra* uplift from 3x instead of the normal 2x captain multiplier
    (i.e. one more multiple of the captain's ev), not the captain's total score."""
    if result.captain is None:
        return 0.0
    return ev_by_code.get(result.captain, 0.0)


def free_hit_ev(free_hit_result: OptimizationResult, normal_result: OptimizationResult) -> float:
    """EV = unconstrained best XI that GW minus our actual (transfer-constrained)
    XI that GW — best in blank gameweeks, per plan §5."""
    return free_hit_result.objective_value - normal_result.objective_value


def wildcard_ev(wildcard_result: OptimizationResult, normal_result: OptimizationResult) -> float:
    """EV = horizon value of a fully re-optimized squad minus the current squad's
    trajectory value, per plan §5."""
    return wildcard_result.objective_value - normal_result.objective_value


@dataclass
class ChipRecommendation:
    chip: str
    gw: int
    ev: float


def plan_chips(
    chip_windows: list[ChipWindow], ev_by_chip_gw: dict[tuple[str, int], float]
) -> list[ChipRecommendation]:
    """Assign each chip window a GW inside it, at most one chip per GW (FPL
    rule), maximising total EV: a max-weight matching of windows to GWs.
    Picking each chip's best week independently put TC, FH and WC all on GW8.
    Every window with a candidate gets a week, since first-half chips are lost
    at the GW19 deadline. `ev_by_chip_gw` is precomputed by the caller, so this
    is testable without the optimizer."""
    cands = [
        {gw: ev for (chip, gw), ev in ev_by_chip_gw.items()
         if chip == w.chip and w.start_gw <= gw <= w.stop_gw}
        for w in chip_windows
    ]
    rows = [i for i, c in enumerate(cands) if c]
    gws = sorted({gw for c in cands for gw in c})
    if not rows:
        return []
    invalid = -1e9
    score = np.full((len(rows), len(gws)), invalid)
    for r, i in enumerate(rows):
        for gw, ev in cands[i].items():
            score[r, gws.index(gw)] = ev
    return [
        ChipRecommendation(chip=chip_windows[rows[r]].chip, gw=gws[c], ev=float(score[r, c]))
        for r, c in zip(*linear_sum_assignment(score, maximize=True), strict=True)
        if score[r, c] > invalid
    ]


def chip_alert(current_gw: int, current_week_ev: float, planned: ChipRecommendation) -> bool:
    """Fire the chip THIS gameweek instead of waiting for its planned gameweek —
    true when we're already at the planned gw, or a better opportunity has shown
    up now than what's still projected for later (extreme fixture, unexpected
    DGW news, etc.)."""
    if current_gw == planned.gw:
        return True
    if current_gw > planned.gw:
        return False  # missed window, caller should re-plan
    return current_week_ev > planned.ev


def evaluate_chip_windows(
    chip_windows: list[ChipWindow],
    projections_by_gw: dict[int, pd.DataFrame],
    base_input: OptimizerInput,
    decay: float = 0.84,
    static: pd.DataFrame | None = None,
    lookahead: int | None = None,
) -> dict[tuple[str, int], float]:
    """Drives the optimizer across every feasible candidate GW in each chip's
    window. Expensive (one solve per candidate GW, some chips need two solves to
    get a delta) — this is what Phase 6's backtest calls once per GW using the
    projections it already has for that GW, not a full standalone horizon scan.
    """
    ev_by_chip_gw: dict[tuple[str, int], float] = {}
    wc_gains: dict[int, float] | None = None
    for window in chip_windows:
        if window.chip == "wildcard":
            if wc_gains is None:
                wc_gains = simulate_wildcard_gains(projections_by_gw, base_input,
                                                   decay=decay, static=static,
                                                   lookahead=lookahead)
            for gw, gain in wc_gains.items():
                if window.start_gw <= gw <= window.stop_gw:
                    ev_by_chip_gw[("wildcard", gw)] = gain
            continue
        for gw in range(window.start_gw, window.stop_gw + 1):
            if gw not in projections_by_gw:
                continue
            gw_projections = projections_by_gw[gw]
            ev_by_code = dict(
                zip(gw_projections["code"], gw_projections["total_ev"], strict=True)
            )
            # the caller's transfer rules (hit cost, penalty, hit cap) carry
            # through, so chip and no-chip weeks are judged the same way
            normal_input = replace(base_input, projections=gw_projections, chip_mode=None)
            normal_result = optimize(normal_input)
            if normal_result.status != "Optimal":
                continue

            if window.chip == "bboost":
                boosted_input = OptimizerInput(
                    **{**vars(normal_input), "chip_mode": "bench_boost"}
                )
                boosted_result = optimize(boosted_input)
                ev = bench_boost_ev(boosted_result, ev_by_code)
            elif window.chip == "3xc":
                ev = triple_captain_ev(normal_result, ev_by_code)
            elif window.chip == "freehit":
                fh_input = replace(normal_input, chip_mode="free_hit",
                                   penalize_chip_transfers=True)
                fh_result = optimize(fh_input)
                ev = free_hit_ev(fh_result, normal_result)
            elif window.chip == "wildcard":
                # Same like-for-like comparison as cli.optimize: the rebuild vs
                # the weekly free transfers you'd get anyway over the same span,
                # neither paying the churn penalty. Scoring it on one week made
                # it a free hit (identical EV, same GW).
                span = [projections_by_gw.get(w) for w in range(gw, gw + WILDCARD_LOOKAHEAD)]
                if any(f is None for f in span):
                    continue
                span_input = replace(
                    normal_input,
                    projections=pd.concat(span)
                    .groupby(["code", "position", "team_id", "price"], as_index=False)["total_ev"]
                    .sum(),
                )
                wc_result = optimize(replace(span_input, chip_mode="wildcard"))
                rolling = optimize(
                    replace(span_input, free_transfers=WILDCARD_LOOKAHEAD, transfer_penalty=0.0)
                )
                if wc_result.status != "Optimal" or rolling.status != "Optimal":
                    continue
                ev = wildcard_ev(wc_result, rolling)
            else:
                continue
            ev_by_chip_gw[(window.chip, gw)] = ev
    return ev_by_chip_gw


# --------------------------------------------------------------------------
# The wildcard, priced by simulating both paths.
#
# The single-solve comparator (wildcard_ev against "N free transfers at once")
# is biased whichever N it uses: N = the horizon (as cli.optimize did) hands the
# no-wildcard path eight transfers in week one, each scoring for all eight
# weeks -- so a wildcard gained ~+2 on almost any squad -- while N = 4 over four
# weeks inflates it. Real free transfers arrive one a week, so the k-th one
# only scores for the weeks after it. Simulating both paths week by week (the
# same optimizer, one free transfer a week, real hit costs) is the comparison
# the decision is actually about.


_STATIC = ["code", "position", "team_id", "price"]


def _decision_frame(projections_by_gw: dict[int, pd.DataFrame], gws: list[int],
                    k: int, decay: float, owned: set[int],
                    static: pd.DataFrame, lookahead: int | None = None) -> pd.DataFrame:
    """The optimizer's input at gws[k]: each player's EV over the next
    `lookahead` weeks of the path (all of them by default), decayed from that
    week -- what total_ev is for the live decision. Owned players with no
    projection are carried at zero EV, as cli.optimize does: a player absent
    from the MILP would be dropped for free instead of costing a transfer."""
    parts = []
    ahead = gws[k:] if lookahead is None else gws[k:k + lookahead]
    for j, g in enumerate(ahead):
        f = projections_by_gw[g][[*_STATIC, "total_ev"]].copy()
        f["total_ev"] = f["total_ev"] * decay**j
        parts.append(f)
    frame = (pd.concat(parts)
             .groupby(_STATIC, as_index=False)["total_ev"]
             .sum())
    missing = owned - set(frame["code"])
    if missing:
        fill = static[static["code"].isin(missing)].drop_duplicates("code").copy()
        fill["total_ev"] = 0.0
        frame = pd.concat([frame, fill[[*_STATIC, "total_ev"]]], ignore_index=True)
    return frame


def week_points(frame: pd.DataFrame, squad: set[int]) -> float:
    """Best legal XI plus captain from `squad` on one week's EV. Greedy is exact
    here: each position's minimum from its best players, then the remaining
    places from the best of the rest under each maximum."""
    own = frame[frame["code"].isin(squad)].sort_values("total_ev", ascending=False)
    xi: list[float] = []
    count = dict.fromkeys(XI_BOUNDS, 0)
    taken: set[int] = set()
    for pos, (lo, _) in XI_BOUNDS.items():
        for row in own[own["position"] == pos].head(lo).itertuples():
            xi.append(row.total_ev)
            taken.add(row.code)
            count[pos] += 1
    for row in own.itertuples():
        if len(xi) >= XI_SIZE:
            break
        if row.code in taken or count[row.position] >= XI_BOUNDS[row.position][1]:
            continue
        xi.append(row.total_ev)
        taken.add(row.code)
        count[row.position] += 1
    return float(sum(xi) + (max(xi) if xi else 0.0))


@dataclass
class _PathState:
    squad: set[int]
    purchase_prices: dict[int, int]
    bank: int
    free_transfers: int


def _step(projections_by_gw, gws, k, state: _PathState, base_input: OptimizerInput,
          decay: float, chip_mode: str | None, static: pd.DataFrame,
          lookahead: int | None = None) -> tuple[float, _PathState] | None:
    frame = _decision_frame(projections_by_gw, gws, k, decay, state.squad, static,
                            lookahead)
    res = optimize(replace(
        base_input, projections=frame, current_squad=state.squad,
        purchase_prices=state.purchase_prices, bank=state.bank,
        free_transfers=state.free_transfers, chip_mode=chip_mode,
        penalize_chip_transfers=True,
    ))
    if res.status != "Optimal":
        return None
    # Every move, the wildcard's included, pays the weekly advice's per-transfer
    # penalty: a move is worth counting only if its edge clears model noise.
    pts = (week_points(projections_by_gw[gws[k]], res.squad) - REAL_HIT_COST * res.hits
           - base_input.transfer_penalty * len(res.transfers_in))
    price = dict(zip(frame["code"], frame["price"], strict=True))
    sale = sum(_selling_price(int(price[c]), int(state.purchase_prices.get(c, price[c])))
               for c in res.transfers_out if c in price)
    cost = sum(int(price[c]) for c in res.transfers_in)
    pp = {c: v for c, v in state.purchase_prices.items() if c not in res.transfers_out}
    pp.update({c: int(price[c]) for c in res.transfers_in})
    ft = (state.free_transfers if chip_mode in ("wildcard", "free_hit")
          else max(0, state.free_transfers - len(res.transfers_in)))
    return pts, _PathState(set(res.squad), pp, int(state.bank + sale - cost),
                           min(MAX_BANKED_FT, ft + 1))


def simulate_wildcard_gains(
    projections_by_gw: dict[int, pd.DataFrame],
    base_input: OptimizerInput,
    decay: float = 0.84,
    max_start_weeks: int | None = None,
    static: pd.DataFrame | None = None,
    lookahead: int | None = None,
) -> dict[int, float]:
    """gw -> what playing the wildcard in that gw gains over not playing it,
    both paths then spending one free transfer a week through the end of the
    projected weeks. Decayed from the first week, like everything the
    optimizer compares. The no-wildcard path is simulated once; each candidate
    week branches off its state (n + n(n+1)/2 solves for n weeks).

    `static` ({code, position, team_id, price}) prices owned players that have
    no projection in some week; by default whatever the weekly frames hold.
    `lookahead` is how many weeks each simulated decision sees (the live
    optimizer's horizon); by default the rest of the path."""
    gws = sorted(projections_by_gw)
    if not gws or not base_input.current_squad:
        return {}
    if static is None:
        static = pd.concat([projections_by_gw[g][_STATIC] for g in gws])
    static = static[_STATIC].dropna()
    state = _PathState(set(base_input.current_squad), dict(base_input.purchase_prices),
                       int(base_input.bank), int(base_input.free_transfers))
    states, base_pts = [], []
    for k in range(len(gws)):
        states.append(state)
        r = _step(projections_by_gw, gws, k, state, base_input, decay, None, static,
                  lookahead)
        if r is None:
            break
        pts, state = r
        base_pts.append(pts)
    n = len(base_pts)
    starts = range(n if max_start_weeks is None else min(n, max_start_weeks))
    gains: dict[int, float] = {}
    for t in starts:
        st, total = states[t], 0.0
        for k in range(t, n):
            r = _step(projections_by_gw, gws, k, st, base_input, decay,
                      "wildcard" if k == t else None, static, lookahead)
            if r is None:
                total = None
                break
            pts, st = r
            total += decay**k * (pts - base_pts[k])
        if total is not None:
            gains[gws[t]] = float(total)
    return gains


# --------------------------------------------------------------------------
# The weekly verdict per chip: checked every run, published whatever it says.


def chip_verdicts(
    current_gw: int,
    windows: list[ChipWindow],
    ev_by_chip_gw: dict[tuple[str, int], float],
    plan: list[ChipRecommendation],
    bars: dict[str, float] | None = None,
) -> dict[tuple[str, int, int], dict]:
    """Play now, planned for later, or hold -- for each unused window, keyed
    (chip, start_gw, stop_gw). `windows` are the chips still unused.

    "play" needs both: this week is where plan_chips puts the chip (no better
    week for it in sight, one chip a week), and its value clears the chip's
    bar. The bar shrinks as the window runs out of spare weeks -- full while
    the weeks left are at least twice the unused chips that must fit into
    them, zero when there are no more weeks than chips -- because an expired
    chip is worth nothing, so a modest week then beats waiting.
    """
    bars = PLAY_BARS if bars is None else bars
    out: dict[tuple[str, int, int], dict] = {}
    for w in windows:
        if w.stop_gw < current_gw:
            continue
        cands = {gw: ev for (chip, gw), ev in ev_by_chip_gw.items()
                 if chip == w.chip and w.start_gw <= gw <= w.stop_gw}
        rec = next((r for r in plan if r.chip == w.chip
                    and w.start_gw <= r.gw <= w.stop_gw), None)
        weeks_left = w.stop_gw - max(current_gw, w.start_gw) + 1
        sharing = sum(1 for o in windows if o.stop_gw == w.stop_gw)
        spare = max(0.0, min(1.0, (weeks_left - sharing) / max(1, sharing)))
        bar = round(bars.get(w.chip, 0.0) * spare, 1)
        best_gw = max(cands, key=cands.get) if cands else None
        v = {
            "this_week_ev": round(cands[current_gw], 1) if current_gw in cands else None,
            "best_gw": best_gw,
            "best_ev": round(cands[best_gw], 1) if best_gw is not None else None,
            "planned_gw": rec.gw if rec else None,
            "bar": bar,
            "weeks_left": weeks_left,
        }
        if current_gw < w.start_gw:
            v["advice"] = "not_yet"
        elif rec is not None and rec.gw == current_gw and rec.ev >= bar:
            v["advice"] = "play"
        elif rec is not None and rec.gw > current_gw and rec.ev >= bar:
            v["advice"] = "planned"
        else:
            v["advice"] = "hold"
        out[(w.chip, w.start_gw, w.stop_gw)] = v
    return out
