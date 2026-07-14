"""Production pipeline: train -> project -> optimize, for real (not backtest) use.

Distinct from models/train.py's run_for_split()/run(), which hold out a season to
validate against. This module trains on EVERY available season (no holdout) and
projects/optimizes for actual deployment — the CLI's `project`/`optimize` commands.

Pre-26/27-launch limitation, not an oversight: models/horizon.py's leak-safe
multi-step forecast needs the TARGET season's own fixture list to swap in real
per-gameweek opponent/venue/DGW context. 2025-26 is finished (GW38 was the last
gameweek) and 26/27's fixtures don't exist yet — there is no future fixture list to
build a genuine horizon forecast from. Rather than fabricate one, `project`/
`optimize` fall back to a flat single-gameweek EV (decay-summed, no per-gameweek
fixture awareness) for this demo/pre-launch period specifically — clearly labeled
`is_live: false` in the published site. Once 26/27 launches and its fixture list
exists, this reverts to the real per-gameweek horizon forecast automatically (see
`generate_projections`'s branch on whether future fixtures exist).
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandas as pd

from fplscout.models import horizon, minutes, points, team_goals
from fplscout.models.dataset import load_dataset
from fplscout.models.train import _team_goals_lookup, project_gw

ALL_SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
HORIZON = 8
DECAY = 0.84


@dataclass
class ProductionModels:
    minutes_model: object
    dc_model: team_goals.DixonColesModel
    points_models: dict
    version: str
    train_seasons: list[str]


def train_production(con: duckdb.DuckDBPyConnection, models_dir: Path) -> ProductionModels:
    """Trains on every available season — no holdout, this is for real use.

    Seasons are derived from the DB, not hardcoded, so once live ingestion
    (ingest/live_gw.py) writes 26/27 rows, weekly retraining picks them up
    automatically. Same for the Dixon-Coles fit: ALL finished fixtures in the
    DB, current season included — `project` retrains on every run, so this IS
    the in-season refit (issue #3) for the live path; time decay already
    weights the freshest matches highest."""
    seasons = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT season FROM player_gw_history ORDER BY season"
        ).fetchall()
    ]
    train_df = load_dataset(con, seasons)
    minutes_model = minutes.train(train_df)

    fixtures = con.execute("SELECT * FROM fixtures").df()
    teams = con.execute("SELECT season, team_id, code FROM teams").df()
    dc_model = team_goals.fit(fixtures, teams)

    mins_proba = minutes.predict_proba(minutes_model, train_df)
    tg_lookup = _team_goals_lookup(dc_model, train_df, teams)
    train_full = points.add_model_features(train_df, mins_proba, tg_lookup)
    points_models = points.train(train_full)

    version = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    models_dir.mkdir(parents=True, exist_ok=True)
    with open(models_dir / f"production_{version}_minutes.pkl", "wb") as f:
        pickle.dump(minutes_model, f)
    with open(models_dir / f"production_{version}_team_goals.pkl", "wb") as f:
        pickle.dump(dc_model, f)
    with open(models_dir / f"production_{version}_points.pkl", "wb") as f:
        pickle.dump(points_models, f)

    return ProductionModels(
        minutes_model=minutes_model,
        dc_model=dc_model,
        points_models=points_models,
        version=version,
        train_seasons=seasons,
    )


def load_production_models(models_dir: Path, version: str) -> ProductionModels:
    """Reloads a ProductionModels bundle previously saved by train_production,
    keyed by its version string — so `optimize` can reuse the exact models
    `project` trained without retraining."""
    with open(models_dir / f"production_{version}_minutes.pkl", "rb") as f:
        minutes_model = pickle.load(f)
    with open(models_dir / f"production_{version}_team_goals.pkl", "rb") as f:
        dc_model = pickle.load(f)
    with open(models_dir / f"production_{version}_points.pkl", "rb") as f:
        points_models = pickle.load(f)
    return ProductionModels(
        minutes_model=minutes_model,
        dc_model=dc_model,
        points_models=points_models,
        version=version,
        train_seasons=ALL_SEASONS,
    )


def latest_reference_point(con: duckdb.DuckDBPyConnection) -> tuple[str, int]:
    """(season, gw) projections should target.

    Live season in progress (an unfinished event in `gameweeks`): the NEXT
    unplayed gameweek — the deadline actually being decided. Feature rows for
    it exist as upcoming-GW synthetic rows (issue #5, features/build.py).
    Otherwise (every season in the DB fully played — the pre-26/27 demo state):
    the most recently completed gameweek, as before."""
    live = con.execute(
        "SELECT season, MIN(event) FROM gameweeks WHERE NOT finished "
        "GROUP BY season ORDER BY season DESC LIMIT 1"
    ).fetchone()
    if live is not None:
        return live[0], live[1]
    row = con.execute(
        "SELECT season, MAX(gw) FROM player_gw_history "
        "WHERE season = (SELECT MAX(season) FROM player_gw_history) "
        "GROUP BY season"
    ).fetchone()
    return row[0], row[1]


def live_availability_factor(con: duckdb.DuckDBPyConnection) -> dict[int, float]:
    """code -> live availability factor read from `players`' bootstrap-static
    snapshot (see `fplscout refresh` / models/minutes.py::apply_availability).
    Inference-only — never touches training data."""
    rows = con.execute(
        "SELECT code, status, chance_of_playing_next_round FROM players"
    ).fetchall()
    factor = {}
    for code, status, chance in rows:
        if chance is not None:
            factor[code] = chance / 100.0
        elif status is None or status == "a":
            # NULL status = never synced (refresh hasn't run since the column
            # existed): no information means NO adjustment, not "ruled out".
            # Treating NULL as 0 silently zeroed every player's minutes — a
            # real bug caught by the season-kickoff dress rehearsal.
            factor[code] = 1.0
        else:
            factor[code] = 0.0
    return factor


def generate_projections(
    con: duckdb.DuckDBPyConnection,
    models: ProductionModels,
    season: str,
    gw: int,
) -> pd.DataFrame:
    """Single-gameweek projection at (season, gw), written to the `projections`
    table. Returns the projection DataFrame (code, ev_points, q10/q90, position)."""
    season_df = load_dataset(con, [season], require_targets=False)
    target_df = season_df[season_df["gw"] == gw]
    teams = con.execute("SELECT season, team_id, code FROM teams WHERE season = ?", [season]).df()

    preds, feat = project_gw(
        models.minutes_model, models.dc_model, models.points_models, target_df, teams,
        availability_factor=live_availability_factor(con),
    )
    # preds and feat share target_df's row order/length exactly (project_gw derives
    # both from it without reordering) — positional concat, not a merge on `code`,
    # since a double-gameweek player has two rows for the same code and a merge on
    # `code` alone would cross-join them into duplicates.
    out = preds.reset_index(drop=True).copy()
    extra = feat[["expected_minutes", "mins_p60_plus", "mins_p0", "clean_sheet_prob"]].reset_index(
        drop=True
    )
    out[extra.columns] = extra
    # one row per player for this gw: DGW fixtures are summed into a single total_ev-
    # style figure, matching how the optimizer and horizon.py treat DGWs elsewhere.
    out = out.groupby("code", as_index=False).agg(
        ev_points=("ev_points", "sum"),
        q10_points=("q10_points", "sum"),
        q90_points=("q90_points", "sum"),
        expected_minutes=("expected_minutes", "sum"),
        mins_p60_plus=("mins_p60_plus", "max"),
        mins_p0=("mins_p0", "min"),
        clean_sheet_prob=("clean_sheet_prob", "max"),
    )

    generated_at = datetime.now(UTC)
    rows_df = pd.DataFrame(
        {
            "season": season,
            "gw": gw,
            "code": out["code"],
            "model_version": models.version,
            "ev_points": out["ev_points"],
            "q10_points": out["q10_points"],
            "q90_points": out["q90_points"],
            "ev_minutes": out["expected_minutes"],
            "p_appearance": 1.0 - out["mins_p0"],
            "p_60_plus": out["mins_p60_plus"],
            "p_clean_sheet": out["clean_sheet_prob"],
            "generated_at": generated_at,
        }
    )
    con.execute(
        "DELETE FROM projections WHERE season = ? AND gw = ? AND model_version = ?",
        [season, gw, models.version],
    )
    # register explicitly rather than relying on DuckDB's implicit replacement
    # scan of local variable names (which linters can't see, and which breaks if
    # this is ever refactored into a helper where `rows_df` isn't in scope).
    con.register("projection_rows", rows_df)
    con.execute("INSERT INTO projections BY NAME SELECT * FROM projection_rows")
    con.unregister("projection_rows")
    return out


def roster_snapshot(con: duckdb.DuckDBPyConnection, season: str, gw: int) -> pd.DataFrame:
    """code, position, team_id, price, web_name as of (season, gw) — the
    optimizer's player universe. Reads the `features` table rather than
    player_gw_history so it also works for an UNPLAYED upcoming gameweek
    (issue #5's synthetic rows, priced from live now_cost); for played
    gameweeks the two are equivalent, since features derive from history."""
    return con.execute(
        """
        SELECT f.code, f.position, f.team_id, f.value AS price, p.web_name
        FROM features f
        JOIN players p ON p.code = f.code
        WHERE f.season = ? AND f.gw = ?
        QUALIFY ROW_NUMBER() OVER (PARTITION BY f.code ORDER BY f.fixture_id) = 1
        """,
        [season, gw],
    ).df()


def total_ev_for_optimizer(
    con: duckdb.DuckDBPyConnection,
    models: ProductionModels,
    season: str,
    gw: int,
    projections: pd.DataFrame,
) -> pd.Series:
    """code -> total_ev for the optimizer's horizon input. Uses the real
    fixture-aware multi-step forecast (models/horizon.py) if the target season
    has fixtures beyond `gw` (a genuine live season in progress); otherwise falls
    back to a flat decay-summed single-gameweek EV (see module docstring — this
    is the pre-26/27-launch demo path, not the intended long-run behavior)."""
    max_gw = con.execute(
        "SELECT MAX(event) FROM fixtures WHERE season = ?", [season]
    ).fetchone()[0]
    if max_gw is not None and max_gw > gw:
        season_df = load_dataset(con, [season], require_targets=False)
        base_rows = season_df[season_df["gw"] == gw]
        fixtures = con.execute("SELECT * FROM fixtures WHERE season = ?", [season]).df()
        teams = con.execute(
            "SELECT season, team_id, code, strength FROM teams WHERE season = ?", [season]
        ).df()
        return horizon.build_horizon_ev(
            models.minutes_model, models.dc_model, models.points_models,
            base_rows, fixtures, teams, decision_gw=gw, horizon=HORIZON, decay=DECAY,
            max_gw=max_gw, availability_factor=live_availability_factor(con),
        )
    decay_sum = sum(DECAY**h for h in range(HORIZON))
    return (projections.set_index("code")["ev_points"] * decay_sum).rename("total_ev")
