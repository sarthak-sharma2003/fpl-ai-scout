"""Provisional 26/27 GW1 draft from the REAL released fixture list, before the
FPL game launches.

Usage: provisional_2627.py <espn_fixtures.html> <real_db> <scratch_db>

Parses ESPN's 2026-27 fixture article (full 380 fixtures, date headers +
"Home v Away time" lines), fabricates the 2026-27 season in a scratch copy of
the DB — real opponents/calendar, 25/26 end prices as stand-ins, promoted
teams with synthetic codes (Dixon-Coles falls back to its relegation-zone
average for them) — and runs the full chain to a DO THIS sheet.

Provisional by nature: no summer transfers, no real prices, no promoted-team
players. Superseded the day the FPL API resets (live_gw overwrites 2026-27
wholesale). Delete this script after launch.
"""

import json
import re
import shutil
import sys
from datetime import datetime
from html import unescape
from pathlib import Path

from fplscout import db, pipeline
from fplscout.decide.optimizer import OptimizerInput, optimize
from fplscout.features.build import write_features
from fplscout.report.weekly import render_weekly

SEASON = "2026-27"

# ESPN's long names -> vaastav/FPL short names in our teams table
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
PROMOTED = {"Coventry", "Hull", "Ipswich"}


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
    # ESPN's article omits a couple of fixtures outright (378 lines at time of
    # writing — Arsenal v Everton and Man City v Aston Villa never appear). A
    # double round-robin makes omissions uniquely inferable: group the parsed
    # sequence greedily into matchweeks (new round when a team would repeat);
    # any 9-fixture round names the two absent teams, and the orientation is
    # whichever direction doesn't already exist elsewhere in the list.
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
        # pair up absent teams: the missing fixture is the (home, away) direction
        # that appears nowhere in the article while its reverse does
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
    assert sum(len(r) for r in rounds) == 380
    return [f for rnd in rounds for f in rnd]


def main() -> None:
    espn_html, src_db, scratch_db = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    fixtures = parse_espn(espn_html)
    print(f"parsed {len(fixtures)} fixtures, GW1 opens {fixtures[0]['kickoff']:%Y-%m-%d}")

    shutil.copyfile(src_db, scratch_db)
    con = db.connect(scratch_db)

    prev = {
        name: (team_id, code, strength) for name, team_id, code, strength in con.execute(
            "SELECT name, team_id, code, strength FROM teams WHERE season = '2025-26'"
        ).fetchall()
    }
    team_rows, name_to_id = [], {}
    next_id, next_code = 1, 90001
    for name in sorted({f["home"] for f in fixtures}):
        if name in prev:
            _, code, strength = prev[name]
        else:
            assert name in PROMOTED, f"unknown non-promoted team {name}"
            code, strength = next_code, 2  # weakest FPL strength band
            next_code += 1
        name_to_id[name] = next_id
        team_rows.append((SEASON, next_id, code, name, name[:3].upper(), strength))
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
        [(SEASON, gw, first_kick[gw], ) for gw in range(1, 39)],
    )
    # player universe: 25/26 players whose CLUB survived into 26/27, remapped to
    # the new team ids; last known 25/26 price as the stand-in now_cost
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

    n = write_features(con)
    season, gw = pipeline.latest_reference_point(con)
    print(f"features {n} rows; reference point {season} GW{gw}")
    assert (season, gw) == (SEASON, 1)

    models = pipeline.train_production(con, models_dir=scratch_db.parent / "models")
    proj = pipeline.generate_projections(con, models, season, gw)
    roster = pipeline.roster_snapshot(con, season, gw)
    total_ev = pipeline.total_ev_for_optimizer(con, models, season, gw, proj)
    opt_df = roster.merge(
        total_ev.rename("total_ev").reset_index().rename(columns={"index": "code"}),
        on="code", how="inner",
    ).merge(proj[["code", "ev_points", "q90_points"]], on="code", how="left")
    opt_df["cap_ev"] = 0.5 * opt_df["ev_points"] + 0.5 * opt_df["q90_points"]
    opt_df = opt_df.dropna(subset=["total_ev", "price", "position", "team_id"])
    opt_df["price"] = opt_df["price"].astype(int)

    result = optimize(
        OptimizerInput(
            projections=opt_df[["code", "position", "team_id", "price", "total_ev", "cap_ev"]],
            current_squad=set(), purchase_prices={}, bank=1000, free_transfers=1,
            chip_mode="wildcard",
        )
    )
    assert result.status == "Optimal", result.status
    con.execute(
        "INSERT INTO recommendations (season, gw, generated_at, squad, starting_xi, "
        "captain_code, vice_captain_code, transfers, hits, chip, confidence) "
        "VALUES (?, ?, now(), ?, ?, ?, ?, '[]', 0, 'wildcard', NULL)",
        [season, gw, json.dumps(sorted(result.squad)), json.dumps(sorted(result.starting_xi)),
         result.captain, result.vice_captain],
    )
    print()
    print("PROVISIONAL 26/27 GW1 DRAFT — real fixtures, stand-in prices,")
    print("no summer transfers. Superseded at FPL launch.")
    print()
    print(render_weekly(con, season, gw))
    con.close()


if __name__ == "__main__":
    main()
