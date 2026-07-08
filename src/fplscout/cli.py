"""fplscout CLI entrypoints: refresh | train | project | optimize | report | serve.

Only `refresh` is implemented in Phase 0. The rest are stubs that name the phase
that implements them, so `--help` always reflects the true state of the build.
"""

from __future__ import annotations

from pathlib import Path

import typer
import yaml

from fplscout.ingest.fpl_api import FplApiClient

app = typer.Typer(no_args_is_help=True, add_completion=False)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_PATH = REPO_ROOT / "config" / "settings.yaml"


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> dict:
    return yaml.safe_load(path.read_text())


@app.command()
def refresh(
    raw: bool = typer.Option(
        False, "--raw", help="Snapshot raw API payloads to data/raw/ without touching DuckDB."
    ),
    settings_path: Path = typer.Option(DEFAULT_SETTINGS_PATH, "--settings"),
) -> None:
    """Pull fresh data from the FPL API.

    --raw hits bootstrap-static, fixtures, a sample element-summary, and (if
    team_id is configured) our entry endpoints, validating each against
    fplscout.ingest.schemas and caching the result under data/raw/. Full
    per-player element-summary ingestion into DuckDB is Phase 1.
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
        fixtures = client.fixtures(force_refresh=True)
        typer.echo(f"  OK — {len(fixtures)} fixtures")

        if not raw:
            typer.echo("Refresh complete (schema validation only; pass --raw to snapshot).")
            return

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


@app.command()
def train() -> None:
    """Train prediction models. Implemented in Phase 3."""
    typer.echo("Not implemented yet — Phase 3 (models).")
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
