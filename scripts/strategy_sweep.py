"""Backtest backtest/strategies.py STRATEGIES over three replay seasons.

    .venv/bin/python scripts/strategy_sweep.py [season ...]

Writes per-GW scores to data/reports/strategy_sweep_<season>.json. Seasons run
in separate processes (one per arg), then `--report` merges them.
"""

import json
import sys
from pathlib import Path

import duckdb

from fplscout.backtest.simulator import prepare_season, simulate_season
from fplscout.backtest.strategies import STRATEGIES, load_history, make_rules

REPO = Path(__file__).resolve().parents[1]
DB = REPO / "data" / "fpl.duckdb"
OUT = REPO / "data" / "reports"
SEASONS = {
    "2023-24": ["2021-22", "2022-23"],
    "2024-25": ["2021-22", "2022-23", "2023-24"],
    "2025-26": ["2021-22", "2022-23", "2023-24", "2024-25"],
}
# The live season so far: real results for the GWs played. Shown beside, never
# pooled into the mean -- 5 GWs is far too short to rank anything on its own.
LIVE = {"2026-27": ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]}
MY_ENTRY = 5874404


def run(season: str) -> None:
    con = duckdb.connect(str(DB), read_only=True)
    train = {**SEASONS, **LIVE}[season]
    prepared = prepare_season(con, season, train)
    rules = make_rules(load_history(con, season))
    out = {}
    for name, rule_names in STRATEGIES.items():
        r = simulate_season(
            con, season, train, prepared=prepared,
            rules=tuple(rules[n] for n in rule_names),
        )
        out[name] = {
            "gw_scores": [g.gw_score for g in r.gw_results],
            "hits": sum(g.hits for g in r.gw_results),
            "transfers": sum(len(g.transfers_in) for g in r.gw_results[1:]),
        }
        print(f"{season} {name}: {r.total_points}", flush=True)
    (OUT / f"strategy_sweep_{season}.json").write_text(json.dumps(out))


def report() -> None:
    data = {s: json.loads((OUT / f"strategy_sweep_{s}.json").read_text()) for s in SEASONS}
    live = {s: json.loads(p.read_text()) for s in LIVE
            if (p := OUT / f"strategy_sweep_{s}.json").exists()}
    base = "baseline (model)"
    rows = []
    for name in STRATEGIES:
        # Strategies added after the historical sweep was last run have no
        # history yet: show them on the live season alone rather than crash
        # (2026-10-13: the weekly job died on "starts weight 0.25").
        hist = [s for s in SEASONS if name in data[s]]
        totals = [sum(data[s][name]["gw_scores"]) if name in data[s] else None for s in SEASONS]
        diffs = [a - b for s in hist
                 for a, b in zip(data[s][name]["gw_scores"], data[s][base]["gw_scores"],
                                 strict=True)]
        mean_d = sum(diffs) / len(diffs) if diffs else None
        se = ((sum((d - mean_d) ** 2 for d in diffs) / (len(diffs) - 1)) ** 0.5
              / len(diffs) ** 0.5 if len(diffs) > 1 else None)
        wins = sum(sum(data[s][name]["gw_scores"]) > sum(data[s][base]["gw_scores"]) for s in hist)
        live_tot = [sum(live[s][name]["gw_scores"]) if name in live[s] else None for s in live]
        mean = sum(t for t in totals if t is not None) / len(hist) if hist else None
        rows.append((name, totals, mean, mean_d, se, wins, len(hist), live_tot))
    cols = list(SEASONS) + [f"{s} GW1-{len(live[s][base]['gw_scores'])}" for s in live]
    lines = ["| strategy | " + " | ".join(cols)
             + " | mean (hist) | vs base /GW (±1 se) | seasons won |",
             "|---|" + "---|" * (len(cols) + 3)]
    fmt = lambda v: "—" if v is None else str(int(v))  # noqa: E731
    for name, totals, mean, d, se, wins, n_hist, live_tot in sorted(
            rows, key=lambda r: -(r[2] if r[2] is not None else -1e9)):
        vs = "—" if d is None else f"{d:+.2f} (±{se:.2f})" if se is not None else f"{d:+.2f}"
        lines.append(f"| {name} | " + " | ".join(fmt(t) for t in totals + live_tot)
                     + f" | {'—' if mean is None else f'{mean:.0f}'} | {vs} | "
                     + (f"{wins}/{n_hist}" if n_hist else "no history yet") + " |")
    if live:
        con = duckdb.connect(str(DB), read_only=True)
        for s in live:
            n = len(live[s][base]["gw_scores"])
            mine = con.execute(
                "SELECT total_points FROM rival_gw WHERE season = ? AND entry_id = ? AND gw = ?",
                [s, MY_ENTRY, n],
            ).fetchone()
            if mine:
                lines.append(f"\nYour real team, {s} GW1-{n}: {mine[0]} (net of hits)")
    print("\n".join(lines))


if __name__ == "__main__":
    if sys.argv[1:] == ["--report"]:
        report()
    else:
        for season in sys.argv[1:] or {**SEASONS, **LIVE}:
            run(season)
