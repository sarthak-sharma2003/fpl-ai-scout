"""Validate every recorded API fixture against its pydantic model.

These are the canary for schema drift: if the FPL API changes shape at the 26/27
season reset, re-recording the fixtures from the live API and re-running this suite
is the fastest way to see exactly what broke.
"""

from __future__ import annotations

from fplscout.ingest.schemas import (
    BootstrapStatic,
    ElementSummary,
    Entry,
    EntryHistory,
    EntryPicks,
    EventLive,
    Fixture,
    Transfer,
)


def test_bootstrap_static_schema(load_fixture):
    data = load_fixture("bootstrap_static.json")
    parsed = BootstrapStatic.model_validate(data)
    assert len(parsed.teams) == 20
    assert len(parsed.elements) > 0
    assert parsed.elements[0].id == 1


def test_fixtures_schema(load_fixture):
    data = load_fixture("fixtures.json")
    parsed = [Fixture.model_validate(item) for item in data]
    assert len(parsed) == 20
    assert parsed[0].team_h_difficulty in range(1, 6)


def test_element_summary_schema(load_fixture):
    data = load_fixture("element_summary.json")
    parsed = ElementSummary.model_validate(data)
    assert len(parsed.history) > 0
    assert len(parsed.history_past) > 0


def test_entry_schema(load_fixture):
    data = load_fixture("entry.json")
    parsed = Entry.model_validate(data)
    assert parsed.id == 1


def test_entry_history_schema(load_fixture):
    data = load_fixture("entry_history.json")
    parsed = EntryHistory.model_validate(data)
    assert len(parsed.current) > 0
    assert parsed.current[0].event == 1


def test_entry_picks_schema(load_fixture):
    data = load_fixture("entry_picks.json")
    parsed = EntryPicks.model_validate(data)
    assert len(parsed.picks) == 15


def test_entry_transfers_schema(load_fixture):
    data = load_fixture("entry_transfers.json")
    parsed = [Transfer.model_validate(item) for item in data]
    assert len(parsed) == 5


def test_event_live_schema(load_fixture):
    data = load_fixture("event_live.json")
    parsed = EventLive.model_validate(data)
    assert len(parsed.elements) > 0


def test_strict_schema_rejects_unknown_field(load_fixture):
    """Simulates an API schema drift: an unexpected new field must fail loudly."""
    data = load_fixture("entry.json")
    data["brand_new_field_from_season_reset"] = "surprise"
    try:
        Entry.model_validate(data)
        raised = False
    except Exception:
        raised = True
    assert raised, "strict model should reject unknown fields so drift is caught immediately"
