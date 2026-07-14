"""fplscout CLI entrypoints: refresh | train | backtest | project | optimize | publish | report.

Only `refresh` is implemented (Phase 0: live-API schema check + raw snapshot; Phase 1:
DuckDB population from vaastav historical data). The rest are stubs that name the
phase that implements them, so `--help` always reflects the true state of the build.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import typer
import yaml

from fplscout import db
from fplscout.features.build import write_features
from fplscout.ingest import live_gw, vaastav
from fplscout.ingest.fpl_api import FplApiClient
from fplscout.ingest.health import archive_ep_next, check_ep_next_health
from fplscout.ingest.vaastav import SEASONS as VAASTAV_SEASONS

app = typer.Typer(no_args_is_help=True, add_completion=False)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_PATH = REPO_ROOT / "config" / "settings.yaml"


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> dict:
    return yaml.safe_load(path.read_text())


def _sync_gameweeks(con, bootstrap, season: str) -> int:
    """Writes bootstrap.events (deadline_time, finished, average_entry_score) to
    the `gameweeks` table for `season`. Real gap found while building publish.py:
    the schema has always had this table but nothing ever wrote to it, so
    dashboard.json's avg_points/deadline were silently always null. vaastav's
    historical scrape has no equivalent columns, so this only ever populates the
    season the live API currently represents — 2025-26 right now, pre-26/27-
    launch, and 2026-27 once that season starts (see plan §9 kickoff checklist
    for how `season` should be derived once vaastav adds a 2026-27 folder)."""
    rows = [
        (season, e.id, e.deadline_time, e.finished, e.average_entry_score)
        for e in bootstrap.events
    ]
    con.executemany(
        "INSERT INTO gameweeks (season, event, deadline_time, finished, average_entry_score) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (season, event) DO UPDATE SET "
        "deadline_time = excluded.deadline_time, finished = excluded.finished, "
        "average_entry_score = excluded.average_entry_score",
        rows,
    )
    return len(rows)


def _sync_player_status(con, bootstrap) -> int:
    """Persists bootstrap-static's live status/news/chance_of_playing_next_round
    to `players` — pipeline.py's inference-time availability overlay (issue #1)
    reads this. Live-only snapshot, never a training feature (see db.py).

    May insert a code vaastav hasn't scraped yet (e.g. a new signing right
    after the 26/27 launch) with NULL name/last_seen_season; vaastav's
    name-backfill upsert explicitly accepts the NULL case (see the IS NULL
    branch of its ON CONFLICT guard in ingest/vaastav.py), so ordering
    relative to the historical load is a nicety, not a correctness rule."""
    status_df = pd.DataFrame(
        [
            (e.code, e.status, e.news, e.chance_of_playing_next_round, e.penalties_order)
            for e in bootstrap.elements
        ],
        columns=["code", "status", "news", "chance_of_playing_next_round", "penalties_order"],
    )
    con.execute(
        "INSERT INTO players AS p "
        "(code, status, news, chance_of_playing_next_round, penalties_order) "
        "SELECT * FROM status_df "
        "ON CONFLICT (code) DO UPDATE SET status = excluded.status, "
        "news = excluded.news, "
        "chance_of_playing_next_round = excluded.chance_of_playing_next_round, "
        "penalties_order = excluded.penalties_order"
    )
    return len(status_df)


@app.command()
def refresh(
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Snapshot raw API payloads to data/raw/ only — skip the DuckDB historical load.",
    ),
    settings_path: Path = typer.Option(DEFAULT_SETTINGS_PATH, "--settings"),
) -> None:
    """Refresh data.

    Always does a live-API schema check (bootstrap-static + fixtures) — our
    earliest warning of the 26/27 season-reset schema drift — and archives the
    current ep_next snapshot to ep_next_archive (effective immediately, not
    waiting for 26/27: values we fetch pre-deadline don't have vaastav's
    post-match-contamination problem, since we control when we fetch them — see
    ingest/health.py). Then either:

    --raw: additionally snapshot a sample element-summary and (if team_id is
    configured) our entry endpoints to data/raw/ — no further DuckDB writes.

    (default): populate DuckDB from vaastav historical data (2021-22 .. 2025-26);
    additionally, if the live API's current season isn't one vaastav covers yet
    (26/27+), ingest that season's finished gameweeks directly from the FPL API
    (ingest/live_gw.py) — no vaastav dependency once that season starts. Then
    rebuild the feature store. Idempotent — safe to re-run.
    """
    settings = load_settings(settings_path)
    raw_cache_dir = REPO_ROOT / settings["paths"]["raw_cache"]
    min_interval = settings["api"]["min_interval_seconds"]
    timeout = settings["api"]["timeout_seconds"]

    duckdb_path = REPO_ROOT / settings["paths"]["duckdb"]
    con = db.connect(duckdb_path)
    db.init_schema(con)

    with FplApiClient(
        cache_dir=raw_cache_dir, min_interval=min_interval, timeout=timeout
    ) as client:
        typer.echo("Fetching bootstrap-static...")
        bootstrap = client.bootstrap_static(force_refresh=True)
        typer.echo(
            f"  OK — {len(bootstrap.elements)} players, {len(bootstrap.teams)} teams, "
            f"{len(bootstrap.events)} gameweeks"
        )

        typer.echo("Fetching fixtures...")
        fixtures = client.fixtures(force_refresh=True)
        typer.echo(f"  OK — {len(fixtures)} fixtures")

        ep_next_warnings = check_ep_next_health(bootstrap)
        if ep_next_warnings:
            typer.echo("  WARNING: ep_next looks degenerate:")
            for warning in ep_next_warnings:
                typer.echo(f"    - {warning}")
        else:
            typer.echo("  ep_next distribution looks healthy.")

        n_archived = archive_ep_next(con, bootstrap)
        typer.echo(f"  archived {n_archived} ep_next values to ep_next_archive")

        current_season = live_gw.derive_current_season(bootstrap)
        n_gws = _sync_gameweeks(con, bootstrap, current_season)
        typer.echo(f"  synced {n_gws} gameweeks for {current_season}")

        if raw:
            sample_player_id = bootstrap.elements[0].id
            typer.echo(f"Fetching element-summary for sample player {sample_player_id}...")
            summary = client.element_summary(sample_player_id, force_refresh=True)
            typer.echo(f"  OK — {len(summary.history)} history rows")

            team_id = settings.get("team_id")
            if team_id:
                typer.echo(f"Fetching entry data for team {team_id}...")
                client.entry(team_id, force_refresh=True)
                client.entry_history(team_id, force_refresh=True)
                client.entry_transfers(team_id, force_refresh=True)
                typer.echo("  OK")
            else:
                typer.echo("team_id not set in config/settings.yaml — skipping entry endpoints.")

            con.close()
            typer.echo(f"Raw snapshots written to {raw_cache_dir}")
            return

        if current_season not in VAASTAV_SEASONS:
            # 26/27+ only — vaastav has no folder for it yet (issue #2). ~15 min
            # the first time (one element-summary call per player at the
            # client's <=1 req/s throttle); near-instant on reruns within the
            # element-summary cache TTL.
            typer.echo(f"Ingesting live per-GW data for {current_season} (no vaastav coverage)...")
            live_summary = live_gw.sync_current_season(
                con, client, bootstrap, fixtures, current_season
            )
            typer.echo(
                f"  {live_summary['season']}: {live_summary['teams']} teams, "
                f"{live_summary['fixtures']} fixtures, {live_summary['players']} players, "
                f"{live_summary['gw_rows']} player-gw rows"
            )

    typer.echo("Loading historical data (vaastav/Fantasy-Premier-League)...")
    summaries = vaastav.load_all_seasons(con, cache_dir=raw_cache_dir / "vaastav")
    for s in summaries:
        typer.echo(
            f"  {s['season']}: {s['teams']} teams, {s['players']} players, "
            f"{s['fixtures']} fixtures, {s['gw_rows']} player-gw rows"
        )

    n_status = _sync_player_status(con, bootstrap)
    typer.echo(f"  synced live status for {n_status} players")

    typer.echo("Building feature store...")
    n_features = write_features(con)
    typer.echo(f"  {n_features} feature rows")

    con.close()
    typer.echo(f"DuckDB updated at {duckdb_path}")


@app.command()
def train(settings_path: Path = typer.Option(DEFAULT_SETTINGS_PATH, "--settings")) -> None:
    """Train minutes/team-goals/points models; write validation report.

    No `fpl_xp` feature (vaastav's `xP` is confirmed post-match-contaminated —
    see models/points.py docstring). Trains two backtest splits: primary (train
    2021-22..2023-24, holdout 2024-25) and secondary (train 2021-22..2024-25,
    holdout 2025-26) — the plan's own requirement to validate on two seasons.
    Writes model pickles to data/models/ and a markdown validation report to
    data/reports/.
    """
    import json

    from fplscout.models.train import render_report, run, to_summary_dict

    settings = load_settings(settings_path)
    duckdb_path = REPO_ROOT / settings["paths"]["duckdb"]
    con = db.connect(duckdb_path)

    typer.echo("Training minutes / team-goals / points models...")
    result = run(con, models_dir=REPO_ROOT / "data" / "models")
    con.close()

    report = render_report(result)
    reports_dir = REPO_ROOT / "data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"phase3_validation_{result['primary']['version']}.md"
    report_path.write_text(report)
    (reports_dir / "phase3_validation_latest.json").write_text(
        json.dumps(to_summary_dict(result), indent=2)
    )

    typer.echo(report)
    typer.echo(f"Report written to {report_path}")
    if not result["beats_naive_decision"]:
        typer.echo(
            "\nDoD NOT met: model must clearly beat the naive baseline on the "
            "primary split's decision-relevant mean per-GW Spearman. STOP and "
            "iterate before building downstream phases."
        )
        raise typer.Exit(code=1)


@app.command()
def backtest(settings_path: Path = typer.Option(DEFAULT_SETTINGS_PATH, "--settings")) -> None:
    """Full-season replay — plan §Phase6 go/no-go gate.

    Runs both required samples (2024-25 primary, 2025-26 secondary), each
    training fresh on seasons strictly before the replayed one. Writes a
    markdown report to data/reports/. Does not gate on the plan's stated 2400
    figure — that bar assumed a legitimate ep_next feature that historical
    data doesn't have (see models/points.py); judge the totals against the
    real benchmarks the report prints instead.
    """
    import json

    from fplscout.backtest.report import render_report, to_summary_dict
    from fplscout.backtest.simulator import simulate_season

    settings = load_settings(settings_path)
    duckdb_path = REPO_ROOT / settings["paths"]["duckdb"]
    con = db.connect(duckdb_path)

    typer.echo("Replaying 2024-25 (primary)...")
    primary = simulate_season(
        con, season="2024-25", train_seasons=["2021-22", "2022-23", "2023-24"]
    )
    typer.echo(f"  {primary.total_points:.0f} pts")

    typer.echo("Replaying 2025-26 (secondary)...")
    secondary = simulate_season(
        con,
        season="2025-26",
        train_seasons=["2021-22", "2022-23", "2023-24", "2024-25"],
    )
    typer.echo(f"  {secondary.total_points:.0f} pts")
    con.close()

    report_text = render_report([primary, secondary])
    reports_dir = REPO_ROOT / "data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    from datetime import UTC, datetime

    version = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = reports_dir / f"phase6_backtest_{version}.md"
    report_path.write_text(report_text)
    (reports_dir / "phase6_backtest_latest.json").write_text(
        json.dumps(to_summary_dict([primary, secondary]), indent=2)
    )
    typer.echo(f"Report written to {report_path}")


@app.command()
def project(settings_path: Path = typer.Option(DEFAULT_SETTINGS_PATH, "--settings")) -> None:
    """Train production models (all seasons, no holdout) and write per-player
    projections for the latest available reference gameweek to the
    `projections` table.

    Pre-26/27-launch: there is no live "next gameweek" yet, so this projects
    "as of" the most recently completed gameweek in the data (2025-26 GW38) —
    see pipeline.py's module docstring. Once 26/27 starts, the reference point
    becomes the real live next gameweek automatically.
    """
    from fplscout import pipeline

    settings = load_settings(settings_path)
    duckdb_path = REPO_ROOT / settings["paths"]["duckdb"]
    con = db.connect(duckdb_path)

    season, gw = pipeline.latest_reference_point(con)
    typer.echo(f"Reference point: season={season}, gw={gw}")

    typer.echo("Training production models on all available seasons...")
    models = pipeline.train_production(con, models_dir=REPO_ROOT / "data" / "models")
    typer.echo(f"  model_version={models.version}, trained on {models.train_seasons}")

    typer.echo(f"Generating projections for {season} GW{gw}...")
    out = pipeline.generate_projections(con, models, season, gw)
    con.close()
    typer.echo(f"  wrote {len(out)} projection rows (model_version={models.version})")


@app.command()
def optimize(settings_path: Path = typer.Option(DEFAULT_SETTINGS_PATH, "--settings")) -> None:
    """Run the MILP optimizer and write a recommendation to `recommendations`.

    No real squad exists yet (team not registered until 26/27 launches — plan
    §9/§11), so this runs in wildcard mode: an unconstrained "best possible
    15" build rather than a transfer decision off a prior squad. Requires
    `project` to have been run first (reads the latest `projections` row per
    the same reference gameweek it computed).
    """
    import json
    from datetime import UTC, datetime

    from fplscout import pipeline
    from fplscout.decide.optimizer import CAPTAIN_Q90_WEIGHT, OptimizerInput
    from fplscout.decide.optimizer import optimize as run_optimizer

    settings = load_settings(settings_path)
    duckdb_path = REPO_ROOT / settings["paths"]["duckdb"]
    con = db.connect(duckdb_path)

    season, gw = pipeline.latest_reference_point(con)
    model_version = con.execute(
        "SELECT model_version FROM projections WHERE season = ? AND gw = ? "
        "ORDER BY generated_at DESC LIMIT 1",
        [season, gw],
    ).fetchone()
    if model_version is None:
        typer.echo("No projections found — run `fplscout project` first.")
        raise typer.Exit(code=1)
    model_version = model_version[0]

    proj = con.execute(
        "SELECT code, ev_points, q90_points FROM projections "
        "WHERE season = ? AND gw = ? AND model_version = ?",
        [season, gw, model_version],
    ).df()
    # captain score: current-GW mean/q90 blend (issue #4), same rule as the backtest
    proj["cap_ev"] = (
        (1 - CAPTAIN_Q90_WEIGHT) * proj["ev_points"]
        + CAPTAIN_Q90_WEIGHT * proj["q90_points"]
    )
    roster = pipeline.roster_snapshot(con, season, gw)
    models = pipeline.load_production_models(REPO_ROOT / "data" / "models", model_version)
    total_ev = pipeline.total_ev_for_optimizer(con, models, season, gw, proj)
    con.close()

    total_ev_df = total_ev.rename("total_ev").reset_index().rename(columns={"index": "code"})
    opt_input_df = roster.merge(total_ev_df, on="code", how="inner")
    opt_input_df = opt_input_df.merge(proj[["code", "cap_ev"]], on="code", how="left")
    opt_input_df["cap_ev"] = opt_input_df["cap_ev"].fillna(0.0)
    opt_input_df = opt_input_df.dropna(subset=["total_ev", "price", "position", "team_id"])

    typer.echo(f"Optimizing over {len(opt_input_df)} players (wildcard mode)...")
    result = run_optimizer(
        OptimizerInput(
            projections=opt_input_df[
                ["code", "position", "team_id", "price", "total_ev", "cap_ev"]
            ],
            current_squad=set(),
            purchase_prices={},
            bank=1000,
            free_transfers=1,
            chip_mode="wildcard",
        )
    )
    if result.status != "Optimal":
        typer.echo(f"Optimizer did not find an optimal solution: {result.status}")
        raise typer.Exit(code=1)

    con = db.connect(duckdb_path)
    con.execute(
        "INSERT INTO recommendations (season, gw, generated_at, squad, starting_xi, "
        "captain_code, vice_captain_code, transfers, hits, chip, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            season, gw, datetime.now(UTC),
            json.dumps(sorted(result.squad)),
            json.dumps(sorted(result.starting_xi)),
            result.captain,
            result.vice_captain,
            json.dumps([]),
            result.hits,
            "wildcard",
            None,
        ],
    )
    con.close()
    typer.echo(
        f"  squad={len(result.squad)}, captain={result.captain}, "
        f"objective={result.objective_value:.1f} — written to recommendations"
    )


@app.command()
def publish(settings_path: Path = typer.Option(DEFAULT_SETTINGS_PATH, "--settings")) -> None:
    """Render every §8 API-contract response to static JSON under
    site/public/data/ (served at /data/*.json by the Vite app, dev and build
    alike) — the fully-static GitHub Pages deploy (no backend server in
    production; see README's static-site pivot). Requires `project` and
    `optimize` to have run first.
    """
    from fplscout.publish import publish_all

    settings = load_settings(settings_path)
    duckdb_path = REPO_ROOT / settings["paths"]["duckdb"]
    con = db.connect(duckdb_path)

    written = publish_all(
        con,
        # site/public/ is Vite's static-asset convention: everything under it is
        # served as-is at the root path, in both `vite dev` and the production
        # build — so public/data/dashboard.json is reachable at /data/dashboard.json
        # either way, no separate copy step needed.
        site_data_dir=REPO_ROOT / "site" / "public" / "data",
        reports_dir=REPO_ROOT / "data" / "reports",
        rules_path=REPO_ROOT / "config" / "rules.yaml",
    )
    con.close()

    for name, size in written.items():
        typer.echo(f"  {name}: {size}")
    typer.echo(f"Published to {REPO_ROOT / 'site' / 'public' / 'data'}")


@app.command()
def report(
    settings_path: Path = typer.Option(DEFAULT_SETTINGS_PATH, "--settings"),
    skip_pipeline: bool = typer.Option(
        False,
        "--skip-pipeline",
        help="Render from existing projections/recommendations instead of "
        "re-running project -> optimize -> publish first.",
    ),
) -> None:
    """The weekly one-command op (plan §8): project -> optimize -> publish,
    then render the 'DO THIS' markdown sheet to data/reports/ and stdout.
    Run `fplscout refresh` first on a real gameweek (it's the network step)."""
    from fplscout import pipeline
    from fplscout.report.weekly import render_weekly

    if not skip_pipeline:
        project(settings_path=settings_path)
        optimize(settings_path=settings_path)
        publish(settings_path=settings_path)

    settings = load_settings(settings_path)
    con = db.connect(REPO_ROOT / settings["paths"]["duckdb"])
    season, gw = pipeline.latest_reference_point(con)
    sheet = render_weekly(con, season, gw)
    con.close()

    reports_dir = REPO_ROOT / "data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / f"weekly_{season}_gw{gw}.md"
    out_path.write_text(sheet)
    typer.echo(sheet)
    typer.echo(f"Written to {out_path}")


if __name__ == "__main__":
    app()
