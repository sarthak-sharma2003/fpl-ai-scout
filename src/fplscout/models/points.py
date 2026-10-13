"""Points model: E[total_points] per player per GW — plan §Phase3.3-8.

Deviation from the plan's literal 8-component decomposition (attacking / clean-sheet
/ DEFCON / bonus / appearance / saves / cards as separate sub-models combined by
formula): trains one direct per-position LightGBM regressor on `total_points`
instead. This is a documented simplification, not a scope cut on modeling effort —
the same signals the plan's components would have used (team-goals-model E[goals]/
clean-sheet probability, minutes-model P(60+)/P(1-59)/P(0), rolling DEFCON/xG/xA/bps)
are all fed in as features, so the model still has to learn the same relationships;
it just learns the combination itself via gradient-boosted trees rather than via an
explicit analytic formula. Reasoning: 8 separate component models (each needing its
own target definition, training loop, and validation) is roughly 8x the engineering
and failure surface of one well-featured regressor, for a task where the plan itself
warns against gold-plating (§5). If the validation report shows the direct regressor
underperforms on a specific component (e.g. clean-sheet timing for defenders), that's
the concrete evidence needed to justify building the decomposed version instead of
guessing upfront that it's necessary.

Quantiles (q10/q90) trained directly on total_points with LightGBM's quantile
objective, per plan §Phase3.8.

`fpl_xp` (vaastav's `xP` column) is NOT a feature here, and this is not the same
"independent variant" hedge from an earlier pass. vaastav's own README documents
that `xP` is scraped from bootstrap-static's `ep_this` *after* each gameweek ends,
with an empirically observed same-GW correlation to actual points (~0.40) the
README itself calls "unusually high for a genuinely pre-match feature" — i.e. the
column is confirmed post-match-contaminated for a meaningful fraction of rows, not
merely "sometimes missing" (the earlier, wrong diagnosis). A model trained on it
was measuring near-oracle hindsight, not predictive skill (this is what actually
produced a 2024-25 backtest total, 3,591 pts, that exceeds the real-world all-time
FPL season record). There is no FULL variant anymore; this is the only model.
Live-fetched ep_next/ep_this (see ingest/health.py's archiving) does not have this
problem — it's fetched pre-deadline by us — and can be legitimately reintroduced
as a feature once enough of our own archived history exists to train on.
"""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import poisson

from fplscout.models.dataset import CATEGORICAL_COLUMNS, COMPONENT_COLUMNS, FEATURE_COLUMNS

POSITIONS = ["GKP", "DEF", "MID", "FWD"]

EXTRA_FEATURE_COLUMNS = [
    "mins_p0", "mins_p1_59", "mins_p60_plus", "expected_minutes",
    "team_xg_for", "team_xg_against", "clean_sheet_prob",
]
ALL_FEATURE_COLUMNS = FEATURE_COLUMNS + EXTRA_FEATURE_COLUMNS

MEAN_PARAMS = {
    "objective": "regression",
    "metric": "rmse",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 30,
    "verbose": -1,
}
QUANTILE_PARAMS_TEMPLATE = {
    "objective": "quantile",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 30,
    "verbose": -1,
}
NUM_BOOST_ROUND = 200

# --- 2026-10-13 model upgrade (scripts/model_lab.py, holdouts 2023-24/2024-25,
# 4 seeds; top-K realized pts per pick 4.12/4.32 -> 4.66/4.52, per-GW Spearman
# among regulars .297/.302 -> .419/.394, RMSE 1.976/1.964 -> 1.879/1.883,
# together with the pre-deadline availability features in dataset.py) --
#
# HURDLE: E[pts] = P(60+) E[pts | 60+] + P(1-59) E[pts | 1-59]. The direct
# regressor had to learn availability and quality from one zero-inflated
# target; the conditional heads only ever see players who played, and the
# minutes model alone owns "will he play" -- i.e. starts drive the EV.
# XPTS_TARGET: the conditional heads train on points with goal/assist/clean-
# sheet luck swapped for its expectation (xpts_target), so they learn chance
# quality instead of finishing noise. The q10/q90 heads stay on raw points:
# they exist to describe the real spread.
HURDLE = True
XPTS_TARGET = True
COND_PARAMS = {
    "objective": "regression", "metric": "rmse", "learning_rate": 0.03,
    "num_leaves": 31, "min_data_in_leaf": 100, "feature_fraction": 0.8,
    "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
    "seed": 7, "verbose": -1,
}
COND_ROUNDS = 500
GOAL_PTS = {"GKP": 6, "DEF": 6, "MID": 5, "FWD": 4}
CS_PTS = {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0}


