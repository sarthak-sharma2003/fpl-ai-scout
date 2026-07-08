"""Training harness + validation report — plan §Phase3 DoD.

Single train/holdout split (train on 2021-22..2024-25, hold out 2025-26), matching
the plan's explicit DoD wording ("beat baselines on held-out 25/26"). The plan's
general sliding time-series-CV protocol (train S-4..S-1, validate S) is implemented
as `train_test_split_by_season`, reusable for the further sliding validation the
Phase 6 backtest naturally extends into — but a single fixed split is what this
phase's gate actually requires, and running a second split (e.g. also holding out
24/25) roughly doubles model-training time for a check the Phase 6 full-season
backtest will redo anyway. Deferred, not skipped.

Two known simplifications, both documented at the point they matter (see
models/points.py and models/team_goals.py docstrings): the points model directly
regresses total_points per position rather than the plan's 8-component decomposition,
and the Dixon-Coles team-strength fit uses a single cutoff (fit once on train
seasons) rather than walking forward week-by-week through the holdout season.
"""

from __future__ import annotations

import pickle
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from fplscout.models import minutes, points, team_goals
from fplscout.models.dataset import load_dataset

TRAIN_SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25"]
HOLDOUT_SEASON = "2025-26"


def train_test_split_by_season(
    seasons: list[str], holdout: str
) -> tuple[list[str], str]:
    """General sliding-window helper: train on all seasons strictly before
    `holdout`. Reusable for further validation splits beyond this phase's gate."""
    idx = seasons.index(holdout)
    return seasons[:idx], holdout


def _team_goals_lookup(
    dc_model: team_goals.DixonColesModel,
    df: pd.DataFrame,
    teams: pd.DataFrame,
) -> pd.DataFrame:
    id_to_code = {(row.season, row.team_id): row.code for row in teams.itertuples()}
    rows = []
    seen = set()
    for r in df[
        ["season", "fixture_id", "code", "team_id", "opponent_team_id", "was_home"]
    ].itertuples():
        key = (r.season, r.fixture_id, r.code)
        if key in seen:
            continue
        seen.add(key)
        own_code = id_to_code.get((r.season, r.team_id))
        opp_code = id_to_code.get((r.season, r.opponent_team_id))
        if own_code is None or opp_code is None:
            rows.append((r.season, r.fixture_id, r.code, np.nan, np.nan, np.nan))
            continue
        if r.was_home:
            lam, mu = dc_model.expected_goals(own_code, opp_code)
            cs_own, _ = dc_model.clean_sheet_prob(own_code, opp_code)
        else:
            mu, lam = dc_model.expected_goals(opp_code, own_code)
            _, cs_own = dc_model.clean_sheet_prob(opp_code, own_code)
        rows.append((r.season, r.fixture_id, r.code, lam, mu, cs_own))
    return pd.DataFrame(
        rows,
        columns=["season", "fixture_id", "code", "team_xg_for", "team_xg_against",
                 "clean_sheet_prob"],
    )


def _naive_5gw_average(con: duckdb.DuckDBPyConnection, season: str) -> pd.DataFrame:
    """Plain (undecayed) trailing 5-game mean of total_points, shifted so the
    current row's own points never enter its own baseline — the literal "naive
    last-5-GW average" baseline from the plan's Phase 3 DoD."""
    df = con.execute(
        "SELECT code, gw, fixture_id, kickoff_time, total_points FROM player_gw_history "
        "WHERE season = ? ORDER BY code, kickoff_time, fixture_id",
        [season],
    ).df()
    df["naive_baseline"] = (
        df.groupby("code")["total_points"]
        .apply(lambda s: s.shift(1).rolling(5, min_periods=1).mean())
        .reset_index(level=0, drop=True)
    )
    return df[["code", "fixture_id", "naive_baseline"]]


def _rmse(y_true: pd.Series, y_pred: pd.Series) -> float:
    mask = y_true.notna() & y_pred.notna()
    return float(np.sqrt(np.mean((y_true[mask] - y_pred[mask]) ** 2)))


def _spearman(y_true: pd.Series, y_pred: pd.Series) -> float:
    mask = y_true.notna() & y_pred.notna()
    if mask.sum() < 2:
        return float("nan")
    return float(spearmanr(y_true[mask], y_pred[mask]).statistic)


