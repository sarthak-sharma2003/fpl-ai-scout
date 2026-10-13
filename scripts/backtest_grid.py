"""One season replay, prepared once, simulated under a grid of decision configs.

    python scripts/backtest_grid.py --season 2024-25 \
        --prep '{"DECAY": 0.9}' \
        --grid '[{}, {"captain_q90_weight": 0.0}, {"rules": ["starts_w50"]}]' \
        --out data/reports/bt/2024-25_decay0.9.json

`--prep` overrides simulator module constants that shape the EV the season is
prepared with (DECAY, HORIZON); `--grid` entries are simulate_season keyword
overrides, which are cheap because they reuse the prepared season. Run by
.github/workflows/backtest.yml as a matrix; also runnable locally.

Remember ROADMAP §2.4-2.5: tune on 2023-24/2024-25, validate on 2025-26 once,
and +-60 season points is noise -- read paired per-GW differences, not totals.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import duckdb

from fplscout.backtest import simulator as sim
from fplscout.backtest.strategies import load_history, make_rules
from fplscout.models import points

SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", required=True)
    ap.add_argument("--prep", default="{}")
    ap.add_argument("--grid", default="[{}]")
    ap.add_argument("--db", default="data/fpl.duckdb")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    prep_over = json.loads(a.prep)
    for k, v in prep_over.items():
        if k == "SEED":          # model bagging seed, for multi-seed evaluation
            points.set_seed(int(v))
        else:
            setattr(sim, k, v)
    t0 = time.time()
    con = duckdb.connect(a.db, read_only=True)
    train = SEASONS[: SEASONS.index(a.season)]
    prepared = sim.prepare_season(con, a.season, train)
    rules_by_name = make_rules(load_history(con, a.season))
    results = []
    for cfg in json.loads(a.grid):
        kw = {k: v for k, v in cfg.items() if k != "rules"}
        if cfg.get("rules"):
            kw["rules"] = tuple(rules_by_name[n] for n in cfg["rules"])
        r = sim.simulate_season(con, a.season, train, prepared=prepared, **kw)
        cap = 0
        for g in r.gw_results:
            act = prepared.actual.get(g.gw)
            if act is not None and g.effective_captain is not None:
                cap += int(act.loc[act["code"] == g.effective_captain, "total_points"].sum())
        results.append({
            "prep": prep_over, "cfg": cfg, "total": r.total_points,
            "hits": sum(g.hits for g in r.gw_results),
            "transfers": sum(len(g.transfers_in) for g in r.gw_results[1:]),
            "captain_pts": cap, "gw_scores": [g.gw_score for g in r.gw_results],
        })
        print(a.season, prep_over, cfg, r.total_points, flush=True)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"season": a.season, "secs": time.time() - t0,
                                       "results": results}))


if __name__ == "__main__":
    main()