def xpts_target(df: pd.DataFrame) -> pd.Series:
    """total_points with goal/assist/clean-sheet/conceded luck replaced by its
    expectation given the player's actual xG, xA and xGC.

    Appearance, bonus, saves, cards and DefCon stay realized. Rows without
    xG data (or 0 minutes) keep total_points unchanged, so the target degrades
    to the old one rather than inventing anything. A training TARGET only --
    the realized components are outcomes and never features.
    """
    if not set(COMPONENT_COLUMNS) <= set(df.columns):
        return df["total_points"].astype(float)
    pos = df["position"].astype(str)
    gpts = pos.map(GOAL_PTS).astype(float)
    cspts = pos.map(CS_PTS).astype(float)
    mins = df["actual_minutes"].fillna(0)
    xg, xa, xgc = df["expected_goals"], df["expected_assists"], df["expected_goals_conceded"]
    has = (xg.notna() & xa.notna() & xgc.notna() & (mins > 0)).to_numpy()
    gk_def = pos.isin(["GKP", "DEF"]).to_numpy()

    real = (df["goals_scored"].fillna(0) * gpts + df["assists"].fillna(0) * 3
            + df["clean_sheets"].fillna(0) * cspts).to_numpy(float)
    real = real - np.where(gk_def, np.floor(df["goals_conceded"].fillna(0) / 2), 0)

    lam = xgc.fillna(0).to_numpy(float)
    p_cs = np.exp(-lam) * (mins.to_numpy() >= 60)
    k = np.arange(0, 11)
    e_half = (poisson.pmf(k[None, :], lam[:, None]) * np.floor(k / 2)[None, :]).sum(axis=1)
    expected = (xg.fillna(0).to_numpy(float) * gpts.to_numpy() + xa.fillna(0).to_numpy(float) * 3
                + p_cs * cspts.to_numpy() - np.where(gk_def, e_half, 0))
    out = df["total_points"].astype(float).to_numpy().copy()
    out[has] = (df["total_points"].to_numpy(float) - real + expected)[has]
    return pd.Series(out, index=df.index)


def _fit(X: pd.DataFrame, y: pd.Series, params: dict, rounds: int) -> lgb.Booster:
    ds = lgb.Dataset(X, label=y, categorical_feature=CATEGORICAL_COLUMNS, free_raw_data=False)
    return lgb.train(params, ds, num_boost_round=rounds)


def add_model_features(
    df: pd.DataFrame,
    minutes_proba: np.ndarray,
    team_goals_lookup: pd.DataFrame,
) -> pd.DataFrame:
    """Attach minutes-model and team-goals-model outputs as extra feature columns.

    team_goals_lookup must have one row per (season, fixture_id, code) with columns
    team_xg_for, team_xg_against, clean_sheet_prob — precomputed by the caller
    (models/train.py), since the Dixon-Coles model itself doesn't know about
    individual player rows.
    """
    out = df.copy()
    out["mins_p0"] = minutes_proba[:, 0]
    out["mins_p1_59"] = minutes_proba[:, 1]
    out["mins_p60_plus"] = minutes_proba[:, 2]
    out["expected_minutes"] = minutes_proba @ np.array([0.0, 30.0, 90.0])
    out = out.merge(team_goals_lookup, on=["season", "fixture_id", "code"], how="left")
    return out


