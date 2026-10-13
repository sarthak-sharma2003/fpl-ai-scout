"""Pre-deadline availability + `ep_next` per (season, gw, player).

Why this exists: the minutes model only ever saw realized history (did he start
the last few?), and live injury/doubt news was bolted on at inference time as a
multiplier (models/minutes.py::apply_availability) because historical status
snapshots "leak". They do -- if you take them from vaastav's end-of-season
players_raw.csv. Taken from bootstrap snapshots captured BEFORE each deadline
they are exactly what a manager knew, and they are the single largest measured
gain available: on the 2023-24/2024-25 holdouts, adding them (with the hurdle +
xPts target, scripts/model_lab.py) took per-GW Spearman among regulars from
~0.30 to ~0.41 and top-K realized points per pick from 4.12/4.32 to 4.66/4.52.

Three writers, one table (`predeadline_signals`, see db.py):

* history (2021-22 .. 2025-26): qazybekb/smartplayfpl-dastan's
  provenance-checked extraction of Randdalf/fplcache (MIT; the last snapshot
  before each deadline, accepted only if that event is `is_next`; median 4.3h
  before the deadline, none after).
* backfill (the live season's past gameweeks): the same rule applied directly
  to Randdalf/fplcache, for gameweeks we did not snapshot ourselves.
* live (`snapshot_live`): every `refresh` before the decision gameweek's
  deadline overwrites that gameweek's rows with the current bootstrap, so the
  last refresh before the deadline is what remains -- the same semantics as
  the history.

Encoding matches Dastan's exactly (it is the training data): status a/d/s/i/u|n
-> 0/1/2/3/4; chance None -> NULL (read as -1).
"""

from __future__ import annotations

import json
import lzma
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import httpx

from fplscout import db

DASTAN_RAW = "https://raw.githubusercontent.com/qazybekb/smartplayfpl-dastan/main/data/"
DASTAN_FILES = ("pre_deadline_signals.parquet", "pre_deadline_ep_next.parquet")
FPLCACHE_REPO = "Randdalf/fplcache"
FPLCACHE_RAW = f"https://raw.githubusercontent.com/{FPLCACHE_REPO}/main/"
FPLCACHE_TREE = f"https://api.github.com/repos/{FPLCACHE_REPO}/git/trees/main?recursive=1"
STATUS_RISK = {"a": 0, "d": 1, "s": 2, "i": 3, "u": 4, "n": 4}
SAFETY_BUFFER = timedelta(minutes=5)
_PATH = re.compile(r"^cache/(\d{4})/(\d{1,2})/(\d{1,2})/(\d{2})(\d{2})\.json\.xz$")

