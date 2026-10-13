"""Weekly "DO THIS" sheet (plan §8): the latest recommendation rendered as
human-readable markdown. Pure renderer — reads what project/optimize already
wrote; cli.py's `report` command chains the pipeline first."""

from __future__ import annotations

import json

import duckdb

POSITION_ORDER = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}


CHIP_NAMES = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost",
              "3xc": "Triple Captain"}
CHIP_ORDER = ["wildcard", "freehit", "bboost", "3xc"]
CHIP_BASIS = {"wildcard": "over a free transfer a week",
              "freehit": "over your own XI",
              "bboost": "from your bench",
              "3xc": "extra from the armband"}


def render_chips(chips: dict, gw: int) -> list[str]:
    """Every chip's verdict from chips.json (publish.build_chips): checked on
    every run and shown whatever it says -- "hold" is an answer too."""
    rows = [c for c in chips.get("chips", []) if c.get("advice")]
    if not rows:
        return []
    out = ["", "## Chips (checked this run)"]
    later = []
    for c in sorted(rows, key=lambda c: CHIP_ORDER.index(c["chip"])
                    if c["chip"] in CHIP_ORDER else 9):
        name = CHIP_NAMES.get(c["chip"], c["chip"])
        advice, bar = c["advice"], c.get("bar")
        now, basis = c.get("this_week_ev"), CHIP_BASIS.get(c["chip"], "")
        who = ""
        if c["chip"] == "3xc" and isinstance(c.get("this_week"), dict):
            who = f"{c['this_week'].get('name')} " if c["this_week"].get("name") else ""
        now_txt = f"this week {who}+{now:.1f} {basis}" if now is not None else ""
        bar_txt = f"bar to play: +{bar:.1f}" if bar is not None else ""
        planned = (f"planned GW{c['planned_gw']} (+{c['planned_ev']:.1f})"
                   if c.get("planned_gw") and c.get("planned_gw") != gw
                   and c.get("planned_ev") is not None else "")
        if advice == "hold" and c.get("planned_gw") == gw:
            planned = ""
            bar_txt += " — this is its best week in view, but it misses the bar"
        if advice == "used":
            out.append(f"- {name}: used (GW{c.get('used_gw')}).")
        elif advice == "not_yet":
            later.append(name)
        elif advice == "play":
            out.append(f"- **{name}: PLAY IT THIS WEEK** ("
                       + "; ".join(filter(None, [now_txt, bar_txt])) + ").")
        elif advice == "planned":
            out.append(f"- {name}: hold for now, {planned}; "
                       + "; ".join(filter(None, [now_txt, bar_txt])) + ".")
        else:
            out.append(f"- {name}: hold ("
                       + "; ".join(filter(None, [now_txt, bar_txt])) + ")"
                       + (f", {planned} if nothing better comes up." if planned else "."))
        if c.get("urgent") and advice not in ("used", "not_yet"):
            out[-1] += f" ⚠ expires after GW{c.get('stop_gw')}."
    if later:
        out.append(f"- Second-half chips ({', '.join(later)}): not open yet.")
    return out


def render_weekly(con: duckdb.DuckDBPyConnection, season: str, gw: int,
                  chips: dict | None = None) -> str:
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

    def bench_order(codes) -> list[int]:
        """Outfield by EV, best first, then the keeper -- the order the site's
        dashboard shows (publish._dashboard_payload); FPL autosubs top-down."""
        def key(c: int):
            pos, ev = players.get(c, UNKNOWN)[1], players.get(c, UNKNOWN)[2]
            return (pos == "GKP", -(ev if ev is not None else -99.0))
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
        "## Bench (in this order — autosubs go top to bottom)",
        *[f"{k}. {line(c)}" for k, c in enumerate(bench_order(set(squad) - xi), 1)],
    ]
    if transfers:
        out += ["", "## Transfers", *[f"- {t}" for t in transfers]]
    if chips and chips.get("season") == season and chips.get("reference_gw") == gw:
        out += render_chips(chips, gw)
    return "\n".join(out) + "\n"
