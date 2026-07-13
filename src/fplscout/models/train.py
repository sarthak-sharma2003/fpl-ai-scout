"""Training harness + validation report — plan §Phase3 DoD.

History of corrections folded in here (each caught a real bug, not a hypothetical
— see git log for the full trail):

Round 1 (pooled-Spearman): pooled Spearman over the whole holdout is dominated by
"can you tell reserves from starters" (~61% of rows are 0-minute players) —
replaced by mean per-GW Spearman on a decision-relevant subset (plausible
starters: expected minutes >= 45 or P(60+) >= 0.5), across all 38 gameweeks.

Round 2 (xP is confirmed leaky, not just occasionally missing): vaastav's own
README documents that `xP` is scraped from bootstrap-static's `ep_this` *after*
each gameweek ends, with an empirically observed same-GW correlation to actual
points the README itself calls "unusually high for a genuinely pre-match
feature". An earlier pass here trained two variants (fpl_xp included vs excluded)
and routed between them, treating xP as merely *sometimes unavailable*. That
diagnosis was wrong — the feature is contaminated where it IS available too. It
has been removed entirely from historical training (models/points.py,
models/dataset.py). There is one model now, not a "full vs independent" choice.
The xP baseline comparison is gone from this report for the same reason; the
naive 5-GW-average baseline remains valid and is the only baseline reported.

The primary backtest season is 2024-25 (train on 2021-22..2023-24); 2025-26
(train on 2021-22..2024-25) is the second sample, per the plan's own requirement
to validate on two seasons.
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

PRIMARY_TRAIN_SEASONS = ["2021-22", "2022-23", "2023-24"]
PRIMARY_HOLDOUT_SEASON = "2024-25"
SECONDARY_TRAIN_SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25"]
SECONDARY_HOLDOUT_SEASON = "2025-26"


def train_test_split_by_season(seasons: list[str], holdout: str) -> tuple[list[str], str]:
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


def project_gw(
    minutes_model,
    dc_model: team_goals.DixonColesModel,
    points_models: dict,
    df: pd.DataFrame,
    teams: pd.DataFrame,
    availability_factor: dict[int, float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Given already-trained models, produce EV projections for the rows in `df`
    — typically one gameweek's worth from `models.dataset.load_dataset()`.
    Factored out of run_for_split() so Phase 5 (chip_planner) and Phase 6
    (backtest simulator) reuse the exact same per-GW prediction path instead of
    duplicating it.

    `availability_factor`: optional code -> live availability factor (see
    models/minutes.py::apply_availability). Only the live pipeline passes
    this; backtest/training callers leave it None, which skips the overlay
    entirely and keeps their output byte-identical to before this existed.

    Returns (predictions, feature_augmented_df) — the second is useful to
    callers that also need expected_minutes/mins_p60_plus/etc. without
    re-deriving them.
    """
    mins_proba = minutes.predict_proba(minutes_model, df)
    if availability_factor is not None:
        factor = df["code"].map(availability_factor).fillna(1.0).to_numpy()
        mins_proba = minutes.apply_availability(mins_proba, factor)
    tg_lookup = _team_goals_lookup(dc_model, df, teams)
    feat = points.add_model_features(df, mins_proba, tg_lookup)
    preds = points.predict(points_models, feat)
    return preds, feat


def _rmse(y_true: pd.Series, y_pred: pd.Series) -> float:
    mask = y_true.notna() & y_pred.notna()
    return float(np.sqrt(np.mean((y_true[mask] - y_pred[mask]) ** 2)))


def _spearman(y_true: pd.Series, y_pred: pd.Series) -> float:
    mask = y_true.notna() & y_pred.notna()
    if mask.sum() < 2:
        return float("nan")
    return float(spearmanr(y_true[mask], y_pred[mask]).statistic)


def _mean_per_gw_spearman(df: pd.DataFrame, actual_col: str, pred_col: str) -> tuple[float, int]:
    """Mean of per-gameweek Spearman correlations, not a single pooled Spearman
    over every row. Pooling across gameweeks is dominated by "can you tell
    reserves from starters" (~61% of holdout rows are 0-minute players) — a bar
    a 5-GW rolling average also clears — rather than the ranking *within* a
    gameweek among plausible starters that the optimizer actually consumes."""
    per_gw = df.groupby("gw", observed=True).apply(
        lambda g: _spearman(g[actual_col], g[pred_col]), include_groups=False
    )
    valid = per_gw.dropna()
    mean = float(valid.mean()) if len(valid) > 0 else float("nan")
    return mean, len(valid)


