"""Provisional 26/27 from the REAL released fixture calendar, before the FPL
game launches.

    parse  <espn_fixtures.html> <fixtures.json>   one-off: article -> JSON
    inject <fixtures.json> <duckdb_path>          fabricate 2026-27 in the DB

`inject` is what the nightly deploy runs (between refresh and project): it
fabricates teams/fixtures/gameweeks/player_season for 2026-27 — real
opponents/calendar, 25/26 end prices as stand-in now_cost, promoted teams with
synthetic codes (the Dixon-Coles fallback covers them) — then rebuilds the
feature store so the normal project -> optimize -> publish chain targets a
provisional GW1. It NO-OPS once the real 26/27 exists in the DB (live_gw has
synced fixtures for it), so launch day needs no workflow change. Delete this
script after launch.

The parser self-heals ESPN omissions (the article lists 378 of 380 fixtures):
in a double round-robin each pairing appears once per direction, so a short
matchweek's absent teams uniquely identify the missing fixtures.
"""

import json
import re
import sys
from datetime import datetime
from html import unescape
from pathlib import Path

import duckdb

SEASON = "2026-27"

# ESPN's long names -> vaastav/FPL names in our teams table
NAME_MAP = {
    "Arsenal": "Arsenal", "Aston Villa": "Aston Villa", "Bournemouth": "Bournemouth",
    "Brentford": "Brentford", "Brighton and Hove Albion": "Brighton",
    "Chelsea": "Chelsea", "Crystal Palace": "Crystal Palace", "Everton": "Everton",
    "Fulham": "Fulham", "Leeds United": "Leeds", "Liverpool": "Liverpool",
    "Manchester City": "Man City", "Manchester United": "Man Utd",
    "Newcastle United": "Newcastle", "Nottingham Forest": "Nott'm Forest",
    "Sunderland": "Sunderland", "Tottenham Hotspur": "Spurs",
    # promoted for 26/27 — not in any prior season's teams table
    "Coventry City": "Coventry", "Hull City": "Hull", "Ipswich Town": "Ipswich",
}
PROMOTED_SHORT = {"Coventry": "COV", "Hull": "HUL", "Ipswich": "IPS"}


def parse_espn(html_path: Path) -> list[dict]:
    raw = html_path.read_text(errors="replace")
    body = raw[raw.find("article-body"):]
    text = unescape(re.sub(r"<[^>]+>", "\n", body))
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    fixtures, current_date = [], None
    date_re = re.compile(r"^([A-Z][a-z]+)\.? (\d{1,2}), (\d{4})$")
    match_re = re.compile(r"^(.+?) v (.+?)(?:\s+\d.*)?$")
    months = {m: i + 1 for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    )}
    for line in lines:
        d = date_re.match(line)
        if d and d.group(1)[:3] in months:
            current_date = datetime(int(d.group(3)), months[d.group(1)[:3]], int(d.group(2)), 15)
            continue
        m = match_re.match(line)
        if m and current_date is not None:
            home, away = m.group(1).strip(), m.group(2).strip()
            if home in NAME_MAP and away in NAME_MAP:
                fixtures.append(
                    {"home": NAME_MAP[home], "away": NAME_MAP[away], "kickoff": current_date}
                )

    # group into matchweeks greedily (new round when a team would repeat), then
    # infer any omitted fixtures from each short round's absent teams
    rounds: list[list[dict]] = [[]]
    for f in fixtures:
        used = {t for x in rounds[-1] for t in (x["home"], x["away"])}
        if f["home"] in used or f["away"] in used or len(rounds[-1]) == 10:
            rounds.append([])
        rounds[-1].append(f)
    assert len(rounds) == 38, f"expected 38 matchweeks, got {len(rounds)}"

    all_teams = set(NAME_MAP.values())
    seen_pairings = {(f["home"], f["away"]) for f in fixtures}
    for gw, rnd in enumerate(rounds, start=1):
        absent = sorted(all_teams - {t for f in rnd for t in (f["home"], f["away"])})
        while absent:
            t = absent.pop(0)
            partner = next(
                u for u in absent
                if ((t, u) in seen_pairings) != ((u, t) in seen_pairings)
            )
            absent.remove(partner)
            home, away = (t, partner) if (t, partner) not in seen_pairings else (partner, t)
            inferred = {"home": home, "away": away, "kickoff": rnd[0]["kickoff"]}
            print(f"inferred missing fixture (ESPN omission): GW{gw} {home} v {away}")
            rnd.append(inferred)
            seen_pairings.add((home, away))
        assert len(rnd) == 10, f"round {gw} has {len(rnd)} fixtures"
        for f in rnd:
            f["gw"] = gw
    flat = [f for rnd in rounds for f in rnd]
    assert len(flat) == 380
    return flat


