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

Two variants, per review: `fpl_xp` (FPL's own ep_next/xP) carries 63-65% of total
gain in the FULL variant's mean model — the model is largely "xP plus learned
corrections". That's fine when xP is genuinely available (live production always
has it from bootstrap-static), but vaastav's historical xP has real gaps (see
dataset.py), and a model trained with xp as a dominant feature performs badly when
that feature is amputated at inference time — worse than a model that never relied
on it. `train_variant()` takes an explicit `feature_columns` list so callers
(models/train.py) can train both a FULL variant (xp included) and an INDEPENDENT
variant (xp excluded, importance forced onto rolling-form/team-goals/minutes
features instead) from the same code path, then route between them per-row based on
whether xp is actually available (train.py's `route_predictions`).
"""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd

from fplscout.models.dataset import CATEGORICAL_COLUMNS, FEATURE_COLUMNS

POSITIONS = ["GKP", "DEF", "MID", "FWD"]

EXTRA_FEATURE_COLUMNS_WITH_XP = [
    "mins_p0", "mins_p1_59", "mins_p60_plus", "expected_minutes",
    "team_xg_for", "team_xg_against", "clean_sheet_prob", "fpl_xp",
]
EXTRA_FEATURE_COLUMNS_NO_XP = [
    c for c in EXTRA_FEATURE_COLUMNS_WITH_XP if c != "fpl_xp"
]
FULL_FEATURE_COLUMNS = FEATURE_COLUMNS + EXTRA_FEATURE_COLUMNS_WITH_XP
INDEPENDENT_FEATURE_COLUMNS = FEATURE_COLUMNS + EXTRA_FEATURE_COLUMNS_NO_XP

# Backwards-compatible aliases (Phase 3's original single-variant names).
EXTRA_FEATURE_COLUMNS = EXTRA_FEATURE_COLUMNS_WITH_XP
ALL_FEATURE_COLUMNS = FULL_FEATURE_COLUMNS

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
    train_df: pd.DataFrame, feature_columns: list[str] = FULL_FEATURE_COLUMNS
) -> dict[str, dict[str, lgb.Booster]]:
    """Returns {position: {"mean": booster, "q10": booster, "q90": booster}}.

    Pass `feature_columns=INDEPENDENT_FEATURE_COLUMNS` to train the xp-free
    variant; defaults to the FULL (xp-included) variant for backward
    compatibility with earlier Phase 3 call sites."""
    models: dict[str, dict[str, lgb.Booster]] = {}
    for position in POSITIONS:
        sub = train_df[train_df["position"] == position]
        if len(sub) < 50:
            continue
        X = sub[feature_columns]
        y = sub["total_points"]
        dataset = lgb.Dataset(
            X, label=y, categorical_feature=CATEGORICAL_COLUMNS, free_raw_data=False
        )
        mean_model = lgb.train(MEAN_PARAMS, dataset, num_boost_round=NUM_BOOST_ROUND)

        q_models = {}
        for alpha, name in [(0.1, "q10"), (0.9, "q90")]:
            params = {**QUANTILE_PARAMS_TEMPLATE, "alpha": alpha}
            q_models[name] = lgb.train(params, dataset, num_boost_round=NUM_BOOST_ROUND)

        models[position] = {"mean": mean_model, **q_models}
    return models


def predict(
    models: dict[str, dict[str, lgb.Booster]],
    df: pd.DataFrame,
    feature_columns: list[str] = FULL_FEATURE_COLUMNS,
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
        out.loc[mask, "ev_points"] = position_models["mean"].predict(X)
        out.loc[mask, "q10_points"] = position_models["q10"].predict(X)
        out.loc[mask, "q90_points"] = position_models["q90"].predict(X)
    return out


def feature_gain_by_column(models: dict[str, dict[str, lgb.Booster]]) -> pd.DataFrame:
    """Per-position, per-feature total gain share for the mean model — used to
    verify (or refute) claims like "fpl_xp carries 63-65% of gain"."""
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