def run(con: duckdb.DuckDBPyConnection, models_dir: Path) -> dict:
    t0 = time.time()
    train_seasons, holdout_season = train_test_split_by_season(
        [*TRAIN_SEASONS, HOLDOUT_SEASON], HOLDOUT_SEASON
    )

    train_df = load_dataset(con, train_seasons)
    holdout_df = load_dataset(con, [holdout_season])

    minutes_model = minutes.train(train_df)
    train_mins_proba = minutes.predict_proba(minutes_model, train_df)
    holdout_mins_proba = minutes.predict_proba(minutes_model, holdout_df)

    fixtures = con.execute(
        f"SELECT * FROM fixtures WHERE season IN ({', '.join(['?'] * len(train_seasons))})",
        train_seasons,
    ).df()
    teams = con.execute("SELECT season, team_id, code FROM teams").df()
    dc_model = team_goals.fit(fixtures, teams)

    train_tg_lookup = _team_goals_lookup(dc_model, train_df, teams)
    holdout_tg_lookup = _team_goals_lookup(dc_model, holdout_df, teams)

    train_full = points.add_model_features(train_df, train_mins_proba, train_tg_lookup)
    holdout_full = points.add_model_features(
        holdout_df, holdout_mins_proba, holdout_tg_lookup
    )

    points_models = points.train(train_full)
    holdout_preds = points.predict(points_models, holdout_full)

    holdout_eval = holdout_full[["code", "fixture_id", "position", "total_points", "fpl_xp"]].merge(
        holdout_preds[["code", "fixture_id", "ev_points"]], on=["code", "fixture_id"]
    )
    naive = _naive_5gw_average(con, holdout_season)
    holdout_eval = holdout_eval.merge(naive, on=["code", "fixture_id"], how="left")

    metrics_by_position = {}
    for position in points.POSITIONS:
        sub = holdout_eval[holdout_eval["position"] == position]
        if len(sub) == 0:
            continue
        metrics_by_position[position] = {
            "n": len(sub),
            "model_rmse": _rmse(sub["total_points"], sub["ev_points"]),
            "model_spearman": _spearman(sub["total_points"], sub["ev_points"]),
            "xp_rmse": _rmse(sub["total_points"], sub["fpl_xp"]),
            "xp_spearman": _spearman(sub["total_points"], sub["fpl_xp"]),
            "naive_rmse": _rmse(sub["total_points"], sub["naive_baseline"]),
            "naive_spearman": _spearman(sub["total_points"], sub["naive_baseline"]),
        }

    overall = {
        "n": len(holdout_eval),
        "model_rmse": _rmse(holdout_eval["total_points"], holdout_eval["ev_points"]),
        "model_spearman": _spearman(holdout_eval["total_points"], holdout_eval["ev_points"]),
        "xp_rmse": _rmse(holdout_eval["total_points"], holdout_eval["fpl_xp"]),
        "xp_spearman": _spearman(holdout_eval["total_points"], holdout_eval["fpl_xp"]),
        "naive_rmse": _rmse(holdout_eval["total_points"], holdout_eval["naive_baseline"]),
        "naive_spearman": _spearman(holdout_eval["total_points"], holdout_eval["naive_baseline"]),
    }

    # minutes-model calibration: predicted P(60+) bucket vs realized 60+ rate
    holdout_df = holdout_df.copy()
    holdout_df["p60_plus"] = holdout_mins_proba[:, 2]
    holdout_df["actually_60_plus"] = (holdout_df["actual_minutes"] >= 60).astype(int)
    calib_bins = pd.cut(holdout_df["p60_plus"], bins=np.linspace(0, 1, 6), include_lowest=True)
    calibration = (
        holdout_df.groupby(calib_bins, observed=True)
        .agg(n=("actually_60_plus", "size"), realized_rate=("actually_60_plus", "mean"))
        .reset_index()
    )

    models_dir.mkdir(parents=True, exist_ok=True)
    version = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    with open(models_dir / f"minutes_{version}.pkl", "wb") as f:
        pickle.dump(minutes_model, f)
    with open(models_dir / f"team_goals_{version}.pkl", "wb") as f:
        pickle.dump(dc_model, f)
    with open(models_dir / f"points_{version}.pkl", "wb") as f:
        pickle.dump(points_models, f)

    elapsed = time.time() - t0
    return {
        "version": version,
        "train_seasons": train_seasons,
        "holdout_season": holdout_season,
        "overall": overall,
        "by_position": metrics_by_position,
        "calibration": calibration,
        "elapsed_seconds": elapsed,
        "beats_xp": overall["model_rmse"] < overall["xp_rmse"]
        and overall["model_spearman"] > overall["xp_spearman"],
        "beats_naive": overall["model_rmse"] < overall["naive_rmse"]
        and overall["model_spearman"] > overall["naive_spearman"],
    }


def render_report(result: dict) -> str:
    lines = [
        "# Phase 3 validation report",
        "",
        f"Trained on {result['train_seasons']}, held out **{result['holdout_season']}**. "
        f"Model version `{result['version']}`. Trained in {result['elapsed_seconds']:.1f}s.",
        "",
        "## Overall (all positions)",
        "",
        "| | RMSE | Spearman |",
        "|---|---|---|",
        f"| **Our model** | {result['overall']['model_rmse']:.3f} | "
        f"{result['overall']['model_spearman']:.3f} |",
        f"| FPL `xP` baseline | {result['overall']['xp_rmse']:.3f} | "
        f"{result['overall']['xp_spearman']:.3f} |",
        f"| Naive 5-GW average | {result['overall']['naive_rmse']:.3f} | "
        f"{result['overall']['naive_spearman']:.3f} |",
        "",
        f"**Beats FPL xP baseline:** {'YES' if result['beats_xp'] else 'NO'}  ",
        f"**Beats naive 5-GW average:** {'YES' if result['beats_naive'] else 'NO'}",
        "",
        "## By position",
        "",
        "| Position | n | Model RMSE | Model Spearman | xP RMSE | xP Spearman "
        "| Naive RMSE | Naive Spearman |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for position, m in result["by_position"].items():
        lines.append(
            f"| {position} | {m['n']} | {m['model_rmse']:.3f} | {m['model_spearman']:.3f} "
            f"| {m['xp_rmse']:.3f} | {m['xp_spearman']:.3f} "
            f"| {m['naive_rmse']:.3f} | {m['naive_spearman']:.3f} |"
        )
    lines += [
        "",
        "## Minutes-model calibration (P(60+ mins) bucket vs realized rate)",
        "",
        "| Predicted P(60+) bucket | n | Realized 60+ rate |",
        "|---|---|---|",
    ]
    for _, row in result["calibration"].iterrows():
        lines.append(f"| {row.iloc[0]} | {row['n']} | {row['realized_rate']:.3f} |")
    return "\n".join(lines) + "\n"
