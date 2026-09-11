"""Our squad state — plan §Phase4: tracks bank, free transfers, chips used,
current 15 (with purchase prices, needed for the selling-price rule in
optimizer.py). The app decides every move, so it always knows the squad in
principle; `reconcile()` exists to catch the human not applying a recommendation
("SCREAMS if drift detected", per plan) rather than to be the source of truth
itself — the public `entry/{id}/event/{gw}/picks/` endpoint is the check, not the
source, since `our_entry`/`our_picks` are only as correct as our own bookkeeping.

Free transfers cap at 5 (2024-25 rule change from the historical 1-2 cap) — the
plan flagged this as "verify"; confirmed live in bootstrap-static's game_config
2026-07-08: `max_extra_free_transfers: 4`, i.e. 1 base + up to 4 banked = 5 max.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb

MAX_FREE_TRANSFERS = 5  # verified: game_config.rules.max_extra_free_transfers=4 (+1 base)


@dataclass
class SquadState:
    entry_id: int
    name: str
    bank: int  # tenths of a million
    free_transfers: int
    squad: set[int] = field(default_factory=set)  # player codes
    purchase_prices: dict[int, int] = field(default_factory=dict)
    chips_used: set[str] = field(default_factory=set)
    last_synced_gw: int | None = None


def load_state(con: duckdb.DuckDBPyConnection, entry_id: int) -> SquadState | None:
    row = con.execute(
        "SELECT entry_id, name, bank, free_transfers, last_synced_gw "
        "FROM our_entry WHERE entry_id = ?",
        [entry_id],
    ).fetchone()
    if row is None:
        return None
    entry_id_, name, bank, free_transfers, last_synced_gw = row

    if last_synced_gw is None:
        squad, purchase_prices = set(), {}
    else:
        picks = con.execute(
            "SELECT code FROM our_picks WHERE gw = ?", [last_synced_gw]
        ).fetchall()
        squad = {p[0] for p in picks}
        # Transfers already confirmed for a gameweek whose picks FPL has not
        # published yet. Picks appear only AFTER a deadline, so a move you made
        # this morning lives solely in entry/{id}/transfers -- and without
        # replaying it here the app recommends selling players you no longer
        # own, offers to buy the ones you just bought, and believes the free
        # transfers you have spent are still yours. Bank moves by the real
        # sale and purchase prices FPL recorded at the time.
        for code_in, code_out, cost_in, cost_out in con.execute(
            "SELECT code_in, code_out, cost_in, cost_out FROM our_transfers "
            "WHERE gw > ? ORDER BY \"time\"",
            [last_synced_gw],
        ).fetchall():
            squad.discard(code_out)
            squad.add(code_in)
            bank += (cost_out or 0) - (cost_in or 0)
            # ponytail: one FT per transfer. A wildcard/free-hit week charges
            # none, but those are visible only post-deadline in history, so
            # this over-counts there and floors at 0 rather than going negative.
            free_transfers = max(0, free_transfers - 1)
        purchase_prices = _infer_purchase_prices(con, entry_id, squad)

    chip_rows = con.execute(
        "SELECT DISTINCT chip FROM recommendations "
        "WHERE season = (SELECT MAX(season) FROM recommendations) AND chip IS NOT NULL"
    ).fetchall()
    chips_used = {c[0] for c in chip_rows}

    return SquadState(
        entry_id=entry_id_,
        name=name,
        bank=bank,
        free_transfers=free_transfers,
        squad=squad,
        purchase_prices=purchase_prices,
        chips_used=chips_used,
        last_synced_gw=last_synced_gw,
    )


def _infer_purchase_prices(
    con: duckdb.DuckDBPyConnection, entry_id: int, squad: set[int]
) -> dict[int, int]:
    """Purchase price = the price at the most recent transfer bringing each
    player in, or (for players never transferred, e.g. from the initial draft)
    their price at the time our_entry was first synced. Falls back to leaving a
    code out of the dict if neither is known — callers should default to current
    price in that case (no gain/loss assumed)."""
    prices: dict[int, int] = {}
    for code in squad:
        row = con.execute(
            "SELECT cost_in FROM our_transfers WHERE code_in = ? "
            "ORDER BY gw DESC LIMIT 1",
            [code],
        ).fetchone()
        if row is not None:
            prices[code] = row[0]
    return prices


def save_state(con: duckdb.DuckDBPyConnection, state: SquadState) -> None:
    con.execute(
        """
        INSERT INTO our_entry (entry_id, name, bank, team_value, free_transfers, last_synced_gw)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (entry_id) DO UPDATE SET
            name = excluded.name,
            bank = excluded.bank,
            free_transfers = excluded.free_transfers,
            last_synced_gw = excluded.last_synced_gw
        """,
        [
            state.entry_id,
            state.name,
            state.bank,
            state.bank,  # team_value placeholder until price-tracking is fuller
            state.free_transfers,
            state.last_synced_gw,
        ],
    )
    if state.last_synced_gw is not None:
        con.execute("DELETE FROM our_picks WHERE gw = ?", [state.last_synced_gw])
        for code in state.squad:
            con.execute(
                "INSERT INTO our_picks (gw, code, position, multiplier, "
                "is_captain, is_vice_captain) VALUES (?, ?, NULL, 1, FALSE, FALSE)",
                [state.last_synced_gw, code],
            )


def chip_available(
    con: duckdb.DuckDBPyConnection, season: str, gw: int, entry_id: int, chip: str
) -> bool:
    """True when `chip` has an open window covering `gw` that this entry hasn't
    already spent inside that window.

    Chip usage is read from `rival_gw`, which holds one row per (entry, gw)
    including our own — ingest/entry.py writes ours, ingest/league.py writes
    rivals'. Modern FPL grants the same chip once per window (two wildcards a
    season, one per half), so availability is per-window, not per-season.
    """
    window = con.execute(
        "SELECT start_event, stop_event FROM chip_windows "
        "WHERE season = ? AND chip = ? AND start_event <= ? AND stop_event >= ? "
        "ORDER BY start_event LIMIT 1",
        [season, chip, gw, gw],
    ).fetchone()
    if window is None:
        return False
    start, stop = window
    used = con.execute(
        "SELECT COUNT(*) FROM rival_gw WHERE season = ? AND entry_id = ? "
        "AND active_chip = ? AND gw BETWEEN ? AND ?",
        [season, entry_id, chip, start, stop],
    ).fetchone()[0]
    return used == 0


def reconcile(
    state: SquadState, live_picks_codes: set[int]
) -> list[str]:
    """Compare our recorded squad against the actual live picks (already mapped
    from the API's season-scoped element_id to persistent code by the caller).
    Empty list = clean; anything else means the human didn't apply the last
    recommendation (or we drifted for some other reason) and must be surfaced
    loudly, not silently auto-corrected."""
    warnings: list[str] = []
    missing = state.squad - live_picks_codes
    extra = live_picks_codes - state.squad
    if missing:
        warnings.append(
            f"Expected {len(missing)} player(s) not in live picks: {sorted(missing)} "
            f"— recommendation may not have been applied."
        )
    if extra:
        warnings.append(
            f"Live picks contain {len(extra)} player(s) we didn't expect: "
            f"{sorted(extra)} — squad drifted from our records."
        )
    return warnings
