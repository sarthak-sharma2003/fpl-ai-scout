"""Which gameweek is being decided right now.

"The first unfinished gameweek" is wrong for ~3 days of every week: FPL marks
an event finished only once its last match is played and bonus confirmed, so
between Friday's deadline and Monday night's final whistle the first
unfinished event is the one already LOCKED. Both the feature store's upcoming
rows and every live command used that rule, so the site spent each weekend
advising on a gameweek nobody could change (caught 2026-10-13: the published
sheet was for GW6, deadline 10 Oct, while GW7's deadline was 17 Oct).

The decision gameweek is the first unfinished event whose deadline is still
ahead. When none is (season over, or a DB whose deadlines are all in the past,
like every backtest/rehearsal fixture), fall back to the first unfinished
event, which is what the old rule returned.
"""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb


def decision_gameweek(
    con: duckdb.DuckDBPyConnection, now: datetime | None = None
) -> tuple[str, int] | None:
    now = (now or datetime.now(UTC)).replace(tzinfo=None)
    row = con.execute(
        "SELECT season, MIN(event) FROM gameweeks "
        "WHERE NOT finished AND deadline_time > ? "
        "GROUP BY season ORDER BY season DESC LIMIT 1",
        [now],
    ).fetchone()
    if row is not None:
        return row[0], row[1]
    row = con.execute(
        "SELECT season, MIN(event) FROM gameweeks WHERE NOT finished "
        "GROUP BY season ORDER BY season DESC LIMIT 1"
    ).fetchone()
    return (row[0], row[1]) if row is not None else None
