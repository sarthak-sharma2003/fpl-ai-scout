"""Sync our own FPL entry + current picks from the live API into
our_entry/our_picks (Phase 4 tables) — the read half of squad-state tracking;
decide/squad_state.py's reconcile() is the compare/repair half, and is what
actually consumes these tables.

Picks for a gameweek are only public after its deadline (same rule as any
other entry's picks — see ingest/league.py); pre-deadline this is a no-op.
"""

from __future__ import annotations

import duckdb
import httpx

from fplscout import db
from fplscout.ingest.fpl_api import FplApiClient


def sync_entry(
    con: duckdb.DuckDBPyConnection,
    client: FplApiClient,
    entry_id: int,
    element_to_code: dict[int, int],
) -> dict[str, int]:
    """Returns {"gw": n, "picks": n}, or {} if this entry's current gameweek
    picks aren't public yet (pre-deadline)."""
    entry = client.entry(entry_id, force_refresh=True)
    gw = entry.current_event
    if gw is None:
        return {}
    try:
        picks_resp = client.entry_picks(entry_id, gw, force_refresh=True)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return {}  # not public yet — deadline hasn't passed
        raise

    h = picks_resp.entry_history
    con.execute(
        "INSERT INTO our_entry (entry_id, name, bank, team_value, last_synced_gw, "
        "event_transfers_cost) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (entry_id) DO UPDATE SET name = excluded.name, "
        "bank = excluded.bank, team_value = excluded.team_value, "
        "last_synced_gw = excluded.last_synced_gw, "
        "event_transfers_cost = excluded.event_transfers_cost",
        [entry_id, entry.name, h.bank, h.value, gw, h.event_transfers_cost],
    )

    con.execute("DELETE FROM our_picks WHERE gw = ?", [gw])
    n_picks = db.executemany(
        con,
        "INSERT INTO our_picks (gw, code, position, multiplier, is_captain, "
        "is_vice_captain) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (gw, element_to_code.get(p.element), p.position, p.multiplier,
             p.is_captain, p.is_vice_captain)
            for p in picks_resp.picks
        ],
    )
    return {"gw": gw, "picks": n_picks}
