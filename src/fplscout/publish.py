"""Static JSON export — plan §8's API contract rendered to files under site/data/,
for the free-tier fully-static GitHub Pages deploy (see README's static-site
pivot). Every function here mirrors one §8 endpoint's response shape as closely
as the data supports, so swapping in a real FastAPI backend later is meant to be
a drop-in: same JSON shape, different transport (`serve` stays local-dev-only).

Pre-26/27-launch limitation, carried through every file here: there is no live
"next gameweek" yet, so everything is generated "as of" the latest available
reference point (2025-26 GW38, the last completed gameweek in the data) and
every file carries `"is_live": false`. Once 26/27 launches and `project`/
`optimize` run against a genuine live gameweek with future fixtures, `is_live`
flips to true automatically — see pipeline.py's total_ev_for_optimizer branch,
which is the thing that actually changes behavior; this module just reports it.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd
import yaml

from fplscout import pipeline
from fplscout.decide.optimizer import DEFAULT_HIT_COST, top_alternative_moves


def _reference_frame(con: duckdb.DuckDBPyConnection, season: str, gw: int) -> pd.DataFrame:
    """One row per player with a projection at (season, gw): identity, price,
    team, and the latest model_version's EV/quantile/probability outputs."""
    # identity/price come from `features` (not player_gw_history) so this also
    # works for an UNPLAYED reference gameweek (upcoming-GW synthetic rows,
    # issue #5 / provisional 26/27); equivalent for played gameweeks since
    # features derive from history.
    return con.execute(
        """
        WITH latest AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY code ORDER BY generated_at DESC
            ) AS rn
            FROM projections
            WHERE season = ? AND gw = ?
        )
        SELECT
            r.code, p.web_name, r.position, r.team_id, t.short_name AS team_short,
            r.price, l.ev_points, l.q10_points, l.q90_points, l.ev_minutes,
            l.p_appearance, l.p_60_plus, l.p_clean_sheet, l.model_version,
            p.status, p.news, p.chance_of_playing_next_round, p.penalties_order
        FROM (SELECT DISTINCT code, position, team_id, price FROM (
                SELECT f.code, f.position, f.team_id, f.value AS price,
                       ROW_NUMBER() OVER (PARTITION BY f.code ORDER BY f.fixture_id) AS rn2
                FROM features f WHERE f.season = ? AND f.gw = ?
              ) WHERE rn2 = 1) r
        JOIN players p ON p.code = r.code
        LEFT JOIN teams t ON t.season = ? AND t.team_id = r.team_id
        LEFT JOIN latest l ON l.code = r.code AND l.rn = 1
        """,
        [season, gw, season, gw, season],
    ).df()


def _round_or_none(value, digits: int) -> float | None:
    """round(value, digits) unless it's NaN/None — FPL projection frames carry
    NaN for players with no valid projection, and JSON has no NaN."""
    return round(float(value), digits) if pd.notna(value) else None


def _player_card(row: pd.Series) -> dict:
    card = {
        "code": int(row["code"]),
        "name": row["web_name"],
        "team": row.get("team_short"),
        "position": row["position"],
        "price": round(row["price"] / 10, 1) if pd.notna(row["price"]) else None,
        "ev": round(row["ev_points"], 2) if pd.notna(row["ev_points"]) else None,
    }
    # availability/penalty flags, only when noteworthy (keeps cards lean)
    status = row.get("status")
    if pd.notna(status) and status != "a":
        card["flag"] = {
            "status": status,
            "news": row["news"] if pd.notna(row.get("news")) else None,
            "chance": (
                int(row["chance_of_playing_next_round"])
                if pd.notna(row.get("chance_of_playing_next_round")) else None
            ),
        }
    if pd.notna(row.get("penalties_order")) and row.get("penalties_order") == 1:
        card["pk"] = True
    return card


def _is_live(con: duckdb.DuckDBPyConnection, season: str, gw: int) -> bool:
    max_gw = con.execute(
        "SELECT MAX(event) FROM fixtures WHERE season = ?", [season]
    ).fetchone()[0]
    return max_gw is not None and max_gw > gw


def _season_state(con: duckdb.DuckDBPyConnection, season: str, gw: int) -> str:
    """'live'        — future fixtures AND real played rows: a season underway.
    'provisional' — future fixtures but ZERO played rows: the fabricated
                    pre-launch 26/27 (scripts/provisional_2627.py) — real
                    calendar, stand-in prices, no summer transfers.
    'demo'        — no future fixtures: idling on the last finished season."""
    if not _is_live(con, season, gw):
        return "demo"
    played = con.execute(
        "SELECT COUNT(*) FROM player_gw_history WHERE season = ?", [season]
    ).fetchone()[0]
    return "live" if played else "provisional"


