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

from fplscout.decide.optimizer import OptimizationResult, OptimizerInput, optimize

# A wildcard's value is the squad you keep afterwards, so it is scored over this
# many weeks from the candidate GW. Fixed, so every candidate is judged on the
# same span (a truncated span would favour early weeks by construction).
WILDCARD_LOOKAHEAD = 4


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
) -> dict[tuple[str, int], float]:
    """Drives the optimizer across every feasible candidate GW in each chip's
    window. Expensive (one solve per candidate GW, some chips need two solves to
    get a delta) — this is what Phase 6's backtest calls once per GW using the
    projections it already has for that GW, not a full standalone horizon scan.
    """
    ev_by_chip_gw: dict[tuple[str, int], float] = {}
    for window in chip_windows:
        for gw in range(window.start_gw, window.stop_gw + 1):
            if gw not in projections_by_gw:
                continue
            gw_projections = projections_by_gw[gw]
            ev_by_code = dict(
                zip(gw_projections["code"], gw_projections["total_ev"], strict=True)
            )
            normal_input = OptimizerInput(
                projections=gw_projections,
                current_squad=base_input.current_squad,
                purchase_prices=base_input.purchase_prices,
                bank=base_input.bank,
                free_transfers=base_input.free_transfers,
                chip_mode=None,
            )
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
                fh_input = OptimizerInput(**{**vars(normal_input), "chip_mode": "free_hit"})
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