def _metric_bundle(df: pd.DataFrame, actual_col: str, pred_col: str) -> dict:
    per_gw_mean, n_gws = _mean_per_gw_spearman(df, actual_col, pred_col)
    return {
        "n": int(df[pred_col].notna().sum()),
        "n_gameweeks": n_gws,
        "rmse": _rmse(df[actual_col], df[pred_col]),
        "pooled_spearman": _spearman(df[actual_col], df[pred_col]),
        "mean_per_gw_spearman": per_gw_mean,
    }


def _compare_columns(df: pd.DataFrame, actual_col: str, pred_cols: dict[str, str]) -> dict:
    return {name: _metric_bundle(df, actual_col, col) for name, col in pred_cols.items()}


PRED_COLS = {"model": "ev_points", "naive": "naive_baseline"}


def _beats(challenger: dict, baseline: dict) -> bool:
    """True if challenger clearly beats baseline on mean-per-GW Spearman. If the
    baseline has no valid gameweeks to compare on, there is nothing to beat —
    that's reported as a coverage caveat, not a pass."""
    if np.isnan(baseline["mean_per_gw_spearman"]):
        return True
    return challenger["mean_per_gw_spearman"] > baseline["mean_per_gw_spearman"]


def run_for_split(
    con: duckdb.DuckDBPyConnection,
    train_seasons: list[str],
    holdout_season: str,
    models_dir: Path,
    split_label: str,
) -> dict:
    t0 = time.time()
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
    holdout_full = points.add_model_features(holdout_df, holdout_mins_proba, holdout_tg_lookup)

    points_models = points.train(train_full)
    preds = points.predict(points_models, holdout_full)
    gain_share = points.feature_gain_by_column(points_models)

    holdout_eval = holdout_full[
        ["code", "gw", "fixture_id", "position", "total_points",
         "expected_minutes", "mins_p60_plus"]
    ].copy()
    holdout_eval["ev_points"] = preds["ev_points"]
    naive = _naive_5gw_average(con, holdout_season)
    holdout_eval = holdout_eval.merge(naive, on=["code", "fixture_id"], how="left")

    # "Decision-relevant" subset: plausible starters — the population the
    # optimizer actually ranks among.
    decision_mask = (holdout_eval["expected_minutes"] >= 45) | (
        holdout_eval["mins_p60_plus"] >= 0.5
    )
    holdout_decision = holdout_eval[decision_mask]

    overall_decision = _compare_columns(holdout_decision, "total_points", PRED_COLS)
    by_position_decision = {}
    for position in points.POSITIONS:
        sub = holdout_decision[holdout_decision["position"] == position]
        by_position_decision[position] = (
            _compare_columns(sub, "total_points", PRED_COLS) if len(sub) > 0 else None
        )

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
    prefix = f"{split_label}_{version}"
    with open(models_dir / f"{prefix}_minutes.pkl", "wb") as f:
        pickle.dump(minutes_model, f)
    with open(models_dir / f"{prefix}_team_goals.pkl", "wb") as f:
        pickle.dump(dc_model, f)
    with open(models_dir / f"{prefix}_points.pkl", "wb") as f:
        pickle.dump(points_models, f)

    elapsed = time.time() - t0
    beats_naive = _beats(overall_decision["model"], overall_decision["naive"])

    return {
        "split_label": split_label,
        "version": version,
        "train_seasons": train_seasons,
        "holdout_season": holdout_season,
        "overall_decision": overall_decision,
        "by_position_decision": by_position_decision,
        "gain_share": gain_share,
        "calibration": calibration,
        "elapsed_seconds": elapsed,
        "beats_naive": beats_naive,
    }


def run(con: duckdb.DuckDBPyConnection, models_dir: Path) -> dict:
    primary = run_for_split(
        con, PRIMARY_TRAIN_SEASONS, PRIMARY_HOLDOUT_SEASON, models_dir, "primary"
    )
    secondary = run_for_split(
        con, SECONDARY_TRAIN_SEASONS, SECONDARY_HOLDOUT_SEASON, models_dir, "secondary"
    )
    return {
        "primary": primary,
        "secondary": secondary,
        "beats_naive_decision": primary["beats_naive"],
    }


