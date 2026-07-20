"""Mini-league rival intel sync.

Reads the public classic-league API (standings, each rival's per-GW history and
picks) into league_standings / rival_gw / rival_picks. Runs from `fplscout
refresh` when settings.mini_league_id is set; a league of N humans costs
~2N throttled calls per sync in steady state.

Everything here is read-only intel for the publish layer (who owns what, chips
burned, our-model EV of each rival's squad). It NEVER feeds training or the
optimizer — beating the projection is the model's job; this exists so the human
knows the race they're in (coverage vs. differentials) when eyeballing the
DO THIS sheet.

Picks for a gameweek are only public after its deadline, and entries that
joined late have no picks for early GWs — both come back 404 and are skipped,
not fatal.
"""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb
import httpx

from fplscout.ingest.fpl_api import FplApiClient

# don't hammer the API for giant public leagues; a friends league is way smaller
MAX_ENTRIES_SYNCED = 20
# per sync, how many recent finished GWs to backfill picks for per entry —
# steady state needs 1; 3 self-heals a missed week or two without a call storm
PICKS_BACKFILL_GWS = 3


def sync_league(
    con: duckdb.DuckDBPyConnection,
    client: FplApiClient,
    league_id: int,
    season: str,
    element_to_code: dict[int, int],
) -> dict[str, int]:
    """Returns {"entries": n, "gw_rows": n, "pick_rows": n}."""
    standings = client.league_standings(league_id, force_refresh=True)
    rows = standings.standings.results[:MAX_ENTRIES_SYNCED]
    fetched_at = datetime.now(UTC)

    con.execute("DELETE FROM league_standings WHERE league_id = ?", [league_id])
    con.executemany(
        "INSERT INTO league_standings (league_id, entry_id, league_name, entry_name, "
        "player_name, rank, last_rank, total, event_total, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                league_id, r.entry, standings.league.name, r.entry_name,
                r.player_name, r.rank, r.last_rank, r.total, r.event_total, fetched_at,
            )
            for r in rows
        ],
    )

    finished = con.execute(
        "SELECT event FROM gameweeks WHERE season = ? AND finished ORDER BY event",
        [season],
    ).fetchall()
    finished_gws = [g[0] for g in finished]
    if not finished_gws:  # pre-GW1: standings exist (all zeros), nothing else yet
        return {"entries": len(rows), "gw_rows": 0, "pick_rows": 0}

    gw_rows = pick_rows = 0
    for r in rows:
        history = client.entry_history(r.entry, force_refresh=True)
        chip_by_event = {c.event: c.name for c in history.chips}
        con.executemany(
            "INSERT INTO rival_gw (season, gw, entry_id, points, total_points, bank, "
            "team_value, event_transfers_cost, points_on_bench, active_chip) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (season, gw, entry_id) DO UPDATE SET "
            "points = excluded.points, total_points = excluded.total_points, "
            "bank = excluded.bank, team_value = excluded.team_value, "
            "event_transfers_cost = excluded.event_transfers_cost, "
            "points_on_bench = excluded.points_on_bench, active_chip = excluded.active_chip",
            [
                (
                    season, h.event, r.entry, h.points, h.total_points, h.bank,
                    h.value, h.event_transfers_cost, h.points_on_bench,
                    chip_by_event.get(h.event),
                )
                for h in history.current
            ],
        )
        gw_rows += len(history.current)

        have = {
            g[0]
            for g in con.execute(
                "SELECT DISTINCT gw FROM rival_picks WHERE season = ? AND entry_id = ?",
                [season, r.entry],
            ).fetchall()
        }
        want = [g for g in finished_gws if g not in have][-PICKS_BACKFILL_GWS:]
        for gw in want:
            try:
                picks = client.entry_picks(r.entry, gw)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    continue  # joined late / picks not visible — expected, skip
                raise
            con.executemany(
                "INSERT INTO rival_picks (season, gw, entry_id, element_id, code, "
                "pick_position, multiplier, is_captain, is_vice_captain) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (season, gw, entry_id, element_id) DO UPDATE SET "
                "code = excluded.code, pick_position = excluded.pick_position, "
                "multiplier = excluded.multiplier, is_captain = excluded.is_captain, "
                "is_vice_captain = excluded.is_vice_captain",
                [
                    (
                        season, gw, r.entry, p.element, element_to_code.get(p.element),
                        p.position, p.multiplier, p.is_captain, p.is_vice_captain,
                    )
                    for p in picks.picks
                ],
            )
            pick_rows += len(picks.picks)

    return {"entries": len(rows), "gw_rows": gw_rows, "pick_rows": pick_rows}
