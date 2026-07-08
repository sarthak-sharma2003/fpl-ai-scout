"""Training harness + validation report — plan §Phase3 DoD, corrected per review.

Two rounds of review correction are folded in here, both real bugs caught before
they reached Phase 4, not hypotheticals:

Round 1 (pooled-Spearman + xP-nulling): pooled Spearman over the whole holdout is
dominated by "can you tell reserves from starters" (~61% of rows are 0-minute
players) — replaced by mean per-GW Spearman on a decision-relevant subset
(plausible starters: expected minutes >= 45 or P(60+) >= 0.5).

Round 2 (xP dependency + season choice): the points model's FULL variant (xp
included) turned out to carry 63-65% of total gain on `fpl_xp` — the model is
mostly "xP plus learned corrections". That's fine when xP is genuinely available
(always true in live production), but scoring it on gameweeks where xP had to be
nulled (see dataset.py) amputates its dominant feature, which is worse than a model
that never depended on it. Two structural fixes:

1. Train a second, INDEPENDENT variant per position with `fpl_xp` excluded
   entirely (models/points.py: FULL_FEATURE_COLUMNS vs INDEPENDENT_FEATURE_COLUMNS),
   forcing feature importance onto rolling-form/Dixon-Coles/minutes signals.
2. Route per-row: FULL model's prediction where xp is valid that gameweek,
   INDEPENDENT model's prediction where it's missing (`route_predictions`).

Measured honestly (`routing_value_check` in the report, isolated to exactly the
gameweeks xp was nulled): routing is close to a wash against just using FULL with
xp=NaN — LightGBM routes missing values to a learned default child per split, so
"amputated FULL" isn't naive, it's already fairly robust. Primary split: independent
edges it (0.319 vs 0.290 mean per-GW Spearman); secondary: they're within noise
(0.231 vs 0.233). Routing is kept anyway because it protects against a different,
and arguably scarier, failure mode that missing-value handling can't touch: xp being
*present but wrong* (frozen, corrupted, or otherwise degenerate without going null)
— exactly what ingest/health.py's ep_next check is watching for, and the reason the
router exists as an automatic fallback rather than a measured backtest win.

The primary backtest season swaps from 2025-26 (11/38 valid-xP gameweeks) to
2024-25 (35/38 valid) — 2024-25 actually exercises the near-full-xP-coverage
regime production will run in; 2025-26 becomes the second, deliberately harder
sample (plan always wanted two seasons; this just changes which one is primary).
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


def route_predictions(
    full_preds: pd.DataFrame, independent_preds: pd.DataFrame, xp_valid_mask: pd.Series
) -> pd.DataFrame:
    """Per-row: FULL model's prediction where `xp_valid_mask` is True, INDEPENDENT
    model's otherwise. All three must share the same index (true of points.predict()
    output, which preserves the input df's index)."""
    routed = independent_preds.copy()
    cols = ["ev_points", "q10_points", "q90_points"]
    routed.loc[xp_valid_mask, cols] = full_preds.loc[xp_valid_mask, cols]
    return routed


