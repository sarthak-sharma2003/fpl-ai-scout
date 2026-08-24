from __future__ import annotations

import httpx
import pytest
import respx

from fplscout import db
from fplscout.ingest.entry import _derive_free_transfers, sync_entry
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


def _mock_entry(load_fixture, current_event=1, history=None):
    entry_payload = load_fixture("entry.json")
    entry_payload["id"] = ENTRY_ID
    entry_payload["current_event"] = current_event
    respx.get(f"{BASE_URL}/entry/{ENTRY_ID}/").mock(
        return_value=httpx.Response(200, json=entry_payload)
    )
    if history is None:
        history = load_fixture("entry_history.json")
        history["current"] = history["current"][:1]
        history["chips"] = []
    respx.get(f"{BASE_URL}/entry/{ENTRY_ID}/history/").mock(
        return_value=httpx.Response(200, json=history)
    )
    respx.get(f"{BASE_URL}/entry/{ENTRY_ID}/transfers/").mock(
        return_value=httpx.Response(200, json=[])
    )
    return entry_payload


@respx.mock
def test_sync_writes_entry_and_picks(con, client, load_fixture):
    entry_payload = _mock_entry(load_fixture)
    respx.get(f"{BASE_URL}/entry/{ENTRY_ID}/event/1/picks/").mock(
        return_value=httpx.Response(200, json=load_fixture("entry_picks.json"))
    )

    picks_elements = [p["element"] for p in load_fixture("entry_picks.json")["picks"]]
    element_to_code = {el: el + 100_000 for el in picks_elements}  # arbitrary, deterministic

    summary = sync_entry(con, client, ENTRY_ID, element_to_code=element_to_code)
    assert summary == {"gw": 1, "picks": 15, "free_transfers": 1}

    row = con.execute(
        "SELECT entry_id, name, bank, team_value, free_transfers, last_synced_gw, "
        "event_transfers_cost FROM our_entry"
    ).fetchone()
    assert row == (ENTRY_ID, entry_payload["name"], 10, 1000, 1, 1, 0)

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


class _Row:
    def __init__(self, event, event_transfers):
        self.event, self.event_transfers = event, event_transfers


class _Chip:
    def __init__(self, event, name):
        self.event, self.name = event, name


class _History:
    def __init__(self, current, chips=()):
        self.current, self.chips = current, list(chips)


@pytest.mark.parametrize(
    "current, chips, expected",
    [
        # after the initial draft you always hold exactly 1
        ([_Row(1, 0)], [], 1),
        # a quiet week banks one: 1 -> 2
        ([_Row(1, 0), _Row(2, 0)], [], 2),
        # spending the FT keeps you at 1
        ([_Row(1, 0), _Row(2, 1)], [], 1),
        # banking caps at 5, never 6
        ([_Row(i, 0) for i in range(1, 9)], [], 5),
        # a -4 hit (2 transfers on 1 FT) floors at 0 spent, then banks 1
        ([_Row(1, 0), _Row(2, 2)], [], 1),
        # wildcard week spends no FT and still banks one
        ([_Row(1, 0), _Row(2, 0), _Row(3, 12)], [_Chip(3, "wildcard")], 3),
    ],
)
def test_derive_free_transfers(current, chips, expected):
    assert _derive_free_transfers(_History(current, chips)) == expected