STATE_TEXT = {
    "live": "Live recommendation for the upcoming gameweek.",
    "provisional": (
        "PROVISIONAL 2026-27 GW1 preview — real released fixture calendar, but "
        "stand-in end-of-25/26 prices, no summer transfers, and no promoted-team "
        "players. Regenerates with real data the day the FPL game launches."
    ),
}


def _confidence(ref: pd.DataFrame, starting_xi: set[int]) -> float:
    """0-100 display number, per plan §8's "document the formula, don't fake it".

    confidence = 100 / (1 + mean relative quantile spread across the starting
    XI), where relative spread = (q90 - q10) / max(ev, 1). Tighter quantile
    bands (the model is more sure) -> higher confidence; wide bands -> lower.
    A ratio-based decay, not a linear-then-clamp one: checked against real
    output first (relative spread ~1.0-1.5 is normal for FPL's high-variance
    scoring — a explosive haul and a blank are both plausible even for a
    good EV), so a naive `1 - spread` formula floored at 0 for every single
    player and never differentiated anything. This form stays in (0, 100]
    and produces a real spread across squads/gameweeks instead.
    """
    xi = ref[ref["code"].isin(starting_xi)].dropna(subset=["ev_points", "q10_points", "q90_points"])
    if len(xi) == 0:
        return 0.0
    spread = (xi["q90_points"] - xi["q10_points"]) / xi["ev_points"].clip(lower=1.0)
    return round(100.0 / (1.0 + spread.mean()), 1)


def build_dashboard(con: duckdb.DuckDBPyConnection, season: str, gw: int) -> dict:
    ref = _reference_frame(con, season, gw)
    state = _season_state(con, season, gw)
    is_live = state == "live"
    rec = con.execute(
        "SELECT * FROM recommendations WHERE season = ? AND gw = ? "
        "ORDER BY generated_at DESC LIMIT 1",
        [season, gw],
    ).df()

    avg_points = con.execute(
        "SELECT SUM(average_entry_score) FROM gameweeks WHERE season = ? AND event <= ?",
        [season, gw],
    ).fetchone()[0]
    deadline_row = con.execute(
        "SELECT deadline_time FROM gameweeks WHERE season = ? AND event = ?", [season, gw]
    ).fetchone()

    if len(rec) == 0:
        return {
            "gw": gw, "season": season, "is_live": is_live, "state": state,
            "deadline": deadline_row[0].isoformat() if deadline_row and deadline_row[0] else None,
            "avg_points": avg_points, "our_points": None, "overall_rank": None,
            "mini_league": None,
            "insight": {
                "text": "No recommendation generated yet — run `fplscout optimize`.",
                "transfer_summary": None, "captain": None,
            },
            "bench_order": [], "pitch": {"gk": [], "def": [], "mid": [], "fwd": []},
        }

    squad = set(json.loads(rec["squad"][0]))
    xi = set(json.loads(rec["starting_xi"][0]))
    captain_code = rec["captain_code"][0]
    ref_by_code = ref.set_index("code", drop=False)

    bench = ref_by_code[ref_by_code["code"].isin(squad - xi)]
    bench_gk = bench[bench["position"] == "GKP"]
    bench_outfield = bench[bench["position"] != "GKP"].sort_values("ev_points", ascending=False)
    bench_order = [
        _player_card(r)
        for _, r in pd.concat([bench_outfield, bench_gk]).iterrows()
        if pd.notna(r["ev_points"])
    ]

    pitch = {"gk": [], "def": [], "mid": [], "fwd": []}
    pos_key = {"GKP": "gk", "DEF": "def", "MID": "mid", "FWD": "fwd"}
    for _, r in ref_by_code[ref_by_code["code"].isin(xi)].iterrows():
        pitch[pos_key[r["position"]]].append(_player_card(r))

    captain_row = ref_by_code.loc[captain_code] if captain_code in ref_by_code.index else None
    our_points = ref_by_code[ref_by_code["code"].isin(xi)]["ev_points"].sum()
    if captain_row is not None and pd.notna(captain_row["ev_points"]):
        our_points += captain_row["ev_points"]  # captain's extra multiplier share

    hits = rec["hits"][0]
    return {
        "gw": gw, "season": season, "is_live": is_live, "state": state,
        "deadline": deadline_row[0].isoformat() if deadline_row and deadline_row[0] else None,
        "avg_points": avg_points,
        "our_points": round(float(our_points), 1) if pd.notna(our_points) else None,
        "overall_rank": None,
        "mini_league": None,
        "insight": {
            "text": STATE_TEXT.get(
                state,
                f"Showing {season} GW{gw} (last completed gameweek) — 26/27 "
                "hasn't launched yet, so this is a demo projection, not a live entry.",
            ),
            "transfer_summary": f"{hits} hit(s) taken" if hits else "No hits taken",
            "captain": captain_row["web_name"] if captain_row is not None else None,
        },
        "captain_code": int(captain_code) if pd.notna(captain_code) else None,
        "vice_captain_code": (
            int(rec["vice_captain_code"][0]) if pd.notna(rec["vice_captain_code"][0]) else None
        ),
        "bench_order": bench_order,
        "pitch": pitch,
    }


