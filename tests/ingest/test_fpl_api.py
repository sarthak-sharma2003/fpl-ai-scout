from __future__ import annotations

import httpx
import pytest
import respx

from fplscout.ingest.fpl_api import BASE_URL, FplApiClient, SchemaDriftError


@pytest.fixture
def client(tmp_path):
    c = FplApiClient(cache_dir=tmp_path / "raw", min_interval=0.0)
    yield c
    c.close()


@respx.mock
def test_bootstrap_static_parses_and_caches(client, load_fixture):
    route = respx.get(f"{BASE_URL}/bootstrap-static/").mock(
        return_value=httpx.Response(200, json=load_fixture("bootstrap_static.json"))
    )

    result = client.bootstrap_static()
    assert len(result.teams) == 20
    assert route.call_count == 1

    # second call within TTL must be served from cache, not hit the network again
    client.bootstrap_static()
    assert route.call_count == 1


@respx.mock
def test_force_refresh_bypasses_cache(client, load_fixture):
    route = respx.get(f"{BASE_URL}/bootstrap-static/").mock(
        return_value=httpx.Response(200, json=load_fixture("bootstrap_static.json"))
    )
    client.bootstrap_static()
    client.bootstrap_static(force_refresh=True)
    assert route.call_count == 2


@respx.mock
def test_fixtures_endpoint(client, load_fixture):
    respx.get(f"{BASE_URL}/fixtures/").mock(
        return_value=httpx.Response(200, json=load_fixture("fixtures.json"))
    )
    result = client.fixtures()
    assert len(result) == 20


@respx.mock
def test_element_summary_endpoint(client, load_fixture):
    respx.get(f"{BASE_URL}/element-summary/1/").mock(
        return_value=httpx.Response(200, json=load_fixture("element_summary.json"))
    )
    result = client.element_summary(1)
    assert len(result.history) > 0


@respx.mock
def test_entry_endpoints(client, load_fixture):
    respx.get(f"{BASE_URL}/entry/1/").mock(
        return_value=httpx.Response(200, json=load_fixture("entry.json"))
    )
    respx.get(f"{BASE_URL}/entry/1/history/").mock(
        return_value=httpx.Response(200, json=load_fixture("entry_history.json"))
    )
    respx.get(f"{BASE_URL}/entry/1/event/1/picks/").mock(
        return_value=httpx.Response(200, json=load_fixture("entry_picks.json"))
    )
    respx.get(f"{BASE_URL}/entry/1/transfers/").mock(
        return_value=httpx.Response(200, json=load_fixture("entry_transfers.json"))
    )

    assert client.entry(1).id == 1
    assert len(client.entry_history(1).current) > 0
    assert len(client.entry_picks(1, 1).picks) == 15
    assert len(client.entry_transfers(1)) == 5


@respx.mock
def test_schema_drift_raises_loudly(client):
    respx.get(f"{BASE_URL}/bootstrap-static/").mock(
        return_value=httpx.Response(200, json={"totally": "different shape"})
    )
    with pytest.raises(SchemaDriftError):
        client.bootstrap_static()


@respx.mock
def test_retries_on_transport_error_then_succeeds(client, load_fixture):
    route = respx.get(f"{BASE_URL}/fixtures/").mock(
        side_effect=[
            httpx.ConnectError("boom"),
            httpx.Response(200, json=load_fixture("fixtures.json")),
        ]
    )
    result = client.fixtures()
    assert len(result) == 20
    assert route.call_count == 2


@respx.mock
def test_http_error_status_raises(client):
    respx.get(f"{BASE_URL}/bootstrap-static/").mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        client.bootstrap_static()
