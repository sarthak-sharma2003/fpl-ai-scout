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
            l.p_appearance, l.p_60_plus, l.p_clean_sheet, l.model_version
        FROM (SELECT DISTINCT code, position, team_id, price FROM (
                SELECT h.code, h.position, h.team_id, h.value AS price,
                       ROW_NUMBER() OVER (PARTITION BY h.code ORDER BY h.fixture_id) AS rn2
                FROM player_gw_history h WHERE h.season = ? AND h.gw = ?
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
    return {
        "code": int(row["code"]),
        "name": row["web_name"],
        "team": row.get("team_short"),
        "position": row["position"],
        "price": round(row["price"] / 10, 1) if pd.notna(row["price"]) else None,
        "ev": round(row["ev_points"], 2) if pd.notna(row["ev_points"]) else None,
    }


def _is_live(con: duckdb.DuckDBPyConnection, season: str, gw: int) -> bool:
    max_gw = con.execute(
        "SELECT MAX(event) FROM fixtures WHERE season = ?", [season]
    ).fetchone()[0]
    return max_gw is not None and max_gw > gw


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
    is_live = _is_live(con, season, gw)
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
            "gw": gw, "season": season, "is_live": is_live,
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
        {"name": r["web_name"], "team": r["team_short"], "ev": round(r["ev_points"], 2)}
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
        "gw": gw, "season": season, "is_live": is_live,
        "deadline": deadline_row[0].isoformat() if deadline_row and deadline_row[0] else None,
        "avg_points": avg_points,
        "our_points": round(float(our_points), 1) if pd.notna(our_points) else None,
        "overall_rank": None,
        "mini_league": None,
        "insight": {
            "text": (
                "Live recommendation for the upcoming gameweek."
                if is_live
                else f"Showing {season} GW{gw} (last completed gameweek) — 26/27 "
                "hasn't launched yet, so this is a demo projection, not a live entry."
            ),
            "transfer_summary": f"{hits} hit(s) taken" if hits else "No hits taken",
            "captain": captain_row["web_name"] if captain_row is not None else None,
        },
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
    """No future fixtures exist for a finished season (see module docstring) —
    shows the trailing `horizon` gameweeks' ticker instead, clearly flagged."""
    start_gw = max(1, gw - horizon + 1)
    fixtures = con.execute(
        "SELECT event AS gw, team_h, team_a, team_h_difficulty, team_a_difficulty "
        "FROM fixtures WHERE season = ? AND event BETWEEN ? AND ?",
        [season, start_gw, gw],
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
        for g in range(start_gw, gw + 1):
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
        "reference_gw": gw, "horizon": horizon, "is_live": _is_live(con, season, gw),
        "note": (
            "26/27 fixtures aren't published yet — showing the trailing "
            f"{horizon} gameweeks of {season} instead."
            if not _is_live(con, season, gw) else None
        ),
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
        "gw": gw, "is_live": _is_live(con, season, gw),
        "price_risers": _rows_to_cards(risers),
        "price_fallers": _rows_to_cards(fallers),
        "injury_news": [],
        "injury_news_note": (
            "Injury-news deltas require our own accumulated ep_next_archive "
            "history (see ingest/health.py) — not enough refreshes have run yet."
        ),
    }


def build_rules(rules_path: Path) -> list[dict]:
    data = yaml.safe_load(rules_path.read_text()) or {"rules": []}
    return [
        {
            "id": r["id"], "title": r["title"], "body": r["body"].strip(),
            "enabled": r.get("enabled", True),
        }
        for r in data.get("rules", [])
    ]


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
        out[int(r["code"])] = {
            "code": int(r["code"]), "name": r["web_name"], "position": r["position"],
            "team": r["team_short"],
            "ev_points": round(r["ev_points"], 2),
            "q10_points": round(r["q10_points"], 2) if pd.notna(r["q10_points"]) else None,
            "q90_points": round(r["q90_points"], 2) if pd.notna(r["q90_points"]) else None,
            "ev_minutes": round(r["ev_minutes"], 1) if pd.notna(r["ev_minutes"]) else None,
            "p_appearance": round(r["p_appearance"], 3) if pd.notna(r["p_appearance"]) else None,
            "p_60_plus": round(r["p_60_plus"], 3) if pd.notna(r["p_60_plus"]) else None,
            "p_clean_sheet": round(r["p_clean_sheet"], 3) if pd.notna(r["p_clean_sheet"]) else None,
            "model_version": r["model_version"],
        }
    return out


def publish_all(
    con: duckdb.DuckDBPyConnection,
    site_data_dir: Path,
    reports_dir: Path,
    rules_path: Path,
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

    return written
