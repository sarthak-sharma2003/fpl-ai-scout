"""fplscout CLI entrypoints: refresh | train | project | optimize | report | serve.

Only `refresh` is implemented (Phase 0: live-API schema check + raw snapshot; Phase 1:
DuckDB population from vaastav historical data). The rest are stubs that name the
phase that implements them, so `--help` always reflects the true state of the build.
"""

from __future__ import annotations

from pathlib import Path

import typer
import yaml

from fplscout import db
from fplscout.features.build import write_features
from fplscout.ingest import vaastav
from fplscout.ingest.fpl_api import FplApiClient

app = typer.Typer(no_args_is_help=True, add_completion=False)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_PATH = REPO_ROOT / "config" / "settings.yaml"


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> dict:
    return yaml.safe_load(path.read_text())


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

    Always does a live-API schema check (bootstrap-static + fixtures), which is
    also our earliest warning of the 26/27 season-reset schema drift. Then either:

    --raw: additionally snapshot a sample element-summary and (if team_id is
    configured) our entry endpoints to data/raw/ — no DuckDB writes.

    (default): populate DuckDB from vaastav historical data (2021-22 .. 2025-26)
    and rebuild the feature store. Idempotent — safe to re-run.
    """
    settings = load_settings(settings_path)
    raw_cache_dir = REPO_ROOT / settings["paths"]["raw_cache"]
    min_interval = settings["api"]["min_interval_seconds"]
    timeout = settings["api"]["timeout_seconds"]

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
        client.fixtures(force_refresh=True)
        typer.echo(f"  OK — {len(client.fixtures())} fixtures")

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

            typer.echo(f"Raw snapshots written to {raw_cache_dir}")
            return

    typer.echo("Loading historical data (vaastav/Fantasy-Premier-League)...")
    duckdb_path = REPO_ROOT / settings["paths"]["duckdb"]
    con = db.connect(duckdb_path)
    db.init_schema(con)
    summaries = vaastav.load_all_seasons(con, cache_dir=raw_cache_dir / "vaastav")
    for s in summaries:
        typer.echo(
            f"  {s['season']}: {s['teams']} teams, {s['players']} players, "
            f"{s['fixtures']} fixtures, {s['gw_rows']} player-gw rows"
        )

    typer.echo("Building feature store...")
    n_features = write_features(con)
    typer.echo(f"  {n_features} feature rows")

    con.close()
    typer.echo(f"DuckDB updated at {duckdb_path}")


@app.command()
def train(settings_path: Path = typer.Option(DEFAULT_SETTINGS_PATH, "--settings")) -> None:
    """Train minutes/team-goals/points models; write validation report.

    Trains on 2021-22..2024-25, holds out 2025-26. Writes model pickles to
    data/models/ and a markdown validation report to data/reports/.
    """
    from fplscout.models.train import render_report, run

    settings = load_settings(settings_path)
    duckdb_path = REPO_ROOT / settings["paths"]["duckdb"]
    con = db.connect(duckdb_path)

    typer.echo("Training minutes / team-goals / points models...")
    result = run(con, models_dir=REPO_ROOT / "data" / "models")
    con.close()

    report = render_report(result)
    reports_dir = REPO_ROOT / "data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"phase3_validation_{result['version']}.md"
    report_path.write_text(report)

    typer.echo(report)
    typer.echo(f"Report written to {report_path}")
    if not result["beats_naive_decision"]:
        typer.echo(
            "\nDoD NOT met: model must beat the naive baseline on decision-relevant "
            "mean per-GW Spearman (plausible starters only). STOP and iterate "
            "before building downstream phases."
        )
        raise typer.Exit(code=1)


@app.command()
def project() -> None:
    """Generate per-player, per-GW point projections. Implemented in Phase 3."""
    typer.echo("Not implemented yet — Phase 3 (models).")
    raise typer.Exit(code=1)


@app.command()
def optimize() -> None:
    """Run the MILP optimizer over the current squad. Implemented in Phase 4."""
    typer.echo("Not implemented yet — Phase 4 (optimizer).")
    raise typer.Exit(code=1)


@app.command()
def report() -> None:
    """Generate the weekly 'DO THIS' report. Implemented in Phase 8."""
    typer.echo("Not implemented yet — Phase 8 (automation).")
    raise typer.Exit(code=1)


@app.command()
def serve() -> None:
    """Run the FastAPI backend. Implemented in Phase 7."""
    typer.echo("Not implemented yet — Phase 7 (API).")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
