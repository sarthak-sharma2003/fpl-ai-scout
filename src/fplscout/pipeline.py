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
import re
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

# Summer-form boost (ingest/summer.py). Hand-set and deliberately small: a World
# Cup goal is evidence, not a projection, and the model already prices everything
# it can see. The threshold is the point of the whole thing — score sits below it
# for all but a few dozen players, so almost everyone's multiplier is exactly 1.0
# and only an exceptional summer clears zero. World Cup goals outweigh pre-season
# ones because the opposition is: a hat-trick against a League Two side in July is
# not the same evidence as one against Brazil.
SUMMER_WC_WEIGHT = 1.5
SUMMER_PRESEASON_WEIGHT = 1.0
SUMMER_THRESHOLD = 3.0  # score below this -> no boost at all
SUMMER_PER_POINT = 0.015  # +1.5% EV per weighted goal above the threshold
# Capped at +5%, ~0.3pts on a 6-point striker. MEASURED, not guessed: sweeping
# this cap against the GW1 wildcard solve, the squad is unchanged below 2%, takes
# one swap at 2%, and settles on the same three swaps everywhere from 3% to 40%.
# Raising it to 10% picks a byte-identical fifteen — this boost only re-orders
# near-ties, and past those the next candidates are blocked by the budget and the
# 3-per-club rule, which no multiplier can buy past. 5% sits mid-plateau rather
# than on the 3% edge, so a nightly EV shift can't flip players in and out.
SUMMER_MAX_BOOST = 0.05
SUMMER_FADE_GWS = 6  # linear fade to nothing; by GW6 the rolling features know


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

    # Manual GW1 cold-start override: FPL's `status` field only flags injuries,
    # not benchings — a fit player the manager just won't pick still reads 'a'.
    # config/lineup_watch.csv lets us haircut minutes for players our preseason eye
    # (or team-news lookup) says won't nail a start. Self-dissolving: once real
    # gameweeks are ingested, roll5_started_share reflects reality — delete rows
    # as they become moot. Multiplies onto the live factor (a doubtful *and*
    # benched player stays doubtful).
    for code, start_prob in _lineup_watch_factor().items():
        if code in factor:  # only haircut players that actually exist
            factor[code] *= start_prob
    return factor


def _lineup_watch_factor(
    path: Path = Path("config/lineup_watch.csv"),
) -> dict[int, float]:
    """code -> manual start_prob from the lineup watchlist. Empty if no file."""
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    return dict(zip(df["code"].astype(int), df["start_prob"].astype(float), strict=True))


def summer_boost(con: duckdb.DuckDBPyConnection, season: str) -> dict[int, float]:
    """code -> EV multiplier (>1.0) for an exceptional summer. Inference-only,
    exactly like live_availability_factor above — never touches training data.

    Fades linearly to nothing over the first SUMMER_FADE_GWS gameweeks. Without
    that this would still be nudging a player in April on the strength of a July
    friendly, long after roll5/roll10 have measured the same thing directly and
    better. Players below the threshold are omitted rather than mapped to 1.0,
    so callers can treat an empty dict as "no adjustment".
    """
    played = con.execute(
        "SELECT count(*) FROM gameweeks WHERE season = ? AND finished", [season]
    ).fetchone()[0]
    fade = max(0.0, 1.0 - played / SUMMER_FADE_GWS)
    if fade == 0.0:
        return {}
    boost = {}
    for code, wc, preseason in con.execute(
        "SELECT code, wc_goals, preseason_goals FROM summer_form"
    ).fetchall():
        score = SUMMER_WC_WEIGHT * (wc or 0.0) + SUMMER_PRESEASON_WEIGHT * (preseason or 0.0)
        excess = min(SUMMER_MAX_BOOST, SUMMER_PER_POINT * max(0.0, score - SUMMER_THRESHOLD))
        if excess > 0.0:
            boost[int(code)] = 1.0 + excess * fade
    return boost


