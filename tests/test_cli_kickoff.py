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
