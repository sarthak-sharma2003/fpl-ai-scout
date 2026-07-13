"""Leak-safe multi-step forecast — plan §Phase3's original spec ("features as-of
now, fixture context per target GW"), never actually implemented until this pass.

`backtest/simulator.py`'s first `_compute_horizon_ev` averaged decayed EV across
future gameweeks using each future gameweek's OWN row from the features table —
which reads real match outcomes from the gameweeks in between (see that function's
git history for the concrete evidence: Salah's roll5_points at gw10 differs from
gw3). The fix removed that, but also removed all fixture-awareness, since a flat
"current gw's EV, decayed" proxy can't tell a DGW from a BGW ahead of time.

This module restores fixture-awareness without restoring the leak: it freezes
every player-*form* feature (rolling stats, price, position — anything reflecting
"how good is this player right now") at the decision gameweek, and swaps in only
what's legitimately knowable in advance for each future target gameweek — the
fixture list itself (opponent, venue, DGW/BGW count) and the Dixon-Coles model's
opponent-specific attack/defense parameters (fit once on training data; doesn't
update within a season, so using it for a future opponent isn't reading anything
that hasn't happened yet). Minutes-model uncertainty widens with horizon distance
via a simple linear blend toward the position-level average probability — a
pragmatic approximation, not a rigorously re-derived k-step-ahead minutes model.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fplscout.models import minutes, points, team_goals
from fplscout.models.dataset import load_dataset

UNCERTAINTY_WIDEN_PER_STEP = 0.15  # h=0: no widening; h>=6: fully at position average


def _team_fixtures_long(fixtures: pd.DataFrame) -> pd.DataFrame:
    """One row per (season, gw, team_id, fixture_id): opponent, venue, fdr, and
    rest_days (days since that team's previous fixture — computed from the
    fixture calendar itself, which is public knowledge in advance, not from any
    match outcome)."""
    home = fixtures[
        ["season", "event", "fixture_id", "kickoff_time", "team_h", "team_a",
         "team_h_difficulty"]
    ].rename(
        columns={
            "event": "gw", "team_h": "team_id", "team_a": "opponent_team_id",
            "team_h_difficulty": "target_fdr",
        }
    )
    home["was_home_target"] = True
    away = fixtures[
        ["season", "event", "fixture_id", "kickoff_time", "team_a", "team_h",
         "team_a_difficulty"]
    ].rename(
        columns={
            "event": "gw", "team_a": "team_id", "team_h": "opponent_team_id",
            "team_a_difficulty": "target_fdr",
        }
    )
    away["was_home_target"] = False
    long = pd.concat([home, away], ignore_index=True)
    long = long.sort_values(["team_id", "kickoff_time"])
    prev_kickoff = long.groupby("team_id")["kickoff_time"].shift(1)
    long["target_rest_days"] = (
        pd.to_datetime(long["kickoff_time"]) - pd.to_datetime(prev_kickoff)
    ).dt.total_seconds() / 86400
    return long


def _dc_matchup_stats(
    dc_model: team_goals.DixonColesModel,
    own_team_id: pd.Series,
    opponent_team_id: pd.Series,
    was_home: pd.Series,
    id_to_code: dict[int, int],
) -> pd.DataFrame:
    own_code = own_team_id.map(id_to_code).to_numpy()
    opp_code = opponent_team_id.map(id_to_code).to_numpy()
    n = len(own_team_id)
    team_xg_for = np.full(n, np.nan)
    team_xg_against = np.full(n, np.nan)
    clean_sheet_prob = np.full(n, np.nan)
    for i in range(n):
        oc, pc, home = own_code[i], opp_code[i], bool(was_home.iloc[i])
        if pd.isna(oc) or pd.isna(pc):
            continue
        if home:
            lam, mu = dc_model.expected_goals(int(oc), int(pc))
            cs, _ = dc_model.clean_sheet_prob(int(oc), int(pc))
        else:
            mu, lam = dc_model.expected_goals(int(pc), int(oc))
            _, cs = dc_model.clean_sheet_prob(int(pc), int(oc))
        team_xg_for[i] = lam
        team_xg_against[i] = mu
        clean_sheet_prob[i] = cs
    return pd.DataFrame(
        {
            "team_xg_for": team_xg_for,
            "team_xg_against": team_xg_against,
            "clean_sheet_prob": clean_sheet_prob,
        },
        index=own_team_id.index,
    )


def build_horizon_ev(
    minutes_model,
    dc_model: team_goals.DixonColesModel,
    points_models: dict,
    base_rows: pd.DataFrame,
    fixtures: pd.DataFrame,
    teams: pd.DataFrame,
    decision_gw: int,
    horizon: int,
    decay: float,
    max_gw: int,
    availability_factor: dict[int, float] | None = None,
) -> pd.Series:
    """base_rows: this season's leak-safe feature rows AT decision_gw only (one
    per player) — the frozen "how good is this player right now" snapshot.
    Returns code -> total_ev, decay-summed across up to `horizon` future
    gameweeks with real per-gameweek fixture context swapped in.

    `availability_factor`: optional code -> live availability factor (see
    models/minutes.py::apply_availability). Fades linearly back to 1.0 by
    h=4 steps ahead (an injured player may return within the horizon) —
    # ponytail: naive linear fade, upgrade path is parsing return dates from
    `news`. Only the live pipeline passes this; backtest callers leave it
    None and get byte-identical output to before this existed."""
    if len(base_rows) == 0:
        return pd.Series(dtype=float)

    # A player with a double gameweek AT decision_gw itself has two rows here;
    # their rolling-form features are identical either way (both computed from
    # data strictly before decision_gw, independent of which of decision_gw's
    # own fixtures they belong to) — one frozen snapshot per player, not one per
    # fixture, is both correct and required (code must be a unique index below).
    base_rows = base_rows.drop_duplicates(subset="code", keep="first").reset_index(drop=True)
    base_mins_proba = minutes.predict_proba(minutes_model, base_rows)

    position_avg_proba: dict[str, np.ndarray] = {}
    for position in base_rows["position"].unique():
        mask = (base_rows["position"] == position).to_numpy()
        position_avg_proba[position] = base_mins_proba[mask].mean(axis=0)

    team_fixtures = _team_fixtures_long(fixtures)
    id_to_code = dict(zip(teams["team_id"], teams["code"], strict=False))
    strength_by_team = dict(zip(teams["team_id"], teams["strength"], strict=False))

    # Drop every column that's specific to the DECISION gw's own fixture — all of
    # these get replaced per target gw by the merge below; keeping them around
    # would either collide with the merge (pandas would silently suffix both to
    # _x/_y) or, worse, silently leave the decision-gw's own fixture context
    # attached to a different target gw's row.
    base_frozen = base_rows.drop(
        columns=["fdr", "opponent_strength", "is_dgw", "rest_days",
                 "opponent_team_id", "was_home", "fixture_id", "kickoff_time",
                 "gw", "season"]
    )
    total_ev: dict[int, float] = {}

    for h in range(horizon):
        target_gw = decision_gw + h
        if target_gw > max_gw:
            break
        tf = team_fixtures[team_fixtures["gw"] == target_gw]
        if len(tf) == 0:
            continue  # nobody plays this gw in the fixture list (shouldn't happen
            # league-wide, but guards a malformed fixture table)

        dgw_counts = tf.groupby("team_id").size()
        merged = base_frozen.merge(tf, on="team_id", how="inner")
        if len(merged) == 0:
            continue

        merged["gw"] = target_gw
        merged["season"] = tf["season"].iloc[0]
        merged["is_dgw"] = merged["team_id"].map(dgw_counts).fillna(1) >= 2
        merged["opponent_strength"] = merged["opponent_team_id"].map(strength_by_team)
        merged["fdr"] = merged["target_fdr"]
        merged["rest_days"] = merged["target_rest_days"]

        dc_stats = _dc_matchup_stats(
            dc_model, merged["team_id"], merged["opponent_team_id"],
            merged["was_home_target"], id_to_code,
        )
        merged["team_xg_for"] = dc_stats["team_xg_for"]
        merged["team_xg_against"] = dc_stats["team_xg_against"]
        merged["clean_sheet_prob"] = dc_stats["clean_sheet_prob"]

        widen = min(1.0, h * UNCERTAINTY_WIDEN_PER_STEP)
        orig_row_idx = base_rows.set_index("code").index.get_indexer(merged["code"])
        own_proba = base_mins_proba[orig_row_idx]
        pos_avg = np.array([position_avg_proba[p] for p in merged["position"]])
        widened_proba = (1 - widen) * own_proba + widen * pos_avg

        if availability_factor is not None:
            factor0 = merged["code"].map(availability_factor).fillna(1.0).to_numpy()
            factor_h = factor0 + (1 - factor0) * min(1.0, h / 4)
            widened_proba = minutes.apply_availability(widened_proba, factor_h)

        merged["mins_p0"] = widened_proba[:, 0]
        merged["mins_p1_59"] = widened_proba[:, 1]
        merged["mins_p60_plus"] = widened_proba[:, 2]
        merged["expected_minutes"] = widened_proba @ np.array([0.0, 30.0, 90.0])

        preds = points.predict(points_models, merged)
        gw_ev = preds.groupby(preds["code"])["ev_points"].sum()  # sums DGW fixtures
        for code, ev in gw_ev.items():
            if pd.isna(ev):
                continue
            total_ev[code] = total_ev.get(code, 0.0) + (decay**h) * ev

    return pd.Series(total_ev, name="total_ev")


def build_horizon_ev_all_gws(
    con,
    minutes_model,
    dc_model: team_goals.DixonColesModel,
    points_models: dict,
    season: str,
    horizon: int,
    decay: float,
    refit_train_fixtures: pd.DataFrame | None = None,
    refit_teams: pd.DataFrame | None = None,
) -> dict[int, pd.Series]:
    """Convenience wrapper for the backtest simulator: precomputes fixtures/teams
    once and returns {decision_gw: total_ev_series} for every gw in the season.

    If `refit_train_fixtures`/`refit_teams` are given, the Dixon-Coles model is
    refit per decision gw on those training fixtures plus the target season's
    finished fixtures strictly before that gw (team_goals.refit_with_target) —
    in-season strength drift tracking, ~1.2s/fit. Otherwise `dc_model` is used
    as-is for every gw (old behavior)."""
    season_df = load_dataset(con, [season])
    fixtures = con.execute(
        "SELECT * FROM fixtures WHERE season = ?", [season]
    ).df()
    teams = con.execute(
        "SELECT season, team_id, code, strength FROM teams WHERE season = ?", [season]
    ).df()
    max_gw = int(fixtures["event"].max())

    result = {}
    for gw in range(1, max_gw + 1):
        gw_dc_model = dc_model
        if refit_train_fixtures is not None:
            gw_dc_model = team_goals.refit_with_target(
                refit_train_fixtures, fixtures, refit_teams, before_gw=gw
            )
        base_rows = season_df[season_df["gw"] == gw]
        result[gw] = build_horizon_ev(
            minutes_model, gw_dc_model, points_models, base_rows, fixtures, teams,
            decision_gw=gw, horizon=horizon, decay=decay, max_gw=max_gw,
        )
    return result
