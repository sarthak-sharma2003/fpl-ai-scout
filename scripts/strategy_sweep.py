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


def run(season: str) -> None:
    con = duckdb.connect(str(DB), read_only=True)
    prepared = prepare_season(con, season, SEASONS[season])
    rules = make_rules(load_history(con, season))
    out = {}
    for name, rule_names in STRATEGIES.items():
        r = simulate_season(
            con, season, SEASONS[season], prepared=prepared,
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
    base = "baseline (model)"
    rows = []
    for name in STRATEGIES:
        totals = [sum(data[s][name]["gw_scores"]) for s in SEASONS]
        diffs = [a - b for s in SEASONS
                 for a, b in zip(data[s][name]["gw_scores"], data[s][base]["gw_scores"], strict=True)]
        mean_d = sum(diffs) / len(diffs)
        sd = (sum((d - mean_d) ** 2 for d in diffs) / (len(diffs) - 1)) ** 0.5
        hits = [data[s][name]["hits"] for s in SEASONS]
        rows.append((name, totals, sum(totals) / len(totals), mean_d, sd / len(diffs) ** 0.5, hits))
    lines = ["| strategy | " + " | ".join(SEASONS) + " | mean | vs base /GW (±1 se) | hits |",
             "|---|" + "---|" * (len(SEASONS) + 4)]
    for name, totals, mean, d, se, hits in sorted(rows, key=lambda r: -r[2]):
        lines.append(f"| {name} | " + " | ".join(map(str, totals))
                     + f" | {mean:.0f} | {d:+.2f} (±{se:.2f}) | {'/'.join(map(str, hits))} |")
    print("\n".join(lines))


if __name__ == "__main__":
    if sys.argv[1:] == ["--report"]:
        report()
    else:
        for season in sys.argv[1:] or SEASONS:
            run(season)