def build_transfers(con: duckdb.DuckDBPyConnection, season: str, gw: int) -> dict:
    ref = _reference_frame(con, season, gw)
    rec = con.execute(
        "SELECT * FROM recommendations WHERE season = ? AND gw = ? "
        "ORDER BY generated_at DESC LIMIT 1",
        [season, gw],
    ).df()
    if len(rec) == 0:
        return {
            "gw": gw, "confidence": 0.0, "bank": None, "free_transfers": None,
            "chip_advice": None, "moves": [], "alternatives": [],
        }

    squad = set(json.loads(rec["squad"][0]))
    xi = set(json.loads(rec["starting_xi"][0]))
    proj_for_optimizer = ref[["code", "position", "team_id", "price"]].copy()
    proj_for_optimizer["total_ev"] = ref["ev_points"]
    proj_for_optimizer = proj_for_optimizer.dropna(subset=["total_ev"])

    # "Alternatives" here means: treating the recommended squad as if it were
    # already yours, what are the top single-swap upgrades available? A real
    # transfer-in/out comparison needs a genuine prior squad (squad_state),
    # which doesn't exist pre-26/27-launch — this is the closest honest
    # approximation using only real projections, not fabricated deltas.
    purchase_prices = {
        int(c): int(p)
        for c, p in zip(ref["code"], ref["price"], strict=True)
        if c in squad
    }
    moves = top_alternative_moves(
        proj_for_optimizer, current_squad=squad, purchase_prices=purchase_prices,
        bank=0, free_transfers=1, hit_cost=DEFAULT_HIT_COST, n=5,
    )
    ref_by_code = ref.set_index("code", drop=False)
    alternatives = []
    for m in moves:
        out_row, in_row = ref_by_code.loc[m.out_code], ref_by_code.loc[m.in_code]
        alternatives.append({
            "out": _player_card(out_row),
            "in": _player_card(in_row),
            "compare": {
                "position": m.position,
                "out_ev": _round_or_none(out_row["ev_points"], 2),
                "in_ev": _round_or_none(in_row["ev_points"], 2),
            },
            "net_ev": round(m.net_ev, 2),
        })

    return {
        "gw": gw,
        "confidence": _confidence(ref, xi),
        "bank": None,
        "free_transfers": None,
        "chip_advice": (
            {"chip": rec["chip"][0], "gw": gw, "ev": None} if rec["chip"][0] else None
        ),
        "moves": [],
        "alternatives": alternatives,
    }


