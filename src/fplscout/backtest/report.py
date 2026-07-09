"""Backtest report — plan §Phase6: total points, GW-by-GW, vs benchmarks.

Provenance note, corrected from an earlier version of this report: this module
previously claimed to have "checked" that vaastav's `xP` column doesn't leak
post-match information (based on 0-minute players still carrying nonzero
pre-match-looking xP) and left the anomalously high backtest totals as an open
question. That check was insufficient — vaastav's own README documents `xP` as
scraped from bootstrap-static's `ep_this` *after* each gameweek ends, with an
empirically observed same-GW correlation to actual points the README itself calls
"unusually high for a genuinely pre-match feature". The column has been removed
from all historical training (models/points.py, models/dataset.py); these backtest
numbers are from models that never saw it.
"""

from __future__ import annotations

from fplscout.backtest.simulator import SeasonResult

REAL_2025_26_AVERAGE_MANAGER = 1895  # summed live bootstrap-static average_entry_score,
# all 38 gameweeks — grounded, not the plan's estimated "~2,200-2,300" (see report notes)
PLAN_STATED_TARGET = 2400


def to_summary_dict(results: list[SeasonResult]) -> dict:
    """Structured counterpart to render_report(), for the static site's
    analytics.json (publish.py) — avoids re-running the (expensive, full
    season-replay) backtest just to read numbers already computed."""
    seasons = []
    for r in results:
        total_hits = sum(g.hits for g in r.gw_results)
        chips = [{"gw": g.gw, "chip": g.chip_used} for g in r.gw_results if g.chip_used]
        seasons.append(
            {
                "season": r.season,
                "train_seasons": r.train_seasons,
                "total_points": round(r.total_points),
                "total_hits": total_hits,
                "chips_used": chips,
                "gw_scores": [
                    {"gw": g.gw, "score": round(g.gw_score), "hits": g.hits}
                    for g in r.gw_results
                ],
            }
        )
    return {
        "seasons": seasons,
        "plan_stated_target": PLAN_STATED_TARGET,
        "real_2025_26_average_manager": REAL_2025_26_AVERAGE_MANAGER,
    }


def render_report(results: list[SeasonResult]) -> str:
    lines = [
        "# Phase 6 backtest report",
        "",
        "Models trained with `fpl_xp` removed entirely (see module docstring) and",
        "with the leak-safe horizon-EV fix (models/train.py, backtest/simulator.py",
        "git history) — no per-future-gameweek rolling-feature peeking, no",
        "hit-cost margin tuned toward a target number.",
        "",
        "## Headline numbers",
        "",
        "| Season | Train seasons | Total points | Total hits | Chips used |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        total_hits = sum(g.hits for g in r.gw_results)
        chips = [f"GW{g.gw}:{g.chip_used}" for g in r.gw_results if g.chip_used]
        lines.append(
            f"| {r.season} | {r.train_seasons} | {r.total_points:.0f} | {total_hits} "
            f"| {', '.join(chips) if chips else 'none'} |"
        )
    lines += [
        "",
        f"Plan's stated go/no-go bar: >= {PLAN_STATED_TARGET} pts. Real grounded "
        f"2025-26 average-manager benchmark (summed live `average_entry_score` "
        f"across all 38 finished gameweeks): {REAL_2025_26_AVERAGE_MANAGER} pts — "
        f"notably below the plan's estimated ~2,200-2,300. No equivalent live figure "
        f"is available for 2024-25 (the API only exposes per-GW averages for the "
        f"most recently finished season), so that season is reported against the "
        f"plan's stated target only. Per review: with `ep_next` gone from historical "
        f"replay, judge these totals against real benchmarks (average manager, "
        f"top-10k cutoff where obtainable) rather than the single 2,400 scalar — "
        f"that gate was set assuming a legitimate xP feature.",
        "",
        "## GW-by-GW",
        "",
    ]
    for r in results:
        lines.append(f"### {r.season}")
        lines.append("")
        lines.append("| GW | Score | Hits | Chip | Bank | FT |")
        lines.append("|---|---|---|---|---|---|")
        for g in r.gw_results:
            lines.append(
                f"| {g.gw} | {g.gw_score:.0f} | {g.hits} | {g.chip_used or ''} "
                f"| {g.bank} | {g.free_transfers} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"
