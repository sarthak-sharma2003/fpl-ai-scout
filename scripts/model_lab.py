"""Model lab: compare points-model variants on FIXED, model-independent metrics.

    python scripts/model_lab.py [variant ...] [--holdout 2024-25] [--db data/fpl.duckdb]

Why not `fplscout train`'s report: its "decision subset" is chosen by each
model's OWN minutes predictions, so two variants are scored on different rows.
Here every variant is scored on the same rows, with metrics shaped like the
decision the optimizer actually makes:

  topk     mean realized points of each GW's top-K predicted players per
           position (K = GKP 2, DEF 8, MID 8, FWD 5) -- what a squad gets
  cap      mean realized points of each GW's single top predicted player
           (the captain), and of the top-3 (captain shortlist)
  sp_reg   mean per-GW Spearman among "regulars" (roll5_minutes >= 45, a
           pre-match-knowable filter that does not depend on any model)
  rmse     over all rows

Tuning discipline (ROADMAP §2.4): tune on 2023-24/2024-25 holdouts; 2025-26 is
touched once, for the final validation of a chosen variant.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from fplscout.models import minutes, points, team_goals  # noqa: E402
from fplscout.models.dataset import (  # noqa: E402
    CATEGORICAL_COLUMNS,
    FEATURE_COLUMNS,
    load_dataset,
    minutes_class,
)
from fplscout.models.train import _team_goals_lookup  # noqa: E402

ALL_SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
TOPK = {"GKP": 2, "DEF": 8, "MID": 8, "FWD": 5}
GOAL_PTS = {"GKP": 6, "DEF": 6, "MID": 5, "FWD": 4}
CS_PTS = {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0}


# --------------------------------------------------------------------------
# data


def _extra_columns(con, seasons: list[str]) -> pd.DataFrame:
    """Per-row realized components, for xPts targets and crowd features."""
    ph = ", ".join(["?"] * len(seasons))
    return con.execute(
        f"""
        SELECT season, code, fixture_id, gw, goals_scored, assists, clean_sheets,
               goals_conceded, expected_goals, expected_assists,
               expected_goals_conceded, starts, selected, transfers_balance
        FROM player_gw_history WHERE season IN ({ph})
        """,
        seasons,
    ).df()


def _crowd_features(con, seasons: list[str]) -> pd.DataFrame:
    """Ownership share and net transfers from the player's PREVIOUS gameweek row
    (lag 1, so nothing from the decision GW itself is used)."""
    ph = ", ".join(["?"] * len(seasons))
    df = con.execute(
        f"""
        WITH g AS (
          SELECT season, code, gw, MAX(selected) AS selected,
                 MAX(transfers_balance) AS tb
          FROM player_gw_history WHERE season IN ({ph}) GROUP BY 1, 2, 3
        ), tot AS (
          SELECT season, gw, MAX(selected) AS top_sel FROM g GROUP BY 1, 2
        )
        SELECT g.season, g.code, g.gw,
               LAG(g.selected / NULLIF(t.top_sel, 0)) OVER w AS own_rel_lag1,
               LAG(g.tb / NULLIF(t.top_sel, 0)) OVER w AS tb_rel_lag1
        FROM g JOIN tot t USING (season, gw)
        WINDOW w AS (PARTITION BY g.season, g.code ORDER BY g.gw)
        """,
        seasons,
    ).df()
    return df


DASTAN = Path("/tmp/claude-0/-home-user-fpl-ai-scout/004bd79f-9545-5705-a04e-df76ce5bcc35/"
              "scratchpad/dastan")
PD_COLS = ["pd_status_risk", "pd_chance", "pd_has_news", "pd_ep_next"]


def _predeadline() -> pd.DataFrame:
    """Dastan's provenance-checked pre-deadline snapshots (fplcache), keyed
    (season, gw, code). Missing is -1, never 0 (0 is a real ep_next/chance)."""
    sig = duckdb.sql(f"SELECT * EXCLUDE (snapshot_at, deadline_time) "
                     f"FROM read_parquet('{DASTAN / 'pre_deadline_signals.parquet'}')").df()
    ep = duckdb.sql(f"SELECT * EXCLUDE (snapshot_at, deadline_time) "
                    f"FROM read_parquet('{DASTAN / 'pre_deadline_ep_next.parquet'}')").df()
    sig = sig.rename(columns={"gameweek": "gw", "fpl_code": "code",
                              "sig_status_risk": "pd_status_risk",
                              "sig_chance_playing": "pd_chance",
                              "sig_has_news": "pd_has_news"})
    ep = ep.rename(columns={"gameweek": "gw", "fpl_code": "code", "ep_next": "pd_ep_next"})
    out = sig[["season", "gw", "code", "pd_status_risk", "pd_chance", "pd_has_news"]].merge(
        ep[["season", "gw", "code", "pd_ep_next"]], on=["season", "gw", "code"], how="outer")
    return out


def load(con, seasons: list[str]) -> pd.DataFrame:
    df = load_dataset(con, seasons)
    extra = _extra_columns(con, seasons)
    df = df.merge(extra.drop(columns=["gw"]), on=["season", "code", "fixture_id"], how="left")
    crowd = _crowd_features(con, seasons)
    df = df.merge(crowd, on=["season", "code", "gw"], how="left")
    if not set(PD_COLS) <= set(df.columns):     # load_dataset carries them since v2
        pdl = _predeadline()
        df = df.merge(pdl, on=["season", "gw", "code"], how="left")
    df[PD_COLS] = df[PD_COLS].fillna(-1.0)
    return df


def xpts_target(df: pd.DataFrame) -> pd.Series:
    """total_points with goal/assist/CS/conceded luck swapped for expectations.

    Realized appearance, bonus, saves, cards, DEFCON stay as they are. Where xG
    is missing (2021-22) the realized value is kept, so the target degrades to
    total_points rather than inventing anything.
    """
    pos = df["position"].astype(str)
    gpts = pos.map(GOAL_PTS).astype(float)
    cspts = pos.map(CS_PTS).astype(float)
    mins = df["actual_minutes"].fillna(0)
    xg = df["expected_goals"]
    xa = df["expected_assists"]
    xgc = df["expected_goals_conceded"]
    has = xg.notna() & xa.notna() & xgc.notna() & (mins > 0)

    real = (df["goals_scored"].fillna(0) * gpts + df["assists"].fillna(0) * 3
            + df["clean_sheets"].fillna(0) * cspts)
    gk_def = pos.isin(["GKP", "DEF"])
    real = real - np.where(gk_def, np.floor(df["goals_conceded"].fillna(0) / 2), 0)

    # P(clean sheet) ~ exp(-xGC) for 60+ minute rows (FPL needs 60 minutes)
    p_cs = np.exp(-xgc.fillna(0)) * (mins >= 60)
    # E[floor(G/2)] for G ~ Poisson(xGC), truncated at 10
    lam = xgc.fillna(0).to_numpy()
    k = np.arange(0, 11)
    from scipy.stats import poisson
    pmf = poisson.pmf(k[None, :], lam[:, None])
    e_half = (pmf * np.floor(k / 2)[None, :]).sum(axis=1)
    exp_ = (xg.fillna(0) * gpts + xa.fillna(0) * 3 + p_cs * cspts
            - np.where(gk_def, e_half, 0))
    out = df["total_points"].astype(float).copy()
    out[has] = (df["total_points"] - real + exp_)[has]
    return out


def pav(x: np.ndarray, y: np.ndarray, bins: int = 40):
    """Isotonic fit via pool-adjacent-violators on quantile-binned means.
    Returns (knots_x, knots_y) for np.interp."""
    order = np.argsort(x)
    x, y = x[order], y[order]
    edges = np.unique(np.quantile(x, np.linspace(0, 1, bins + 1)))
    idx = np.clip(np.searchsorted(edges, x, side="right") - 1, 0, len(edges) - 2)
    bx = np.array([x[idx == k].mean() for k in range(len(edges) - 1) if (idx == k).any()])
    by = np.array([y[idx == k].mean() for k in range(len(edges) - 1) if (idx == k).any()])
    bw = np.array([(idx == k).sum() for k in range(len(edges) - 1) if (idx == k).any()], float)
    vals, wts, xs = list(by), list(bw), [[v] for v in bx]
    i = 0
    while i < len(vals) - 1:
        if vals[i] > vals[i + 1]:
            w = wts[i] + wts[i + 1]
            vals[i] = (vals[i] * wts[i] + vals[i + 1] * wts[i + 1]) / w
            wts[i] = w
            xs[i] = xs[i] + xs[i + 1]
            del vals[i + 1], wts[i + 1], xs[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    kx = np.array([np.mean(v) for v in xs])
    return kx, np.array(vals)


# --------------------------------------------------------------------------
# variants

REG_PARAMS = {
    "objective": "regression", "metric": "rmse", "learning_rate": 0.03,
    "num_leaves": 31, "min_data_in_leaf": 100, "feature_fraction": 0.8,
    "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0, "verbose": -1,
}


SEED = int(__import__("os").environ.get("LAB_SEED", "0"))


def _fit(X, y, params, rounds):
    if SEED:
        params = {**params, "seed": SEED, "bagging_seed": SEED,
                  "feature_fraction_seed": SEED}
    ds = lgb.Dataset(X, label=y, categorical_feature=[c for c in CATEGORICAL_COLUMNS
                                                     if c in X.columns],
                     free_raw_data=False)
    return lgb.train(params, ds, num_boost_round=rounds)


ODDS_COLUMNS = ["odds_win_prob", "odds_exp_goals_for", "odds_exp_goals_against"]


def run_variant(name: str, opts: dict, train: pd.DataFrame, test: pd.DataFrame,
                con, train_seasons: list[str]) -> pd.Series:
    """Returns EV predictions for `test` rows."""
    if opts.get("no_odds"):
        # ablation: blank the bookmaker-odds inputs everywhere (LightGBM reads
        # an all-missing column as no information), for minutes AND points
        train, test = train.copy(), test.copy()
        train[ODDS_COLUMNS] = np.nan
        test[ODDS_COLUMNS] = np.nan
        return run_variant(name, {k: v for k, v in opts.items() if k != "no_odds"},
                           train, test, con, train_seasons)
    if opts.get("calib"):
        # out-of-sample predictions for the LAST training season, from a model
        # trained on the seasons before it, fit a per-position isotonic map
        inner = {k: v for k, v in opts.items() if k != "calib"}
        last = train_seasons[-1]
        tr_in = train[train["season"] != last]
        va = train[train["season"] == last]
        p_va = run_variant(name, inner, tr_in, va, con, train_seasons[:-1])
        pred = run_variant(name, inner, train, test, con, train_seasons)
        out = pred.copy()
        for pos in points.POSITIONS:
            mv = (va["position"] == pos).to_numpy() & p_va.notna().to_numpy()
            mt = (test["position"] == pos).to_numpy() & pred.notna().to_numpy()
            if mv.sum() < 200 or not mt.any():
                continue
            kx, ky = pav(p_va.to_numpy()[mv], va["total_points"].to_numpy(float)[mv])
            out[mt] = np.interp(pred.to_numpy()[mt], kx, ky)
        return out
    mfeats = list(dict.fromkeys(list(FEATURE_COLUMNS) + (PD_COLS if opts.get("pd") else [])))
    if opts.get("pd"):
        y = minutes_class(train["actual_minutes"])
        ds = lgb.Dataset(train[mfeats], label=y, categorical_feature=CATEGORICAL_COLUMNS,
                         free_raw_data=False)
        mm = lgb.train(minutes.LGB_PARAMS, ds, num_boost_round=minutes.NUM_BOOST_ROUND)
        p_tr, p_te = mm.predict(train[mfeats]), mm.predict(test[mfeats])
    else:
        mm = minutes.train(train)
        p_tr = minutes.predict_proba(mm, train)
        p_te = minutes.predict_proba(mm, test)
    fixtures = con.execute(
        f"SELECT * FROM fixtures WHERE season IN ({', '.join(['?'] * len(train_seasons))})",
        train_seasons).df()
    teams = con.execute("SELECT season, team_id, code FROM teams").df()
    dc = team_goals.fit(fixtures, teams)
    tr = points.add_model_features(train, p_tr, _team_goals_lookup(dc, train, teams))
    te = points.add_model_features(test, p_te, _team_goals_lookup(dc, test, teams))

    feats = list(points.ALL_FEATURE_COLUMNS)
    if opts.get("crowd"):
        feats += ["own_rel_lag1", "tb_rel_lag1"]
    if opts.get("pd"):
        feats += PD_COLS
    feats = list(dict.fromkeys(feats))
    target = xpts_target(tr) if opts.get("xpts") else tr["total_points"].astype(float)
    params = REG_PARAMS if opts.get("reg") else points.MEAN_PARAMS
    rounds = 500 if opts.get("reg") else points.NUM_BOOST_ROUND

    pred = pd.Series(np.nan, index=te.index)
    for pos in points.POSITIONS:
        mtr = tr["position"] == pos
        mte = te["position"] == pos
        if not mte.any():
            continue
        if opts.get("hurdle"):
            # E = P(60+) E[pts | 60+] + P(1-59) E[pts | 1-59]: the conditional
            # models never see the zero-inflation of benched rows, and the
            # minutes model alone carries availability.
            m60 = mtr & (tr["actual_minutes"] >= 60)
            msub = mtr & (tr["actual_minutes"] > 0) & (tr["actual_minutes"] < 60)
            b60 = _fit(tr.loc[m60, feats], target[m60], params, rounds)
            bsub = _fit(tr.loc[msub, feats], target[msub], params, rounds)
            e60 = b60.predict(te.loc[mte, feats])
            esub = bsub.predict(te.loc[mte, feats])
            pred[mte] = (te.loc[mte, "mins_p60_plus"] * e60
                         + te.loc[mte, "mins_p1_59"] * esub)
        else:
            b = _fit(tr.loc[mtr, feats], target[mtr], params, rounds)
            pred[mte] = b.predict(te.loc[mte, feats])
        if opts.get("blend_naive"):
            naive = te.loc[mte, "roll5_points"].fillna(te.loc[mte, "prev_season_points_per90"]
                                                     .fillna(2.0) * 0.7)
            pred[mte] = (1 - opts["blend_naive"]) * pred[mte] + opts["blend_naive"] * naive
    return pred


VARIANTS = {
    "base": {},
    "reg": {"reg": True},
    "xpts": {"xpts": True},
    "hurdle": {"hurdle": True},
    "crowd": {"crowd": True},
    "xpts+reg": {"xpts": True, "reg": True},
    "hurdle+xpts": {"hurdle": True, "xpts": True},
    "hurdle+xpts+reg": {"hurdle": True, "xpts": True, "reg": True},
    "all": {"hurdle": True, "xpts": True, "reg": True, "crowd": True},
    "crowd+xpts+reg": {"crowd": True, "xpts": True, "reg": True},
    "pd": {"pd": True},
    "pd+hurdle": {"pd": True, "hurdle": True},
    "pd+xpts+reg": {"pd": True, "xpts": True, "reg": True},
    "pd+hurdle+xpts+reg": {"pd": True, "hurdle": True, "xpts": True, "reg": True},
    "xpts+reg+calib": {"xpts": True, "reg": True, "calib": True},
    "pd+xpts+reg+calib": {"pd": True, "xpts": True, "reg": True, "calib": True},
    "pd+hurdle+xpts+reg+calib": {"pd": True, "hurdle": True, "xpts": True, "reg": True,
                                 "calib": True},
    # production v2 without the bookmaker-odds features (odds ablation)
    "pd+hurdle+xpts+reg-noodds": {"pd": True, "hurdle": True, "xpts": True, "reg": True,
                                  "no_odds": True},
}


# --------------------------------------------------------------------------
# metrics


def metrics(test: pd.DataFrame, pred: pd.Series) -> dict:
    d = test[["gw", "code", "position", "total_points", "roll5_minutes", "value"]].copy()
    d["pred"] = pred.values
    # DGW: a player has two rows; decisions are per player-GW, so sum
    g = d.groupby(["gw", "code", "position"], observed=True).agg(
        pts=("total_points", "sum"), pred=("pred", "sum"),
        r5=("roll5_minutes", "max")).reset_index()
    topk, oracle = [], []
    for (_gw, pos), sub in g.groupby(["gw", "position"], observed=True):
        k = TOPK.get(str(pos), 5)
        topk.append(sub.nlargest(k, "pred")["pts"].mean())
        oracle.append(sub.nlargest(k, "pts")["pts"].mean())
    cap1, cap3 = [], []
    sp = []
    for _gw, sub in g.groupby("gw"):
        cap1.append(sub.nlargest(1, "pred")["pts"].iloc[0])
        cap3.append(sub.nlargest(3, "pred")["pts"].mean())
        reg = sub[sub["r5"] >= 45]
        if len(reg) > 10:
            sp.append(spearmanr(reg["pts"], reg["pred"]).statistic)
    mask = d["pred"].notna()
    return {
        "topk": float(np.mean(topk)),
        "topk_oracle": float(np.mean(oracle)),
        "cap1": float(np.mean(cap1)),
        "cap3": float(np.mean(cap3)),
        "sp_reg": float(np.nanmean(sp)),
        "rmse": float(np.sqrt(np.mean((d.loc[mask, "total_points"] - d.loc[mask, "pred"]) ** 2))),
        "n_gw": int(g["gw"].nunique()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("variants", nargs="*", default=list(VARIANTS))
    ap.add_argument("--holdout", action="append", default=None)
    ap.add_argument("--db", default=str(REPO / "data" / "fpl.duckdb"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    holdouts = a.holdout or ["2023-24", "2024-25"]
    con = duckdb.connect(a.db, read_only=True)
    results = {}
    for h in holdouts:
        tr_seasons = ALL_SEASONS[:ALL_SEASONS.index(h)]
        train, test = load(con, tr_seasons), load(con, [h])
        for v in a.variants:
            t0 = time.time()
            pred = run_variant(v, VARIANTS[v], train, test, con, tr_seasons)
            m = metrics(test, pred)
            results[f"{h}/{v}"] = m
            print(f"{h} {v:18} topk {m['topk']:.3f} cap1 {m['cap1']:.2f} "
                  f"cap3 {m['cap3']:.2f} sp_reg {m['sp_reg']:.4f} rmse {m['rmse']:.3f} "
                  f"({time.time() - t0:.0f}s)", flush=True)
    if a.out:
        Path(a.out).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