def build_fixtures(con: duckdb.DuckDBPyConnection, season: str, gw: int, horizon: int = 8) -> dict:
    """Fixture ticker. With future fixtures (live/provisional season): the
    UPCOMING `horizon` gameweeks from `gw`. Finished season (demo): the
    trailing window instead, clearly flagged."""
    state = _season_state(con, season, gw)
    if state == "demo":
        start_gw, end_gw = max(1, gw - horizon + 1), gw
    else:
        max_gw = con.execute(
            "SELECT MAX(event) FROM fixtures WHERE season = ?", [season]
        ).fetchone()[0]
        start_gw, end_gw = gw, min(max_gw, gw + horizon - 1)
    fixtures = con.execute(
        "SELECT event AS gw, team_h, team_a, team_h_difficulty, team_a_difficulty "
        "FROM fixtures WHERE season = ? AND event BETWEEN ? AND ?",
        [season, start_gw, end_gw],
    ).df()
    teams = con.execute(
        "SELECT team_id, code, name, short_name FROM teams WHERE season = ?", [season]
    ).df()

    cols = ["gw", "team_id", "opponent_id", "fdr"]
    home = fixtures.rename(
        columns={"team_h": "team_id", "team_a": "opponent_id", "team_h_difficulty": "fdr"}
    )[cols].assign(was_home=True)
    away = fixtures.rename(
        columns={"team_a": "team_id", "team_h": "opponent_id", "team_a_difficulty": "fdr"}
    )[cols].assign(was_home=False)
    long = pd.concat([home, away], ignore_index=True)
    dgw_counts = long.groupby(["gw", "team_id"]).size().reset_index(name="n")
    long = long.merge(dgw_counts, on=["gw", "team_id"], how="left")
    short_by_id = dict(zip(teams["team_id"], teams["short_name"], strict=False))

    out = []
    for _, team in teams.iterrows():
        team_fixtures = long[long["team_id"] == team["team_id"]].sort_values("gw")
        ticker = []
        for g in range(start_gw, end_gw + 1):
            rows = team_fixtures[team_fixtures["gw"] == g]
            if len(rows) == 0:
                ticker.append({"gw": g, "is_bgw": True})
                continue
            for _, r in rows.iterrows():
                ticker.append({
                    "gw": g,
                    "opponent": short_by_id.get(r["opponent_id"], "?"),
                    "was_home": bool(r["was_home"]),
                    "fdr": int(r["fdr"]) if pd.notna(r["fdr"]) else None,
                    "is_dgw": bool(r["n"] >= 2),
                })
        out.append({
            "code": int(team["code"]), "name": team["name"], "short_name": team["short_name"],
            "ticker": ticker,
        })

    return {
        "reference_gw": gw, "horizon": horizon, "is_live": state == "live",
        "state": state,
        "note": {
            "demo": (
                "26/27 fixtures aren't loaded yet — showing the trailing "
                f"{horizon} gameweeks of {season} instead."
            ),
            "provisional": (
                "Real released 2026-27 calendar (provisional pre-launch preview)."
            ),
            "live": None,
        }[state],
        "teams": out,
    }


def build_signals(con: duckdb.DuckDBPyConnection, season: str, gw: int) -> dict:
    """Price-change risk and ownership swings from real historical transfer
    data at (season, gw). Injury-news deltas need our own live ep_next_archive
    accumulating over successive refreshes (see ingest/health.py) — there isn't
    enough history yet, so that section is empty with a note rather than faked.
    """
    rows = con.execute(
        """
        SELECT h.code, p.web_name, h.value AS price, h.transfers_in, h.transfers_out,
               h.transfers_balance, h.selected
        FROM player_gw_history h JOIN players p ON p.code = h.code
        WHERE h.season = ? AND h.gw = ?
        QUALIFY ROW_NUMBER() OVER (PARTITION BY h.code ORDER BY h.fixture_id) = 1
        """,
        [season, gw],
    ).df()
    rows = rows.dropna(subset=["transfers_balance"])
    risers = rows.sort_values("transfers_balance", ascending=False).head(10)
    fallers = rows.sort_values("transfers_balance", ascending=True).head(10)

    def _rows_to_cards(df: pd.DataFrame) -> list[dict]:
        return [
            {
                "code": int(r["code"]), "name": r["web_name"],
                "price": round(r["price"] / 10, 1) if pd.notna(r["price"]) else None,
                "transfers_balance": int(r["transfers_balance"]),
                "selected_by": int(r["selected"]) if pd.notna(r["selected"]) else None,
            }
            for _, r in df.iterrows()
        ]

    return {
        "gw": gw, "is_live": _season_state(con, season, gw) == "live",
        "price_risers": _rows_to_cards(risers),
        "price_fallers": _rows_to_cards(fallers),
        "injury_news": [],
        "injury_news_note": (
            "Injury-news deltas require our own accumulated ep_next_archive "
            "history (see ingest/health.py) — not enough refreshes have run yet."
        ),
    }


CHIP_GUIDANCE = {
    "wildcard": (
        "Rebuilds the whole squad; value scales with how many dead spots you have. "
        "Elite pattern: first wildcard GW4-9 once real form separates from summer "
        "prices, second around the season's fixture swings. Don't burn it to fix "
        "one player — that's what transfers are for."
    ),
    "freehit": (
        "One-week loan squad. Save it for the biggest blank gameweek (most teams "
        "without a fixture) or a freak double you can't otherwise exploit; typical "
        "value 10-25 pts over a patched squad. Firing it on an ordinary week wastes "
        "almost all of that."
    ),
    "bboost": (
        "Scores your bench for one week. Play it on a double gameweek where all "
        "four bench players have two fixtures — a well-set BB returns 15-25 pts. "
        "The optimizer's bench EV below is the honest this-week number; compare it "
        "against that 15+ bar before burning the chip."
    ),
    "3xc": (
        "One extra captain multiple. Best case: a premium captain with two friendly "
        "fixtures in a double gameweek (uplift = one full extra captain score). The "
        "this-week number below is exactly what you'd gain over a normal armband."
    ),
    "manager": (
        "Assistant-manager chip (if this season runs it): follow the API window; "
        "value hinges on that season's specific scoring rules — check before using."
    ),
}


