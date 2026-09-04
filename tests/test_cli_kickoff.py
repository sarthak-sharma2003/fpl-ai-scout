"""Regression guard for the typer-OptionInfo trap.

`kickoff`/`report` call other typer commands as plain functions. Any boolean
option not passed explicitly stays a truthy `OptionInfo` object, silently
flipping behaviour — most damagingly `refresh(raw=...)`, where the default
would make refresh snapshot raw payloads and return WITHOUT ingesting the live
season, so the whole chain runs on stale data. These tests pin the explicit
values at each internal call site.
"""

from __future__ import annotations

from unittest import mock

from fplscout import cli


def test_kickoff_refreshes_live_not_raw():
    with (
        mock.patch.object(cli, "refresh") as refresh,
        mock.patch.object(cli, "project"),
        mock.patch.object(cli, "optimize"),
        mock.patch.object(cli, "preflight"),
        mock.patch.object(cli, "publish") as publish,
        mock.patch.object(cli, "report"),
    ):
        cli.kickoff(settings_path="s.yaml", skip_refresh=False)

    refresh.assert_called_once_with(raw=False, settings_path="s.yaml")
    # publish's preflight gate must not be bypassed by a truthy OptionInfo force
    _, publish_kwargs = publish.call_args
    assert publish_kwargs.get("force") is False


def test_report_publishes_with_preflight_gate():
    with (
        mock.patch.object(cli, "project"),
        mock.patch.object(cli, "optimize"),
        mock.patch.object(cli, "publish") as publish,
        mock.patch.object(cli, "load_settings", return_value={"paths": {"duckdb": "x"}}),
        mock.patch.object(cli.db, "connect"),
        mock.patch("fplscout.pipeline.latest_reference_point", return_value=("2026-27", 1)),
        mock.patch("fplscout.report.weekly.render_weekly", return_value="sheet"),
        mock.patch.object(cli, "REPO_ROOT"),
    ):
        cli.report(settings_path="s.yaml", skip_pipeline=False)

    _, publish_kwargs = publish.call_args
    assert publish_kwargs.get("force") is False


def test_gameweek_deadlines_are_stored_as_utc_whatever_the_session_timezone():
    """`gameweeks.deadline_time` is a naive TIMESTAMP that everything downstream
    reads as UTC (preflight does a bare .replace(tzinfo=UTC)). DuckDB converts
    an aware datetime into the SESSION's timezone on insert, so before this was
    normalised the stored deadline was whatever zone the machine running
    `refresh` sat in — GW3's 17:30Z landed as 11:30 on a laptop in MDT."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    import duckdb

    from fplscout.cli import _sync_gameweeks

    con = duckdb.connect()
    con.execute("SET TimeZone='America/Denver'")
    con.execute(
        "CREATE TABLE IF NOT EXISTS gameweeks ("
        " season VARCHAR, event INTEGER, deadline_time TIMESTAMP,"
        " finished BOOLEAN, average_entry_score INTEGER,"
        " PRIMARY KEY (season, event))"
    )

    bootstrap = SimpleNamespace(
        events=[
            SimpleNamespace(
                id=3,
                deadline_time=datetime(2026, 9, 4, 17, 30, tzinfo=UTC),
                finished=False,
                average_entry_score=0,
            )
        ]
    )
    _sync_gameweeks(con, bootstrap, "2026-27")

    stored = con.execute("SELECT deadline_time FROM gameweeks WHERE event = 3").fetchone()[0]
    assert stored == datetime(2026, 9, 4, 17, 30), stored
    con.close()