# FPL writes return dates two ways and only these two: "Suspended until 30 Aug",
# "Expected back 10 Oct". Everything else is "Unknown return date" (the majority)
# or a transfer note, both of which fall through to no date and keep the fade.
_RETURN_DATE = re.compile(r"(?:until|back)\s+(\d{1,2})\s+([A-Za-z]{3})", re.I)
_NEVER_GW = 999  # departed the league: out for every gameweek in any horizon


def availability_return_gw(con: duckdb.DuckDBPyConnection, season: str) -> dict[int, int]:
    """code -> first gameweek the player is expected available, read from FPL's
    own `news` string. Inference-only, like live_availability_factor.

    We already ingest `news` and show it to the human (preflight gates on it, the
    site badges it, the weekly sheet prints it) — but nothing ever fed it to the
    model, so the horizon guessed at return dates FPL had already published. This
    is that text, parsed.

    A player whose status is 'u' (left the club) maps to a sentinel beyond any
    horizon rather than a date: "gone" is not a long injury, and letting the fade
    restore them was handing EV to players who are no longer in the league.

    Players with no parseable date are OMITTED, not defaulted — the caller must
    keep the linear fade for them, since "no date published" genuinely means we
    do not know.
    """
    deadlines = con.execute(
        "SELECT event, deadline_time FROM gameweeks WHERE season = ? "
        "AND deadline_time IS NOT NULL ORDER BY event",
        [season],
    ).fetchall()
    if not deadlines:
        return {}
    # A season straddles the new year, and `news` gives a day and month with no
    # year ("6 Sep"). Resolve against the real gameweek calendar rather than
    # assuming: whichever candidate year lands inside the season wins.
    years = {d[1].year for d in deadlines}
    first_deadline = min(d[1] for d in deadlines).replace(tzinfo=UTC)

    out: dict[int, int] = {}
    for code, status, news in con.execute(
        "SELECT code, status, news FROM players"
    ).fetchall():
        if status == "u":
            out[int(code)] = _NEVER_GW
            continue
        match = _RETURN_DATE.search(news or "")
        if match is None:
            continue
        for year in sorted(years):
            try:
                back = datetime.strptime(
                    f"{match.group(1)} {match.group(2)} {year}", "%d %b %Y"
                ).replace(tzinfo=UTC)
            except ValueError:
                break  # not a real date ("back 31 Feb") — leave them to the fade
            # A candidate landing BEFORE the season started is the wrong year:
            # "back 9 Jan" against 2026 predates GW1, and "first gameweek after
            # it" would then be GW1 — reading a January return as "available
            # now", the precise error this whole function exists to remove.
            if back < first_deadline:
                continue
            future = [event for event, dl in deadlines if dl.replace(tzinfo=UTC) >= back]
            # A date past the final deadline means out for the rest of the season.
            out[int(code)] = future[0] if future else _NEVER_GW
            break
    return out


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

    # Applied here, once, after DGW fixtures are summed — so a double gameweek is
    # boosted once on the player's total rather than twice on each fixture. The
    # quantiles move with the mean to keep q10 <= ev <= q90 intact.
    multiplier = out["code"].map(summer_boost(con, season)).fillna(1.0)
    for column in ("ev_points", "q10_points", "q90_points"):
        out[column] = out[column] * multiplier

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
        total_ev = horizon.build_horizon_ev(
            models.minutes_model, models.dc_model, models.points_models,
            base_rows, fixtures, teams, decision_gw=gw, horizon=HORIZON, decay=DECAY,
            max_gw=max_gw, availability_factor=live_availability_factor(con),
            return_gw=availability_return_gw(con, season),
        )
        # This branch builds its EV independently of `projections`, so it needs
        # the boost applied here too. The fallback below does NOT: it scales the
        # projections frame, which generate_projections already boosted.
        return total_ev * total_ev.index.to_series().map(summer_boost(con, season)).fillna(1.0)
    decay_sum = sum(DECAY**h for h in range(HORIZON))
    return (projections.set_index("code")["ev_points"] * decay_sum).rename("total_ev")