def build_chips(
    con: duckdb.DuckDBPyConnection,
    season: str,
    gw: int,
    our_entry_id: int | None = None,
    horizon: int = 12,
) -> dict:
    """Chip windows (live from the API, never hardcoded), our usage state, the
    honest this-week observables per chip, and the DGW/BGW radar the timing
    decisions actually hinge on. Wildcard/free-hit deltas need an in-season
    prior squad (squad_state) — until then they're presented as guidance only."""
    windows = con.execute(
        "SELECT chip_id, chip, number, start_event, stop_event FROM chip_windows "
        "WHERE season = ? ORDER BY chip, start_event",
        [season],
    ).df()

    used_by_window: dict[int, int] = {}
    if our_entry_id is not None and len(windows):
        used = con.execute(
            "SELECT gw, active_chip FROM rival_gw "
            "WHERE season = ? AND entry_id = ? AND active_chip IS NOT NULL",
            [season, our_entry_id],
        ).fetchall()
        for used_gw, chip_name in used:
            match = windows[
                (windows["chip"] == chip_name)
                & (windows["start_event"] <= used_gw)
                & (windows["stop_event"] >= used_gw)
            ]
            if len(match):
                used_by_window[int(match["chip_id"].iloc[0])] = used_gw

    # this-week observables from the latest recommendation
    ref = _reference_frame(con, season, gw)
    ref_by_code = ref.set_index("code", drop=False)
    rec = con.execute(
        "SELECT squad, starting_xi, captain_code FROM recommendations "
        "WHERE season = ? AND gw = ? ORDER BY generated_at DESC LIMIT 1",
        [season, gw],
    ).fetchone()
    bench_ev = captain_card = None
    if rec is not None:
        squad, xi, captain = set(json.loads(rec[0])), set(json.loads(rec[1])), rec[2]
        bench = ref_by_code[ref_by_code["code"].isin(squad - xi)]
        if bench["ev_points"].notna().any():
            bench_ev = round(float(bench["ev_points"].sum(skipna=True)), 1)
        if captain is not None and captain in ref_by_code.index:
            cap = ref_by_code.loc[captain]
            captain_card = {
                "name": cap["web_name"],
                "extra_ev": _round_or_none(cap["ev_points"], 1),
                "q90": _round_or_none(cap["q90_points"], 1),
            }

    this_week = {
        "bboost": {"bench_ev": bench_ev},
        "3xc": captain_card,
        "wildcard": None,  # needs an in-season prior squad to price a delta
        "freehit": None,
    }

    chips_out = []
    for _, w in windows.iterrows():
        chip_id = int(w["chip_id"])
        chips_out.append({
            "chip": w["chip"],
            "chip_id": chip_id,
            "start_gw": int(w["start_event"]) if pd.notna(w["start_event"]) else None,
            "stop_gw": int(w["stop_event"]) if pd.notna(w["stop_event"]) else None,
            "available": chip_id not in used_by_window,
            "used_gw": used_by_window.get(chip_id),
            "active_now": (
                pd.notna(w["start_event"]) and pd.notna(w["stop_event"])
                and int(w["start_event"]) <= gw <= int(w["stop_event"])
            ),
            "this_week": this_week.get(w["chip"]),
            "guidance": CHIP_GUIDANCE.get(w["chip"], ""),
        })

    # DGW/BGW radar over the upcoming horizon — the thing chip timing hinges on
    all_teams = con.execute(
        "SELECT team_id, short_name FROM teams WHERE season = ?", [season]
    ).df()
    short_by_id = dict(zip(all_teams["team_id"], all_teams["short_name"], strict=False))
    counts = con.execute(
        """
        SELECT event AS gw, team_id, COUNT(*) AS n FROM (
            SELECT event, team_h AS team_id FROM fixtures WHERE season = ?
            UNION ALL
            SELECT event, team_a AS team_id FROM fixtures WHERE season = ?
        ) GROUP BY event, team_id
        """,
        [season, season],
    ).df()
    radar = []
    for g in range(gw, gw + horizon):
        gw_counts = counts[counts["gw"] == g]
        if len(gw_counts) == 0:
            continue  # beyond the loaded calendar
        dgw = sorted(
            short_by_id.get(t, str(t))
            for t in gw_counts.loc[gw_counts["n"] >= 2, "team_id"]
        )
        bgw = sorted(
            short_by_id.get(t, str(t))
            for t in set(short_by_id) - set(gw_counts["team_id"])
        )
        if dgw or bgw:
            radar.append({"gw": g, "dgw_teams": dgw, "bgw_teams": bgw})

    return {
        "season": season,
        "reference_gw": gw,
        "configured": bool(len(windows)),
        "note": (
            None if len(windows) else
            "Chip windows sync from the live API at refresh — populated once the "
            "season's bootstrap is ingested."
        ),
        "chips": chips_out,
        "dgw_bgw_radar": radar,
        "radar_note": (
            "Doubles/blanks emerge from postponements during the season — an empty "
            "radar early on is normal. Chip weeks are usually decided by this table."
        ),
    }


