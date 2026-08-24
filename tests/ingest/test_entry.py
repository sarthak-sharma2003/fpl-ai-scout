from __future__ import annotations

import httpx
import pytest
import respx

from fplscout import db
from fplscout.ingest.entry import sync_entry
from fplscout.ingest.fpl_api import BASE_URL, FplApiClient

ENTRY_ID = 5874404


@pytest.fixture
def con():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def client(tmp_path):
    c = FplApiClient(cache_dir=tmp_path / "raw", min_interval=0.0)
    yield c
    c.close()


@respx.mock
def test_sync_writes_entry_and_picks(con, client, load_fixture):
    entry_payload = load_fixture("entry.json")
    entry_payload["id"] = ENTRY_ID
    entry_payload["current_event"] = 1
    respx.get(f"{BASE_URL}/entry/{ENTRY_ID}/").mock(
        return_value=httpx.Response(200, json=entry_payload)
    )
    respx.get(f"{BASE_URL}/entry/{ENTRY_ID}/event/1/picks/").mock(
        return_value=httpx.Response(200, json=load_fixture("entry_picks.json"))
    )

    picks_elements = [p["element"] for p in load_fixture("entry_picks.json")["picks"]]
    element_to_code = {el: el + 100_000 for el in picks_elements}  # arbitrary, deterministic

    summary = sync_entry(con, client, ENTRY_ID, element_to_code=element_to_code)
    assert summary == {"gw": 1, "picks": 15}

    row = con.execute(
        "SELECT entry_id, name, bank, team_value, last_synced_gw, event_transfers_cost "
        "FROM our_entry"
    ).fetchone()
    assert row == (ENTRY_ID, entry_payload["name"], 10, 1000, 1, 0)

    first_element = picks_elements[0]
    codes = con.execute(
        "SELECT code FROM our_picks WHERE gw = 1 AND position = 1"
    ).fetchone()
    assert codes == (element_to_code[first_element],)


@respx.mock
def test_sync_returns_empty_when_picks_not_public_yet(con, client, load_fixture):
    entry_payload = load_fixture("entry.json")
    entry_payload["id"] = ENTRY_ID
    entry_payload["current_event"] = 1
    respx.get(f"{BASE_URL}/entry/{ENTRY_ID}/").mock(
        return_value=httpx.Response(200, json=entry_payload)
    )
    respx.get(f"{BASE_URL}/entry/{ENTRY_ID}/event/1/picks/").mock(
        return_value=httpx.Response(404)
    )

    summary = sync_entry(con, client, ENTRY_ID, element_to_code={})
    assert summary == {}
    assert con.execute("SELECT COUNT(*) FROM our_entry").fetchone()[0] == 0