def inject(con: duckdb.DuckDBPyConnection, fixtures: list[dict]) -> bool:
    """Fabricates 2026-27 in `con` and rebuilds features. Returns False (no-op)
    if the real 2026-27 already exists — i.e. the FPL API has reset and
    live_gw synced genuine fixtures — so the deploy step is launch-day-safe."""
    existing = con.execute(
        "SELECT COUNT(*) FROM fixtures WHERE season = ?", [SEASON]
    ).fetchone()[0]
    if existing:
        print(f"{SEASON} fixtures already in DB (real season live) — skipping injection")
        return False

    prev = {
        name: (code, short, strength) for name, code, short, strength in con.execute(
            "SELECT name, code, short_name, strength FROM teams WHERE season = '2025-26'"
        ).fetchall()
    }
    team_rows, name_to_id = [], {}
    next_id, next_code = 1, 90001
    for name in sorted({f["home"] for f in fixtures}):
        if name in prev:
            code, short, strength = prev[name]
        else:
            code, short, strength = next_code, PROMOTED_SHORT[name], 2
            next_code += 1
        name_to_id[name] = next_id
        team_rows.append((SEASON, next_id, code, name, short, strength))
        next_id += 1
    con.executemany(
        "INSERT INTO teams (season, team_id, code, name, short_name, strength) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        team_rows,
    )
    con.executemany(
        "INSERT INTO fixtures (season, fixture_id, event, kickoff_time, team_h, team_a, "
        "team_h_difficulty, team_a_difficulty, finished) VALUES (?, ?, ?, ?, ?, ?, 3, 3, false)",
        [
            (SEASON, i + 1, f["gw"], f["kickoff"], name_to_id[f["home"]], name_to_id[f["away"]])
            for i, f in enumerate(fixtures)
        ],
    )
    first_kick = {f["gw"]: f["kickoff"] for f in reversed(fixtures)}
    con.executemany(
        "INSERT INTO gameweeks (season, event, deadline_time, finished) VALUES (?, ?, ?, false)",
        [(SEASON, gw, first_kick[gw]) for gw in range(1, 39)],
    )
    con.execute(
        """
        INSERT INTO player_season (season, element_id, code, team_id, position, web_name, value)
        SELECT ?, ps.element_id, ps.code, m.new_id, ps.position, ps.web_name,
               (SELECT h.value FROM player_gw_history h
                WHERE h.code = ps.code AND h.season = '2025-26'
                ORDER BY h.gw DESC LIMIT 1)
        FROM player_season ps
        JOIN teams old ON old.season = '2025-26' AND old.team_id = ps.team_id
        JOIN (SELECT name, team_id AS new_id FROM teams WHERE season = ?) m
          ON m.name = old.name
        WHERE ps.season = '2025-26'
        """,
        [SEASON, SEASON],
    )
    con.execute(f"UPDATE player_season SET value = 45 WHERE season = '{SEASON}' AND value IS NULL")

    from fplscout.features.build import write_features

    n = write_features(con)
    synth = con.execute(
        "SELECT COUNT(*) FROM features WHERE season = ?", [SEASON]
    ).fetchone()[0]
    print(f"injected provisional {SEASON}: 380 fixtures, {len(team_rows)} teams; "
          f"features rebuilt ({n} rows, {synth} for {SEASON} GW1)")
    return True


def main() -> None:
    cmd = sys.argv[1]
    if cmd == "parse":
        fixtures = parse_espn(Path(sys.argv[2]))
        payload = [
            {**f, "kickoff": f["kickoff"].isoformat()} for f in fixtures
        ]
        Path(sys.argv[3]).write_text(json.dumps(payload, indent=1))
        print(f"wrote {len(payload)} fixtures to {sys.argv[3]}")
    elif cmd == "inject":
        fixtures = [
            {**f, "kickoff": datetime.fromisoformat(f["kickoff"])}
            for f in json.loads(Path(sys.argv[2]).read_text())
        ]
        assert len(fixtures) == 380
        con = duckdb.connect(sys.argv[3])
        inject(con, fixtures)
        con.close()
    else:
        raise SystemExit(f"unknown command {cmd!r} (parse|inject)")


if __name__ == "__main__":
    main()
