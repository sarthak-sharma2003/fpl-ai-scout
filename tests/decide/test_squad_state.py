from __future__ import annotations

import pytest

from fplscout import db
from fplscout.decide.squad_state import SquadState, load_state, reconcile, save_state


@pytest.fixture
def con():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


def test_save_and_load_round_trip(con):
    state = SquadState(
        entry_id=1,
        name="Test Team",
        bank=15,
        free_transfers=2,
        squad={101, 102, 103},
        last_synced_gw=5,
    )
    save_state(con, state)
    loaded = load_state(con, 1)
    assert loaded is not None
    assert loaded.entry_id == 1
    assert loaded.name == "Test Team"
    assert loaded.bank == 15
    assert loaded.free_transfers == 2
    assert loaded.squad == {101, 102, 103}
    assert loaded.last_synced_gw == 5


def test_load_state_returns_none_when_no_entry(con):
    assert load_state(con, 999) is None


def test_save_state_upsert_overwrites_previous(con):
    state1 = SquadState(
        entry_id=1, name="Team", bank=10, free_transfers=1, squad={1}, last_synced_gw=1
    )
    save_state(con, state1)
    state2 = SquadState(
        entry_id=1, name="Team", bank=5, free_transfers=3, squad={2, 3}, last_synced_gw=2
    )
    save_state(con, state2)

    loaded = load_state(con, 1)
    assert loaded.bank == 5
    assert loaded.free_transfers == 3
    assert loaded.squad == {2, 3}
    # old gw1 picks should not leak into gw2's squad
    assert 1 not in loaded.squad


def test_purchase_price_inferred_from_transfer_history(con):
    con.execute(
        "INSERT INTO our_transfers (gw, code_in, code_out, cost_in, cost_out) "
        "VALUES (3, 101, 55, 62, 45)"
    )
    state = SquadState(
        entry_id=1, name="Team", bank=0, free_transfers=1, squad={101}, last_synced_gw=3
    )
    save_state(con, state)
    loaded = load_state(con, 1)
    assert loaded.purchase_prices.get(101) == 62


def test_reconcile_no_discrepancy_when_matching():
    state = SquadState(entry_id=1, name="Team", bank=0, free_transfers=1, squad={1, 2, 3})
    assert reconcile(state, {1, 2, 3}) == []


def test_reconcile_flags_missing_expected_player():
    state = SquadState(entry_id=1, name="Team", bank=0, free_transfers=1, squad={1, 2, 3})
    warnings = reconcile(state, {1, 2})
    assert len(warnings) == 1
    assert "3" in warnings[0]


def test_reconcile_flags_unexpected_player():
    state = SquadState(entry_id=1, name="Team", bank=0, free_transfers=1, squad={1, 2, 3})
    warnings = reconcile(state, {1, 2, 3, 4})
    assert len(warnings) == 1
    assert "4" in warnings[0]


def test_reconcile_flags_both_missing_and_unexpected():
    state = SquadState(entry_id=1, name="Team", bank=0, free_transfers=1, squad={1, 2, 3})
    warnings = reconcile(state, {1, 2, 4})
    assert len(warnings) == 2
