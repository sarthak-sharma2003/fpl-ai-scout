"""Aggregate backtest_grid.py outputs into one markdown table.

    python scripts/backtest_report.py DIR > report.md

Per config: each season's total, the mean over seasons, and the paired
per-gameweek difference against the baseline config (empty prep, empty cfg)
with its standard error -- the number to read, since season totals carry
~+-60 points of decision-path noise (ROADMAP §2.5).
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def main(d: str) -> None:
    rows: dict[str, dict[str, dict]] = defaultdict(dict)
    for f in sorted(Path(d).glob("**/*.json")):
        data = json.loads(f.read_text())
        for r in data.get("results", []):
            key = json.dumps({"prep": r["prep"], "cfg": r["cfg"]}, sort_keys=True)
            rows[key][data["season"]] = r
    base_key = json.dumps({"prep": {}, "cfg": {}}, sort_keys=True)
    base = rows.get(base_key, {})
    seasons = sorted({s for v in rows.values() for s in v})
    out = ["| config | " + " | ".join(seasons) + " | mean | vs base /GW (±se) | hits | cap pts |",
           "|---|" + "---|" * (len(seasons) + 4)]
    table = []
    for key, by_season in rows.items():
        totals = [by_season[s]["total"] if s in by_season else None for s in seasons]
        have = [t for t in totals if t is not None]
        diffs = []
        for s, r in by_season.items():
            if s in base:
                diffs += [a - b for a, b in zip(r["gw_scores"], base[s]["gw_scores"],
                                                strict=False)]
        if len(diffs) > 1:
            m = sum(diffs) / len(diffs)
            sd = (sum((x - m) ** 2 for x in diffs) / (len(diffs) - 1)) ** 0.5
            vs = f"{m:+.2f} (±{sd / len(diffs) ** 0.5:.2f})"
        else:
            m, vs = 0.0, "—"
        hits = sum(r["hits"] for r in by_season.values())
        cap = sum(r["captain_pts"] for r in by_season.values())
        label = json.loads(key)
        merged = {**label["prep"], **label["cfg"]}
        name = ", ".join(f"{k}={v}" for k, v in merged.items()) or "baseline"
        table.append((m, f"| {name} | " + " | ".join("" if t is None else str(t) for t in totals)
                      + f" | {sum(have) / max(1, len(have)):.0f} | {vs} | {hits} | {cap} |"))
    out += [line for _, line in sorted(table, key=lambda t: -t[0])]
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1])