UPSERT = (
    "INSERT OR REPLACE INTO predeadline_signals "
    "(season, gw, code, status_risk, chance_playing, has_news, ep_next, snapshot_at, source) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _get(url: str, timeout: float = 60.0, headers: dict | None = None) -> bytes:
    with httpx.Client(timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": "fpl-ai-scout/1.0", **(headers or {})}) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.content


def sync_history(con: duckdb.DuckDBPyConnection, cache_dir: Path) -> int:
    """Load Dastan's 2021-22..2025-26 pre-deadline rows. Idempotent; seasons
    already present are left alone (the history does not change)."""
    have = {r[0] for r in con.execute(
        "SELECT DISTINCT season FROM predeadline_signals WHERE source = 'dastan'"
    ).fetchall()}
    if {"2021-22", "2022-23", "2023-24", "2024-25", "2025-26"} <= have:
        return 0
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in DASTAN_FILES:
        local = cache_dir / name
        if not local.exists():
            local.write_bytes(_get(DASTAN_RAW + name))
        paths.append(str(local))
    sig, ep = paths
    con.execute(
        f"""
        INSERT OR REPLACE INTO predeadline_signals
        SELECT COALESCE(s.season, e.season), COALESCE(s.gameweek, e.gameweek),
               COALESCE(s.fpl_code, e.fpl_code),
               s.sig_status_risk,
               CASE WHEN s.sig_chance_playing < 0 THEN NULL ELSE s.sig_chance_playing END,
               s.sig_has_news, e.ep_next,
               CAST(COALESCE(s.snapshot_at, e.snapshot_at) AS TIMESTAMP), 'dastan'
        FROM read_parquet('{sig}') s
        FULL OUTER JOIN read_parquet('{ep}') e
          ON s.season = e.season AND s.gameweek = e.gameweek AND s.fpl_code = e.fpl_code
        WHERE COALESCE(s.season, e.season) >= '2021-22'
        """
    )
    return con.execute(
        "SELECT count(*) FROM predeadline_signals WHERE source = 'dastan'"
    ).fetchone()[0]


def _rows_from_elements(elements, season: str, gw: int, snapshot_at: datetime,
                        source: str) -> list[tuple]:
    rows = []
    for e in elements:
        get = e.get if isinstance(e, dict) else (lambda k, _e=e: getattr(_e, k, None))
        chance = get("chance_of_playing_next_round")
        ep = get("ep_next")
        rows.append((
            season, gw, int(get("code")),
            STATUS_RISK.get(str(get("status") or "a"), 0),
            None if chance is None else float(chance),
            float(bool((get("news") or "").strip())),
            None if ep in (None, "") else float(ep),
            snapshot_at.replace(tzinfo=None), source,
        ))
    return rows


def snapshot_live(con: duckdb.DuckDBPyConnection, bootstrap, season: str,
                  now: datetime | None = None) -> int:
    """Write the decision gameweek's rows from the live bootstrap, if its
    deadline is still ahead (never after: that would leak post-deadline news
    into a row the model trains on once the gameweek is played)."""
    now = now or datetime.now(UTC)
    nxt = next((ev for ev in bootstrap.events if getattr(ev, "is_next", False)), None)
    if nxt is None or not getattr(nxt, "deadline_time", None):
        return 0
    deadline = nxt.deadline_time
    if isinstance(deadline, str):
        deadline = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    if now >= deadline:
        return 0
    rows = _rows_from_elements(bootstrap.elements, season, int(nxt.id), now, "live")
    return db.executemany(con, UPSERT, rows)


def _fplcache_index(cache_dir: Path) -> dict[datetime, str]:
    headers = {}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    tree = json.loads(_get(FPLCACHE_TREE, headers=headers))
    index = {}
    for entry in tree.get("tree", []):
        m = _PATH.match(entry.get("path", ""))
        if entry.get("type") != "blob" or not m:
            continue
        y, mo, d, hh, mm = map(int, m.groups())
        try:
            index[datetime(y, mo, d, hh, mm, tzinfo=UTC)] = entry["path"]
        except ValueError:
            continue
    return index


def backfill_live_season(con: duckdb.DuckDBPyConnection, cache_dir: Path,
                         season: str, now: datetime | None = None) -> int:
    """Past gameweeks of the live season with no rows yet, from fplcache.

    Needs the GitHub API (to list snapshots) and raw.githubusercontent.com;
    any failure just leaves those gameweeks missing (-1 features), which the
    model already handles -- never a reason to fail `refresh`.
    """
    now = now or datetime.now(UTC)
    missing = con.execute(
        """
        SELECT g.event, g.deadline_time FROM gameweeks g
        WHERE g.season = ? AND g.deadline_time < ?
          AND NOT EXISTS (SELECT 1 FROM predeadline_signals p
                          WHERE p.season = g.season AND p.gw = g.event)
        ORDER BY g.event
        """,
        [season, now.replace(tzinfo=None)],
    ).fetchall()
    if not missing:
        return 0
    index = _fplcache_index(cache_dir)
    written = 0
    for gw, deadline in missing:
        deadline = deadline.replace(tzinfo=UTC)
        eligible = [t for t in index if t <= deadline - SAFETY_BUFFER]
        if not eligible:
            continue
        captured = max(eligible)
        local = cache_dir / "fplcache" / index[captured]
        if not local.exists():
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(_get(FPLCACHE_RAW + index[captured]))
        payload = json.loads(lzma.decompress(local.read_bytes()))
        ev = next((e for e in payload.get("events", []) if int(e["id"]) == gw), None)
        if not ev or not ev.get("is_next"):
            continue          # provenance check failed: not the pre-deadline view
        rows = _rows_from_elements(payload.get("elements", []), season, gw, captured,
                                   "fplcache")
        written += db.executemany(con, UPSERT, rows)
    return written
