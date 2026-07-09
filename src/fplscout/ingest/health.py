"""Live-data health checks — plan §Phase3 review follow-up.

`ep_next` is not currently a model feature (vaastav's historical `xP` — the same
underlying FPL field, `ep_this` — was found to be scraped post-gameweek and
confirmed post-match-contaminated; see models/points.py). This check exists for
when we resume accumulating our own pre-deadline `ep_next` snapshots (ingest/
health.py's archiving, effective from GW1 of 26/27): live-fetched values don't have
vaastav's timing problem, but if FPL's own `ep_next` ever goes degenerate mid-season
(all zero, all null, or otherwise nonsensical), that would silently corrupt our own
archive the same way — worse than a scraping outage, because nothing else fails
loudly. Checked healthy live 2026-07-08: per-position means ranged 0.68-1.31
(element_type 1=GKP .. 4=FWD); vaastav's historical `xP` outages (see models/
dataset.py's git history) showed entire-gameweek means going to exactly 0.0, so
that's the specific failure shape to watch for.
"""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb

from fplscout.ingest.schemas import BootstrapStatic

DEGENERATE_MEAN_THRESHOLD = 0.2  # healthy range observed live: ~0.6-1.3 per position
HIGH_NULL_FRACTION_THRESHOLD = 0.5


def check_ep_next_health(bootstrap: BootstrapStatic) -> list[str]:
    """Returns a list of human-readable warnings; empty means healthy."""
    warnings: list[str] = []

    by_position: dict[int, list[float]] = {}
    null_count = 0
    for element in bootstrap.elements:
        if element.ep_next is None:
            null_count += 1
            continue
        try:
            value = float(element.ep_next)
        except ValueError:
            continue
        by_position.setdefault(element.element_type, []).append(value)

    for position_id in sorted(by_position):
        values = by_position[position_id]
        mean = sum(values) / len(values)
        if mean < DEGENERATE_MEAN_THRESHOLD:
            warnings.append(
                f"element_type {position_id}: mean ep_next={mean:.3f} across "
                f"{len(values)} players looks degenerate (healthy range observed "
                f"live is roughly 0.6-1.3). This is the failure shape seen in "
                f"vaastav's historical xP outages (see models/dataset.py)."
            )

    total = len(bootstrap.elements)
    if total > 0 and null_count / total > HIGH_NULL_FRACTION_THRESHOLD:
        warnings.append(
            f"{null_count}/{total} players have null ep_next "
            f"({null_count / total:.0%}) — well above normal."
        )

    return warnings


def archive_ep_next(con: duckdb.DuckDBPyConnection, bootstrap: BootstrapStatic) -> int:
    """Persists one row per player with a parseable ep_next to ep_next_archive,
    timestamped now. Effective immediately (plan review directive) — not waiting
    for the 26/27 season to start, so the archive is already accumulating by the
    time there's a real gameweek to validate against. Returns rows written."""
    next_event = next((e for e in bootstrap.events if e.is_next), None)
    gw = next_event.id if next_event is not None else None
    snapshot_time = datetime.now(UTC)

    rows = []
    for element in bootstrap.elements:
        if element.ep_next is None:
            continue
        try:
            value = float(element.ep_next)
        except ValueError:
            continue
        rows.append((snapshot_time, element.code, element.id, gw, value))

    if not rows:
        return 0

    con.executemany(
        "INSERT INTO ep_next_archive (snapshot_time, code, element_id, gw, ep_next) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (snapshot_time, code) DO NOTHING",
        rows,
    )
    return len(rows)