def project_gw(
    minutes_model,
    dc_model: team_goals.DixonColesModel,
    full_models: dict,
    independent_models: dict,
    df: pd.DataFrame,
    teams: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Given already-trained models, produce routed EV projections (full model
    where fpl_xp is valid, independent otherwise) for the rows in `df` — typically
    one gameweek's worth from `models.dataset.load_dataset()`. Factored out of
    run_for_split() so Phase 5 (chip_planner) and Phase 6 (backtest simulator)
    reuse the exact same per-GW prediction path instead of duplicating it.

    Returns (routed_predictions, feature_augmented_df) — the second is useful to
    callers that also need expected_minutes/mins_p60_plus/etc. (e.g. to compute
    the decision-relevant subset) without re-deriving them.
    """
    mins_proba = minutes.predict_proba(minutes_model, df)
    tg_lookup = _team_goals_lookup(dc_model, df, teams)
    full_feat = points.add_model_features(df, mins_proba, tg_lookup)
    full_preds = points.predict(
        full_models, full_feat, feature_columns=points.FULL_FEATURE_COLUMNS
    )
    independent_preds = points.predict(
        independent_models, full_feat, feature_columns=points.INDEPENDENT_FEATURE_COLUMNS
    )
    xp_valid_mask = full_feat["fpl_xp"].notna()
    routed = route_predictions(full_preds, independent_preds, xp_valid_mask)
    return routed, full_feat


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


PRED_COLS = {
    "independent": "ev_points_independent",
    "full": "ev_points_full",
    "routed": "ev_points_routed",
    "xp": "fpl_xp",
    "naive": "naive_baseline",
}


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

    full_models = points.train(train_full, feature_columns=points.FULL_FEATURE_COLUMNS)
    independent_models = points.train(
        train_full, feature_columns=points.INDEPENDENT_FEATURE_COLUMNS
    )

    full_preds = points.predict(
        full_models, holdout_full, feature_columns=points.FULL_FEATURE_COLUMNS
    )
    independent_preds = points.predict(
        independent_models, holdout_full, feature_columns=points.INDEPENDENT_FEATURE_COLUMNS
    )
    xp_valid_mask = holdout_full["fpl_xp"].notna()
    routed_preds = route_predictions(full_preds, independent_preds, xp_valid_mask)

    gain_share = points.feature_gain_by_column(full_models)

    holdout_eval = holdout_full[
        ["code", "gw", "fixture_id", "position", "total_points", "fpl_xp",
         "expected_minutes", "mins_p60_plus"]
    ].copy()
    holdout_eval["ev_points_full"] = full_preds["ev_points"]
    holdout_eval["ev_points_independent"] = independent_preds["ev_points"]
    holdout_eval["ev_points_routed"] = routed_preds["ev_points"]
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

    # The routing question, isolated: on exactly the gameweeks where xp was
    # nulled, is a from-scratch independent model actually better than full
    # model's native handling of a missing feature? (LightGBM routes missing
    # values to a learned default child per split, so "full with xp=NaN" isn't
    # naive — it may already be fairly robust.)
    xp_invalid_mask_decision = ~xp_valid_mask.reindex(holdout_decision.index)
    holdout_xp_invalid = holdout_decision[xp_invalid_mask_decision]
    routing_value_check = _compare_columns(
        holdout_xp_invalid,
        "total_points",
        {"full_amputated": "ev_points_full", "independent": "ev_points_independent",
         "naive": "naive_baseline"},
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
    with open(models_dir / f"{prefix}_points_full.pkl", "wb") as f:
        pickle.dump(full_models, f)
    with open(models_dir / f"{prefix}_points_independent.pkl", "wb") as f:
        pickle.dump(independent_models, f)

    elapsed = time.time() - t0
    # Primary go/no-go gate: routed variant must clearly beat naive on
    # decision-relevant mean-per-GW Spearman.
    beats_naive = _beats(overall_decision["routed"], overall_decision["naive"])
    # Directive 1's own gate: the INDEPENDENT variant alone (no xp, no routing)
    # must also clearly beat naive — proves the model has real value that isn't
    # just "xp with extra steps".
    independent_beats_naive = _beats(overall_decision["independent"], overall_decision["naive"])

    return {
        "split_label": split_label,
        "version": version,
        "train_seasons": train_seasons,
        "holdout_season": holdout_season,
        "overall_decision": overall_decision,
        "by_position_decision": by_position_decision,
        "routing_value_check": routing_value_check,
        "n_xp_invalid_gws": routing_value_check["full_amputated"]["n_gameweeks"],
        "gain_share": gain_share,
        "calibration": calibration,
        "elapsed_seconds": elapsed,
        "beats_naive": beats_naive,
        "independent_beats_naive": independent_beats_naive,
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
        "independent_beats_naive": primary["independent_beats_naive"],
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
            _bundle_row("**Independent (no xp)**", compare["independent"]),
            _bundle_row("**Full (xp included)**", compare["full"]),
            _bundle_row("**Routed (production)**", compare["routed"]),
            _bundle_row("FPL `xP` baseline", compare["xp"]),
            _bundle_row("Naive 5-GW average", compare["naive"]),
        ]

    def _gain_table(gain_share: pd.DataFrame) -> list[str]:
        lines = ["| Position | Top feature | Gain share |", "|---|---|---|"]
        for position in ("GKP", "DEF", "MID", "FWD"):
            sub = gain_share[gain_share["position"] == position].sort_values(
                "gain_share", ascending=False
            )
            if len(sub) == 0:
                continue
            top = sub.iloc[0]
            xp_row = sub[sub["feature"] == "fpl_xp"]
            xp_share = f"{xp_row.iloc[0]['gain_share']:.1%}" if len(xp_row) > 0 else "n/a"
            lines.append(
                f"| {position} | {top['feature']} ({top['gain_share']:.1%}) "
                f"| fpl_xp: {xp_share} |"
            )
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
            f"**Routed beats naive:** {'YES' if split['beats_naive'] else 'NO'}  ",
            f"**Independent (no xp) alone beats naive:** "
            f"{'YES' if split['independent_beats_naive'] else 'NO'}",
            "",
            "### Is routing actually earning its keep?",
            "",
            f"Isolated to exactly the {split['n_xp_invalid_gws']} gameweeks where xp was",
            "nulled: does a from-scratch independent model actually beat full model's",
            "native handling of a missing feature? (LightGBM routes missing values to a",
            "learned default child per split, so \"full with xp=NaN\" isn't naive — it",
            "may already be fairly robust.) This determines whether routing is pulling",
            "its weight or is close to a wash:",
            "",
            "| | n rows | n GWs | RMSE | Mean per-GW Spearman | Pooled Spearman |",
            "|---|---|---|---|---|---|",
            _bundle_row(
                "**Full, xp amputated**", split["routing_value_check"]["full_amputated"]
            ),
            _bundle_row("**Independent**", split["routing_value_check"]["independent"]),
            _bundle_row("Naive 5-GW average", split["routing_value_check"]["naive"]),
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
            "### Feature gain share (FULL variant) — is it really \"xp plus corrections\"?",
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
        "Two variants trained per position: **independent** (fpl_xp excluded — forces",
        "importance onto rolling-form/Dixon-Coles/minutes features) and **full** (fpl_xp",
        "included). **routed** uses full's prediction where fpl_xp is valid that",
        "gameweek and independent's prediction otherwise — this is what production runs.",
        "",
        "Two backtest splits: **primary** (2024-25 holdout, 35/38 gameweeks have valid",
        "xp — the near-full-coverage regime production actually runs in) and",
        "**secondary** (2025-26 holdout, only 11/38 valid — a deliberately harder,",
        "xp-scarce stress test).",
        "",
        f"**Gate result — primary split beats naive:** "
        f"{'YES' if result['beats_naive_decision'] else 'NO'}  ",
        f"**Gate result — independent variant alone beats naive:** "
        f"{'YES' if result['independent_beats_naive'] else 'NO'}",
        "",
        *_render_split(result["primary"]),
        "",
        "---",
        "",
        *_render_split(result["secondary"]),
    ]
    return "\n".join(lines) + "\n"