def train(
    train_df: pd.DataFrame,
    feature_columns: list[str] = ALL_FEATURE_COLUMNS,
    min_rows: int = 50,
) -> dict[str, dict[str, lgb.Booster]]:
    """Returns {position: {"mean": booster, "q10": booster, "q90": booster}}.
    Positions with fewer than min_rows training rows are skipped (their
    predictions come back NaN); tests with tiny fixtures lower it."""
    models: dict[str, dict[str, lgb.Booster]] = {}
    target = xpts_target(train_df) if XPTS_TARGET else train_df["total_points"].astype(float)
    for position in POSITIONS:
        sub = train_df[train_df["position"] == position]
        if len(sub) < min_rows:
            continue
        X = sub[feature_columns]
        y = sub["total_points"]
        dataset = lgb.Dataset(
            X, label=y, categorical_feature=CATEGORICAL_COLUMNS, free_raw_data=False
        )
        cond: dict[str, lgb.Booster] = {}
        if HURDLE and "actual_minutes" in sub:
            m60 = sub["actual_minutes"] >= 60
            msub = (sub["actual_minutes"] > 0) & (sub["actual_minutes"] < 60)
            # a head needs real data; tiny test fixtures fall back to direct
            need = max(min_rows, 30)
            if m60.sum() >= need and msub.sum() >= need:
                cond["mean60"] = _fit(sub.loc[m60, feature_columns],
                                      target.loc[sub.index][m60], COND_PARAMS, COND_ROUNDS)
                cond["meansub"] = _fit(sub.loc[msub, feature_columns],
                                       target.loc[sub.index][msub], COND_PARAMS, COND_ROUNDS)
        if cond:
            mean_model = cond["mean60"]      # gain reporting reads "mean"
        else:
            mean_model = lgb.train(MEAN_PARAMS, dataset, num_boost_round=NUM_BOOST_ROUND)

        q_models = {}
        for alpha, name in [(0.1, "q10"), (0.9, "q90")]:
            params = {**QUANTILE_PARAMS_TEMPLATE, "alpha": alpha}
            q_models[name] = lgb.train(params, dataset, num_boost_round=NUM_BOOST_ROUND)

        models[position] = {"mean": mean_model, **cond, **q_models}
    return models


def predict(
    models: dict[str, dict[str, lgb.Booster]],
    df: pd.DataFrame,
    feature_columns: list[str] = ALL_FEATURE_COLUMNS,
) -> pd.DataFrame:
    out = df[["season", "gw", "fixture_id", "code", "position"]].copy()
    out["ev_points"] = np.nan
    out["q10_points"] = np.nan
    out["q90_points"] = np.nan
    for position, position_models in models.items():
        mask = df["position"] == position
        if not mask.any():
            continue
        X = df.loc[mask, feature_columns]
        if "mean60" in position_models and "mins_p60_plus" in df:
            # hurdle: the minutes model's probabilities carry availability
            out.loc[mask, "ev_points"] = (
                df.loc[mask, "mins_p60_plus"].to_numpy() * position_models["mean60"].predict(X)
                + df.loc[mask, "mins_p1_59"].to_numpy() * position_models["meansub"].predict(X)
            )
        else:
            out.loc[mask, "ev_points"] = position_models["mean"].predict(X)
        out.loc[mask, "q10_points"] = position_models["q10"].predict(X)
        out.loc[mask, "q90_points"] = position_models["q90"].predict(X)
    return out


def feature_gain_by_column(models: dict[str, dict[str, lgb.Booster]]) -> pd.DataFrame:
    """Per-position, per-feature total gain share for the mean model."""
    rows = []
    for position, position_models in models.items():
        booster = position_models["mean"]
        importances = booster.feature_importance(importance_type="gain")
        names = booster.feature_name()
        total = importances.sum()
        for name, gain in zip(names, importances, strict=True):
            rows.append(
                {
                    "position": position,
                    "feature": name,
                    "gain": float(gain),
                    "gain_share": float(gain / total) if total > 0 else 0.0,
                }
            )
    return pd.DataFrame(rows)
