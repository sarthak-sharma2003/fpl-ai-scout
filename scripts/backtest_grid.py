"""One season replay, prepared once, simulated under a grid of decision configs.

    python scripts/backtest_grid.py --season 2024-25 \
        --prep '{"DECAY": 0.9}' \
        --grid '[{}, {"captain_q90_weight": 0.0}, {"rules": ["starts_w50"]}]' \
        --out data/reports/bt/2024-25_decay0.9.json

`--prep` overrides simulator module constants that shape the EV the season is
prepared with (DECAY, HORIZON), the model seed (SEED), the minutes model
(MINUTES: {"params": {...}, "rounds": n}) or the feature set (DROP_FEATURES:
[...]); `--grid` entries are simulate_season keyword
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
from fplscout.models import dataset, minutes, points

SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]


def _apply_prep(prep_over: dict, defaults: tuple) -> None:
    """Reset every override to its default, then apply this prep's."""
    (sim_defaults, lgb_defaults, rounds_default, cond_defaults,
     feat_defaults, all_feat_defaults) = defaults
    for k, v in sim_defaults.items():
        setattr(sim, k, v)
    minutes.LGB_PARAMS.clear()
    minutes.LGB_PARAMS.update(lgb_defaults)
    minutes.NUM_BOOST_ROUND = rounds_default
    points.COND_PARAMS.clear()
    points.COND_PARAMS.update(cond_defaults)
    # in place: minutes/points hold references to these very lists
    dataset.FEATURE_COLUMNS[:] = feat_defaults
    points.ALL_FEATURE_COLUMNS[:] = all_feat_defaults
    for k, v in prep_over.items():
        if k == "SEED":          # model bagging seed, for multi-seed evaluation
            points.set_seed(int(v))
            continue
        if k == "DROP_FEATURES":  # ablation: train every model without these columns
            for cols in (dataset.FEATURE_COLUMNS, points.ALL_FEATURE_COLUMNS):
                cols[:] = [c for c in cols if c not in v]
            continue
        if k == "MINUTES":       # minutes-model overrides {"params": {...}, "rounds": n}
            minutes.LGB_PARAMS.update(v.get("params", {}))
            if "rounds" in v:
                minutes.NUM_BOOST_ROUND = int(v["rounds"])
        else:
            setattr(sim, k, v)


def _run(con, season, train, prepared, rules_by_name, prep_over, cfg) -> dict:
    kw = {k: v for k, v in cfg.items() if k != "rules"}
    if cfg.get("rules"):
        kw["rules"] = tuple(rules_by_name[n] for n in cfg["rules"])
    r = sim.simulate_season(con, season, train, prepared=prepared, **kw)
    cap = 0
    for g in r.gw_results:
        act = prepared.actual.get(g.gw)
        if act is not None and g.effective_captain is not None:
            cap += int(act.loc[act["code"] == g.effective_captain, "total_points"].sum())
    print(season, prep_over, cfg, r.total_points, flush=True)
    return {
        "prep": prep_over, "cfg": cfg, "total": r.total_points,
        "hits": sum(g.hits for g in r.gw_results),
        "transfers": sum(len(g.transfers_in) for g in r.gw_results[1:]),
        "captain_pts": cap, "gw_scores": [g.gw_score for g in r.gw_results],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", required=True)
    ap.add_argument("--prep", default="{}")
    ap.add_argument("--grid", default="[{}]")
    ap.add_argument("--db", default="data/fpl.duckdb")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    # One prep object, or a list of them run one after another in this process
    # -- on the same machine, which is what makes their per-GW diffs paired:
    # LightGBM is only bit-reproducible on identical hardware, and CI runners
    # differ (identical configs diverged from GW1 across jobs, 2026-10-13).
    preps = json.loads(a.prep)
    preps = preps if isinstance(preps, list) else [preps]
    defaults = ({k: getattr(sim, k) for k in dir(sim) if k.isupper()},
                dict(minutes.LGB_PARAMS), minutes.NUM_BOOST_ROUND,
                dict(points.COND_PARAMS),
                list(dataset.FEATURE_COLUMNS), list(points.ALL_FEATURE_COLUMNS))
    t0 = time.time()
    con = duckdb.connect(a.db, read_only=True)
    train = SEASONS[: SEASONS.index(a.season)]
    rules_by_name = make_rules(load_history(con, a.season))
    results = []
    for prep_over in preps:
        _apply_prep(prep_over, defaults)
        prepared = sim.prepare_season(con, a.season, train)
        for cfg in json.loads(a.grid):
            results.append(_run(con, a.season, train, prepared, rules_by_name,
                                prep_over, cfg))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"season": a.season, "secs": time.time() - t0,
                                       "results": results}))


if __name__ == "__main__":
    main()
