"""Weekly "DO THIS" sheet (plan §8): the latest recommendation rendered as
human-readable markdown. Pure renderer — reads what project/optimize already
wrote; cli.py's `report` command chains the pipeline first."""

from __future__ import annotations

import json

import duckdb

POSITION_ORDER = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}


def render_weekly(con: duckdb.DuckDBPyConnection, season: str, gw: int) -> str:
    rec = con.execute(
        "SELECT squad, starting_xi, captain_code, vice_captain_code, transfers, "
        "hits, chip, generated_at FROM recommendations "
        "WHERE season = ? AND gw = ? ORDER BY generated_at DESC LIMIT 1",
        [season, gw],
    ).fetchone()
    if rec is None:
        raise ValueError(f"no recommendation for {season} GW{gw} — run `fplscout optimize`")
    squad, xi, captain, vice, transfers, hits, chip, generated_at = rec
    squad, xi, transfers = json.loads(squad), set(json.loads(xi)), json.loads(transfers)

    placeholders = ", ".join(["?"] * len(squad))
    players = {
        row[0]: row[1:]
        for row in con.execute(
            f"""
            SELECT p.code, p.web_name, ps.position, proj.ev_points,
                   p.status, p.news, p.penalties_order
            FROM players p
            LEFT JOIN (
                SELECT DISTINCT code, position FROM features WHERE season = ?
            ) ps ON ps.code = p.code
            LEFT JOIN projections proj
              ON proj.code = p.code AND proj.season = ? AND proj.gw = ?
            WHERE p.code IN ({placeholders})
            QUALIFY ROW_NUMBER() OVER (PARTITION BY p.code ORDER BY proj.generated_at DESC) = 1
            """,
            [season, season, gw, *squad],
        ).fetchall()
    }
    UNKNOWN = ("?", "?", None, None, None, None)

    def line(code: int) -> str:
        name, position, ev, status, news, pens = players.get(code, (f"code {code}", *UNKNOWN[1:]))
        ev_txt = f" ({ev:.1f} EV)" if ev is not None else ""
        # info flags for the human sanity-check: PK = first-choice penalty
        # taker (already priced into xG, shown for awareness); a non-'a'
        # status with its news is a "look at this before confirming" marker
        flags = ""
        if pens == 1:
            flags += " ⚽PK"
        if status is not None and status != "a":
            flags += f" ⚠{status.upper()}" + (f" ({news})" if news else "")
        return f"{name} [{position}]{ev_txt}{flags}"

    def by_position(codes) -> list[int]:
        def key(c: int) -> int:
            return POSITION_ORDER.get(players.get(c, ("", "?", 0))[1], 9)

        return sorted(codes, key=key)

    out = [
        f"# DO THIS — {season} GW{gw}",
        "",
        f"Generated {generated_at} | chip: **{chip or 'none'}** | hits: **{hits}**",
        "",
        f"**Captain:** {line(captain)}",
        f"**Vice:** {line(vice)}" if vice is not None else "**Vice:** (none)",
        "",
        "## Starting XI",
        *[f"- {line(c)}" for c in by_position(xi)],
        "",
        "## Bench",
        # ponytail: bench ORDER isn't persisted in recommendations yet — listed
        # unordered; add a bench_order column when it matters
        *[f"- {line(c)}" for c in by_position(set(squad) - xi)],
    ]
    if transfers:
        out += ["", "## Transfers", *[f"- {t}" for t in transfers]]
    return "\n".join(out) + "\n"
