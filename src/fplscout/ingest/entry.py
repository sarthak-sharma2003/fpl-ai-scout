"""Sync our own FPL entry + current picks from the live API into
our_entry/our_picks (Phase 4 tables) — the read half of squad-state tracking;
decide/squad_state.py's reconcile() is the compare/repair half, and is what
actually consumes these tables.

Picks for a gameweek are only public after its deadline (same rule as any
other entry's picks — see ingest/league.py); pre-deadline this is a no-op.

Free transfers are NOT exposed by any public endpoint (only the login-walled
my-team is), so they're derived from the public transfer counts per gameweek
using the same accrual rule the backtest simulator applies — see
_derive_free_transfers.
"""

from __future__ import annotations

import duckdb
import httpx

from fplscout import db
from fplscout.decide.squad_state import MAX_FREE_TRANSFERS
from fplscout.ingest.fpl_api import FplApiClient

# chips that don't consume a free transfer (the week's FT still banks)
FT_NEUTRAL_CHIPS = {"wildcard", "freehit"}


def _derive_free_transfers(history) -> int:
    """Free transfers available for the NEXT gameweek, from public history.

    Mirrors backtest/simulator.py's accrual exactly: the initial draft leaves
    you with 1; a wildcard/free-hit week banks one without spending any; an
    ordinary week spends what you used and banks one, capped at
    MAX_FREE_TRANSFERS and floored at zero (hits go below "free" but never
    below 0 available).
    """
    chip_by_event = {c.event: c.name for c in history.chips}
    ft = 1
    for i, h in enumerate(sorted(history.current, key=lambda r: r.event)):
        if i == 0:
            ft = 1  # initial draft: unlimited transfers, not FT-consuming
        elif chip_by_event.get(h.event) in FT_NEUTRAL_CHIPS:
            ft = min(MAX_FREE_TRANSFERS, ft + 1)
        else:
            ft = min(MAX_FREE_TRANSFERS, max(0, ft - h.event_transfers) + 1)
    return ft


def sync_entry(
    con: duckdb.DuckDBPyConnection,
    client: FplApiClient,
    entry_id: int,
    element_to_code: dict[int, int],
    season: str | None = None,
) -> dict[str, int]:
    """Returns {"gw": n, "picks": n, "free_transfers": n}, or {} if this
    entry's current gameweek picks aren't public yet (pre-deadline)."""
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

    history = client.entry_history(entry_id, force_refresh=True)
    free_transfers = _derive_free_transfers(history)

    h = picks_resp.entry_history
    con.execute(
        "INSERT INTO our_entry (entry_id, name, bank, team_value, free_transfers, "
        "last_synced_gw, event_transfers_cost) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (entry_id) DO UPDATE SET name = excluded.name, "
        "bank = excluded.bank, team_value = excluded.team_value, "
        "free_transfers = excluded.free_transfers, "
        "last_synced_gw = excluded.last_synced_gw, "
        "event_transfers_cost = excluded.event_transfers_cost",
        [entry_id, entry.name, h.bank, h.value, free_transfers, gw, h.event_transfers_cost],
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

    # Real purchase prices for the selling-price rule (optimizer prices a sale
    # at 50% of profit). Without these every held player is assumed to be worth
    # exactly what it costs today, which misprices every funded transfer.
    transfers = client.entry_transfers(entry_id, force_refresh=True)
    con.execute("DELETE FROM our_transfers")
    db.executemany(
        con,
        'INSERT INTO our_transfers (gw, code_in, code_out, cost_in, cost_out, "time") '
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (t.event, element_to_code.get(t.element_in), element_to_code.get(t.element_out),
             t.element_in_cost, t.element_out_cost, t.time)
            for t in transfers
            if element_to_code.get(t.element_in) and element_to_code.get(t.element_out)
        ],
    )

    # Our own per-GW row (points/chips) in the same table the mini-league sync
    # fills for rivals — publish.build_chips reads chip usage from here, and it
    # must work whether or not a mini_league_id is configured.
    if season is not None:
        chip_by_event = {c.event: c.name for c in history.chips}
        db.executemany(
            con,
            "INSERT INTO rival_gw (season, gw, entry_id, points, total_points, bank, "
            "team_value, event_transfers_cost, points_on_bench, active_chip) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (season, gw, entry_id) DO UPDATE SET "
            "points = excluded.points, total_points = excluded.total_points, "
            "bank = excluded.bank, team_value = excluded.team_value, "
            "event_transfers_cost = excluded.event_transfers_cost, "
            "points_on_bench = excluded.points_on_bench, "
            "active_chip = excluded.active_chip",
            [
                (season, r.event, entry_id, r.points, r.total_points, r.bank, r.value,
                 r.event_transfers_cost, r.points_on_bench, chip_by_event.get(r.event))
                for r in history.current
            ],
        )

    return {"gw": gw, "picks": n_picks, "free_transfers": free_transfers}