def build_league(
    con: duckdb.DuckDBPyConnection, season: str, gw: int, our_entry_id: int | None = None
) -> dict:
    """Mini-league rival intel: standings, each rival's squad scored with OUR
    model's EV for the upcoming reference gw, chip usage, and the coverage/
    differential picture vs. our squad. Approximation stated in-line: rival
    squads are their last post-deadline picks — their upcoming transfers are
    unknowable pre-deadline."""
    standings = con.execute(
        "SELECT * FROM league_standings ORDER BY rank"
    ).df()
    if len(standings) == 0:
        return {
            "configured": False,
            "note": (
                "No mini-league synced. Set mini_league_id in config/settings.yaml "
                "(the number in the league's FPL URL) and run `fplscout refresh`."
            ),
        }

    picks_gw_row = con.execute(
        "SELECT MAX(gw) FROM rival_picks WHERE season = ?", [season]
    ).fetchone()
    picks_gw = picks_gw_row[0] if picks_gw_row else None

    ev = con.execute(
        """
        WITH latest AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY code ORDER BY generated_at DESC) AS rn
            FROM projections WHERE season = ? AND gw = ?
        )
        SELECT l.code, l.ev_points, p.web_name, ps.position, ps.team_id, ps.value,
               t.short_name AS team_short
        FROM latest l
        JOIN players p ON p.code = l.code
        LEFT JOIN (
            SELECT DISTINCT code, position, team_id, value FROM (
                SELECT code, position, team_id, value,
                       ROW_NUMBER() OVER (PARTITION BY code ORDER BY fixture_id) AS rn2
                FROM features WHERE season = ? AND gw = ?
            ) WHERE rn2 = 1
        ) ps ON ps.code = l.code
        LEFT JOIN teams t ON t.season = ? AND t.team_id = ps.team_id
        WHERE l.rn = 1
        """,
        [season, gw, season, gw, season],
    ).df().set_index("code", drop=False)

    def card(code: int) -> dict:
        if code not in ev.index:
            return {"code": int(code) if pd.notna(code) else None, "name": None,
                    "team": None, "position": None, "price": None, "ev": None}
        r = ev.loc[code]
        return {
            "code": int(code), "name": r["web_name"], "team": r["team_short"],
            "position": r["position"],
            "price": round(r["value"] / 10, 1) if pd.notna(r["value"]) else None,
            "ev": _round_or_none(r["ev_points"], 2),
        }

    chips = con.execute(
        "SELECT entry_id, gw, active_chip FROM rival_gw "
        "WHERE season = ? AND active_chip IS NOT NULL ORDER BY gw",
        [season],
    ).df()
    latest_state = con.execute(
        "SELECT entry_id, bank, team_value FROM rival_gw WHERE season = ? AND gw = "
        "(SELECT MAX(gw) FROM rival_gw WHERE season = ?)",
        [season, season],
    ).df().set_index("entry_id") if picks_gw else pd.DataFrame()

    picks = con.execute(
        "SELECT entry_id, code, multiplier, is_captain FROM rival_picks "
        "WHERE season = ? AND gw = ?",
        [season, picks_gw],
    ).df() if picks_gw else pd.DataFrame(columns=["entry_id", "code", "multiplier", "is_captain"])

    # our squad: real synced picks when our entry is in the league; otherwise the
    # latest recommendation (pre-launch preview / not-yet-configured fallback)
    our_codes: set[int] = set()
    ours_source = None
    if our_entry_id is not None and picks_gw and (picks["entry_id"] == our_entry_id).any():
        our_codes = set(picks.loc[picks["entry_id"] == our_entry_id, "code"].dropna().astype(int))
        ours_source = "synced picks"
    else:
        rec = con.execute(
            "SELECT squad FROM recommendations WHERE season = ? AND gw = ? "
            "ORDER BY generated_at DESC LIMIT 1",
            [season, gw],
        ).fetchone()
        if rec:
            our_codes = set(json.loads(rec[0]))
            ours_source = "latest recommendation"

    out_standings = []
    for _, s in standings.iterrows():
        entry_id = int(s["entry_id"])
        entry_picks = picks[picks["entry_id"] == entry_id] if picks_gw else pd.DataFrame()
        squad_cards, captain_card, next_ev = [], None, None
        if len(entry_picks):
            next_ev = 0.0
            for _, p in entry_picks.iterrows():
                c = card(p["code"]) if pd.notna(p["code"]) else card(-1)
                c["multiplier"] = int(p["multiplier"])
                c["is_captain"] = bool(p["is_captain"])
                squad_cards.append(c)
                if p["is_captain"]:
                    captain_card = c
                if p["multiplier"] >= 1 and c["ev"] is not None:
                    next_ev += c["ev"] * (2 if p["is_captain"] else 1)
            next_ev = round(next_ev, 1)
        entry_chips = chips[chips["entry_id"] == entry_id]
        out_standings.append({
            "entry_id": entry_id,
            "entry_name": s["entry_name"],
            "player_name": s["player_name"],
            "rank": int(s["rank"]) if pd.notna(s["rank"]) else None,
            "last_rank": int(s["last_rank"]) if pd.notna(s["last_rank"]) else None,
            "total": int(s["total"]) if pd.notna(s["total"]) else None,
            "event_total": int(s["event_total"]) if pd.notna(s["event_total"]) else None,
            "is_us": our_entry_id is not None and entry_id == our_entry_id,
            "chips_used": [
                {"chip": c["active_chip"], "gw": int(c["gw"])} for _, c in entry_chips.iterrows()
            ],
            "bank": (
                round(latest_state.loc[entry_id, "bank"] / 10, 1)
                if len(latest_state) and entry_id in latest_state.index
                and pd.notna(latest_state.loc[entry_id, "bank"]) else None
            ),
            "team_value": (
                round(latest_state.loc[entry_id, "team_value"] / 10, 1)
                if len(latest_state) and entry_id in latest_state.index
                and pd.notna(latest_state.loc[entry_id, "team_value"]) else None
            ),
            "projected_next_ev": next_ev,
            "captain": captain_card,
            "squad": squad_cards,
        })

    ownership, differentials = [], {"our_edges": [], "threats": []}
    if picks_gw:
        rival_picks_only = picks[picks["entry_id"] != our_entry_id] if our_entry_id else picks
        n_rivals = rival_picks_only["entry_id"].nunique()
        name_by_entry = dict(
            zip(standings["entry_id"].astype(int), standings["entry_name"], strict=False)
        )
        grouped = rival_picks_only.dropna(subset=["code"]).groupby("code")
        for code, g in grouped:
            code = int(code)
            c = card(code)
            c.update({
                "owned_by": sorted(name_by_entry.get(int(e), str(e)) for e in g["entry_id"]),
                "n_owned": int(g["entry_id"].nunique()),
                "n_captained": int(g["is_captain"].sum()),
                "we_own": code in our_codes,
            })
            ownership.append(c)
        ownership.sort(key=lambda c: (-c["n_owned"], -(c["ev"] or 0)))

        owned_count = {c["code"]: c["n_owned"] for c in ownership}
        our_cards = [card(code) for code in our_codes]
        differentials["our_edges"] = sorted(
            [
                {**c, "n_owned": owned_count.get(c["code"], 0)}
                for c in our_cards
                if owned_count.get(c["code"], 0) <= max(1, n_rivals // 4)
            ],
            key=lambda c: -(c["ev"] or 0),
        )[:8]
        differentials["threats"] = [
            c for c in ownership
            if not c["we_own"] and c["n_owned"] >= 2
        ][:8]

    return {
        "configured": True,
        "note": (
            None if picks_gw else
            "League synced but no post-deadline picks exist yet — squads, ownership "
            "and differentials appear after GW1's deadline."
        ),
        "league": {
            "id": int(standings["league_id"].iloc[0]),
            "name": standings["league_name"].iloc[0],
            "fetched_at": str(standings["fetched_at"].iloc[0]),
        },
        "our_entry_id": our_entry_id,
        "our_squad_source": ours_source,
        "picks_gw": picks_gw,
        "projection_gw": gw,
        "standings": out_standings,
        "ownership": ownership[:60],
        "differentials": differentials,
    }


def build_rules(rules_path: Path) -> list[dict]:
    data = yaml.safe_load(rules_path.read_text()) or {"rules": []}
    out = []
    for r in data.get("rules", []):
        item = {
            "id": r["id"], "title": r["title"], "body": r["body"].strip(),
            "enabled": r.get("enabled", True),
        }
        for optional in ("source", "kind"):
            if r.get(optional):
                item[optional] = r[optional]
        out.append(item)
    return out


def build_analytics(reports_dir: Path, model_version: str | None) -> dict:
    def _load(name: str) -> dict | None:
        path = reports_dir / name
        if not path.exists():
            return None
        return json.loads(path.read_text())

    return {
        "model_version": model_version,
        "validation": _load("phase3_validation_latest.json"),
        "backtest": _load("phase6_backtest_latest.json"),
    }


def build_player_projections(ref: pd.DataFrame) -> dict[int, dict]:
    """code -> per-GW EV breakdown, the static equivalent of
    GET /api/players/{id}/projection."""
    out = {}
    for _, r in ref.iterrows():
        if pd.isna(r["ev_points"]):
            continue
        entry = {
            "code": int(r["code"]), "name": r["web_name"], "position": r["position"],
            "team": r["team_short"],
            "price": round(r["price"] / 10, 1) if pd.notna(r["price"]) else None,
            "ev_points": round(r["ev_points"], 2),
            "q10_points": round(r["q10_points"], 2) if pd.notna(r["q10_points"]) else None,
            "q90_points": round(r["q90_points"], 2) if pd.notna(r["q90_points"]) else None,
            "ev_minutes": round(r["ev_minutes"], 1) if pd.notna(r["ev_minutes"]) else None,
            "p_appearance": round(r["p_appearance"], 3) if pd.notna(r["p_appearance"]) else None,
            "p_60_plus": round(r["p_60_plus"], 3) if pd.notna(r["p_60_plus"]) else None,
            "p_clean_sheet": round(r["p_clean_sheet"], 3) if pd.notna(r["p_clean_sheet"]) else None,
            "model_version": r["model_version"],
        }
        if pd.notna(r.get("status")) and r["status"] != "a":
            entry["flag"] = {
                "status": r["status"],
                "news": r["news"] if pd.notna(r.get("news")) else None,
                "chance": (
                    int(r["chance_of_playing_next_round"])
                    if pd.notna(r.get("chance_of_playing_next_round")) else None
                ),
            }
        if pd.notna(r.get("penalties_order")) and r["penalties_order"] == 1:
            entry["pk"] = True
        out[int(r["code"])] = entry
    return out


def publish_all(
    con: duckdb.DuckDBPyConnection,
    site_data_dir: Path,
    reports_dir: Path,
    rules_path: Path,
    our_entry_id: int | None = None,
) -> dict[str, int]:
    """Writes every §8-shaped JSON file to site_data_dir. Returns a summary
    dict of {filename: bytes_written} for the CLI to echo."""
    season, gw = pipeline.latest_reference_point(con)
    ref = _reference_frame(con, season, gw)
    versions = ref["model_version"].dropna()
    model_version = versions.iloc[0] if len(versions) else None

    site_data_dir.mkdir(parents=True, exist_ok=True)
    players_dir = site_data_dir / "players"
    players_dir.mkdir(parents=True, exist_ok=True)

    files: dict[str, object] = {
        "dashboard.json": build_dashboard(con, season, gw),
        "transfers.json": build_transfers(con, season, gw),
        "fixtures.json": build_fixtures(con, season, gw),
        "signals.json": build_signals(con, season, gw),
        "chips.json": build_chips(con, season, gw, our_entry_id=our_entry_id),
        "league.json": build_league(con, season, gw, our_entry_id=our_entry_id),
        "rules.json": build_rules(rules_path),
        "analytics.json": build_analytics(reports_dir, model_version),
    }

    written = {}
    for name, payload in files.items():
        path = site_data_dir / name
        text = json.dumps(payload, indent=2, default=str)
        path.write_text(text)
        written[name] = len(text)

    player_projections = build_player_projections(ref)
    for code, payload in player_projections.items():
        text = json.dumps(payload, indent=2)
        (players_dir / f"{code}.json").write_text(text)
    written["players/*.json"] = len(player_projections)

    # the whole table in one file for the sortable player-explorer page
    table = sorted(
        player_projections.values(), key=lambda p: -(p["ev_points"] or 0)
    )
    text = json.dumps({"season": season, "gw": gw, "players": table}, indent=2)
    (site_data_dir / "projections.json").write_text(text)
    written["projections.json"] = len(text)

    return written
