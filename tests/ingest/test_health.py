from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fplscout import db
from fplscout.ingest.health import (
    archive_ep_next,
    check_ep_next_health,
    sync_ep_next_archive_csv,
)
from fplscout.ingest.schemas import BootstrapStatic


def _bootstrap(load_fixture, ep_next_overrides: dict[int, str | None]) -> BootstrapStatic:
    data = load_fixture("bootstrap_static.json")
    for element in data["elements"]:
        if element["id"] in ep_next_overrides:
            element["ep_next"] = ep_next_overrides[element["id"]]
    return BootstrapStatic.model_validate(data)


def test_healthy_ep_next_has_no_warnings(load_fixture):
    data = load_fixture("bootstrap_static.json")
    # give every sampled element a healthy-looking value
    for element in data["elements"]:
        element["ep_next"] = "1.0"
    bootstrap = BootstrapStatic.model_validate(data)
    assert check_ep_next_health(bootstrap) == []


def test_degenerate_mean_triggers_warning(load_fixture):
    data = load_fixture("bootstrap_static.json")
    for element in data["elements"]:
        element["ep_next"] = "0.0"
    bootstrap = BootstrapStatic.model_validate(data)
    warnings = check_ep_next_health(bootstrap)
    assert len(warnings) > 0
    assert any("degenerate" in w for w in warnings)


def test_high_null_fraction_triggers_warning(load_fixture):
    data = load_fixture("bootstrap_static.json")
    for element in data["elements"]:
        element["ep_next"] = None
    bootstrap = BootstrapStatic.model_validate(data)
    warnings = check_ep_next_health(bootstrap)
    assert any("null ep_next" in w for w in warnings)


@pytest.fixture
def con():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


def test_archive_ep_next_writes_one_row_per_parseable_value(load_fixture, con):
    data = load_fixture("bootstrap_static.json")
    for element in data["elements"]:
        element["ep_next"] = "1.5"
    bootstrap = BootstrapStatic.model_validate(data)

    n = archive_ep_next(con, bootstrap)
    assert n == len(bootstrap.elements)
    count = con.execute("SELECT COUNT(*) FROM ep_next_archive").fetchone()[0]
    assert count == n


def test_archive_ep_next_skips_null_values(load_fixture, con):
    data = load_fixture("bootstrap_static.json")
    data["elements"][0]["ep_next"] = None
    for element in data["elements"][1:]:
        element["ep_next"] = "1.5"
    bootstrap = BootstrapStatic.model_validate(data)

    n = archive_ep_next(con, bootstrap)
    assert n == len(bootstrap.elements) - 1


def test_archive_ep_next_records_the_next_gameweek(load_fixture, con):
    data = load_fixture("bootstrap_static.json")
    for element in data["elements"]:
        element["ep_next"] = "1.5"
    for event in data["events"]:
        event["is_next"] = event["id"] == 5
    bootstrap = BootstrapStatic.model_validate(data)

    archive_ep_next(con, bootstrap)
    gws = con.execute("SELECT DISTINCT gw FROM ep_next_archive").fetchall()
    assert gws == [(5,)]


def test_archive_ep_next_returns_zero_when_nothing_to_archive(load_fixture, con):
    data = load_fixture("bootstrap_static.json")
    for element in data["elements"]:
        element["ep_next"] = None
    bootstrap = BootstrapStatic.model_validate(data)
    assert archive_ep_next(con, bootstrap) == 0


def _seed_archive(con, snapshot_hour: int, codes: range) -> None:
    con.executemany(
        "INSERT INTO ep_next_archive (snapshot_time, code, element_id, gw, ep_next) "
        "VALUES (?, ?, ?, ?, ?)",
        [(datetime(2026, 8, 1, snapshot_hour, tzinfo=UTC), c, c, 1, 2.5) for c in codes],
    )


def test_sync_csv_roundtrip_and_merge(tmp_path, con):
    csv_path = tmp_path / "archive.csv"
    _seed_archive(con, snapshot_hour=6, codes=range(1, 4))
    assert sync_ep_next_archive_csv(con, csv_path) == 3

    # a "different runner": empty DB imports the CSV, adds its own snapshot
    other = db.connect(":memory:")
    db.init_schema(other)
    _seed_archive(other, snapshot_hour=7, codes=range(1, 4))
    assert sync_ep_next_archive_csv(other, csv_path) == 6
    other.close()

    # original DB now picks up the other runner's rows from the same CSV
    assert sync_ep_next_archive_csv(con, csv_path) == 6
    # and values survived the roundtrip intact
    val = con.execute(
        "SELECT ep_next FROM ep_next_archive WHERE code = 2 LIMIT 1"
    ).fetchone()[0]
    assert val == 2.5


def test_sync_csv_is_idempotent(tmp_path, con):
    csv_path = tmp_path / "archive.csv"
    _seed_archive(con, snapshot_hour=6, codes=range(1, 4))
    assert sync_ep_next_archive_csv(con, csv_path) == 3
    assert sync_ep_next_archive_csv(con, csv_path) == 3
    assert len(csv_path.read_text().strip().splitlines()) == 4  # header + 3 rows