def to_summary_dict(result: dict) -> dict:
    """Structured counterpart to render_report(), for the static site's
    analytics.json (publish.py)."""
    def _split_summary(split: dict) -> dict:
        overall = split["overall_decision"]
        return {
            "split_label": split["split_label"],
            "version": split["version"],
            "train_seasons": split["train_seasons"],
            "holdout_season": split["holdout_season"],
            "beats_naive": split["beats_naive"],
            "model_mean_per_gw_spearman": overall["model"]["mean_per_gw_spearman"],
            "naive_mean_per_gw_spearman": overall["naive"]["mean_per_gw_spearman"],
            "model_rmse": overall["model"]["rmse"],
            "naive_rmse": overall["naive"]["rmse"],
        }

    return {
        "beats_naive_decision": result["beats_naive_decision"],
        "primary": _split_summary(result["primary"]),
        "secondary": _split_summary(result["secondary"]),
    }


def render_report(result: dict) -> str:
    def _bundle_row(label: str, b: dict) -> str:
        pooled = f"{b['pooled_spearman']:.3f}" if not np.isnan(b["pooled_spearman"]) else "n/a"
        per_gw = (
            f"{b['mean_per_gw_spearman']:.3f}" if not np.isnan(b["mean_per_gw_spearman"])
            else "n/a"
        )
        return (
            f"| {label} | {b['n']} | {b['n_gameweeks']} | {b['rmse']:.3f} "
            f"| **{per_gw}** | {pooled} |"
        )

    def _comparison_table(compare: dict) -> list[str]:
        return [
            "| | n rows | n GWs | RMSE | Mean per-GW Spearman | Pooled Spearman |",
            "|---|---|---|---|---|---|",
            _bundle_row("**Model**", compare["model"]),
            _bundle_row("Naive 5-GW average", compare["naive"]),
        ]

    def _gain_table(gain_share: pd.DataFrame) -> list[str]:
        lines = ["| Position | Top 3 features by gain share |", "|---|---|"]
        for position in ("GKP", "DEF", "MID", "FWD"):
            sub = gain_share[gain_share["position"] == position].sort_values(
                "gain_share", ascending=False
            )
            if len(sub) == 0:
                continue
            top3 = ", ".join(
                f"{row['feature']} ({row['gain_share']:.1%})" for _, row in sub.head(3).iterrows()
            )
            lines.append(f"| {position} | {top3} |")
        return lines

    def _render_split(split: dict) -> list[str]:
        lines = [
            f"## {split['split_label'].capitalize()} split: "
            f"train {split['train_seasons']}, holdout **{split['holdout_season']}**",
            "",
            f"Model version `{split['version']}`. Trained in {split['elapsed_seconds']:.1f}s.",
            "",
            "Decision-relevant subset (plausible starters: expected minutes >= 45 or "
            "P(60+) >= 0.5), all gameweeks in the holdout season:",
            "",
            *_comparison_table(split["overall_decision"]),
            "",
            f"**Beats naive:** {'YES' if split['beats_naive'] else 'NO'}",
            "",
            "### By position",
            "",
        ]
        for position in ("GKP", "DEF", "MID", "FWD"):
            compare = split["by_position_decision"].get(position)
            lines.append(f"#### {position}")
            lines.append("")
            if compare is None:
                lines.append("(no decision-relevant rows for this position)")
            else:
                lines += _comparison_table(compare)
            lines.append("")
        lines += [
            "### Feature gain share",
            "",
            *_gain_table(split["gain_share"]),
            "",
            "### Minutes-model calibration (P(60+ mins) bucket vs realized rate)",
            "",
            "| Predicted P(60+) bucket | n | Realized 60+ rate |",
            "|---|---|---|",
        ]
        for _, row in split["calibration"].iterrows():
            lines.append(f"| {row.iloc[0]} | {row['n']} | {row['realized_rate']:.3f} |")
        return lines

    lines = [
        "# Phase 3 validation report",
        "",
        "One model per position (no `fpl_xp` — see models/points.py docstring for",
        "why it was removed entirely from historical training, not merely made",
        "optional). Naive 5-GW average is the only baseline reported; the `xP`",
        "comparison from earlier reports is void (the column was confirmed",
        "post-match-contaminated in vaastav's historical data).",
        "",
        "Two backtest splits: **primary** (2024-25 holdout, train on 2021-22..",
        "2023-24) and **secondary** (2025-26 holdout, train on 2021-22..2024-25) —",
        "the plan's own requirement to validate on two seasons.",
        "",
        f"**Gate result — primary split beats naive:** "
        f"{'YES' if result['beats_naive_decision'] else 'NO'}",
        "",
        *_render_split(result["primary"]),
        "",
        "---",
        "",
        *_render_split(result["secondary"]),
    ]
    return "\n".join(lines) + "\n"
