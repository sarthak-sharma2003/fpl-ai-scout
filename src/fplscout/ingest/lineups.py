"""Predicted line-ups (RotoWire, via pabloroldan98/knapsack-football-formations).

Why: the minutes model knows a player's history and FPL's injury flags, but
not "the manager is resting him this week". Measured on 2025-26 GW22-38 and
2026-27 GW1-5 (docs/lineups_fpl_2026-10.md, leak-free point-in-time
snapshots): adding RotoWire's predicted XI to our P(60+) cut start log-loss
~8% (16% early season), better in 22/22 gameweeks; a regular our model
expects to start but RotoWire leaves out started 51% of the time and scored
1.3 points less. Applied at inference time only (no history before 2025-26
to train on); every fetched snapshot is archived in `lineup_snapshots` so it
can become a training feature once deep enough.

Guards (from the analysis): a team's rows are used only if the file was
committed AFTER that team's previous Premier League kickoff + 2h15 (else it
still describes the last match), within 48h of the deadline, and never with
"confirmed" (1.0) values. "Not listed" is only informative when the team's
predicted XI matched in full (11 players).
"""

from __future__ import annotations

import difflib
import json
import os
import re
import unicodedata
from datetime import UTC, datetime, timedelta

import duckdb
import httpx
import numpy as np
import pandas as pd

from fplscout import db

REPO = "pabloroldan98/knapsack-football-formations"
FILE = "json_files/rotowire_premier_players_start_probabilities.json"
RAW = f"https://raw.githubusercontent.com/{REPO}/master/{FILE}"
COMMITS = f"https://api.github.com/repos/{REPO}/commits?path={FILE}&per_page=1"
MAX_AGE = timedelta(hours=48)
AFTER_KICKOFF = timedelta(hours=2, minutes=15)
LEVELS = (0.8, 0.5, 0.2, 0.0)
XI_LEVELS = (0.8, 0.5)
MATCH_THRESHOLD = 0.80

TEAM_ALIAS = {
    "AFC Bournemouth": "Bournemouth", "Brentford FC": "Brentford",
    "Brighton & Hove Albion": "Brighton", "Coventry": "Coventry City",
    "Hull": "Hull City", "Ipswich": "Ipswich Town", "Leeds United": "Leeds",
    "Manchester City": "Man City", "Manchester United": "Man Utd",
    "Newcastle United": "Newcastle", "Nottingham Forest": "Nott'm Forest",
    "Tottenham Hotspur": "Spurs", "Tottenham": "Spurs", "West Ham United": "West Ham",
    "Wolverhampton": "Wolves", "Wolverhampton Wanderers": "Wolves",
}
# normalized source name -> normalized FPL name, found by inspecting misses
PLAYER_ALIAS = {
    "v nilsson lindeloef": "victor lindelof", "yeremi pino": "yeremy pino",
    "magalhaes": "gabriel magalhaes", "valentino livramento": "tino livramento",
    "edmond paris maghoma": "paris maghoma", "issahaku fatawu": "abdul fatawu",
    "eli kroupi": "junior kroupi", "eli junior kroupi": "junior kroupi",
}

UPSERT = (
    "INSERT OR REPLACE INTO lineup_snapshots "
    "(season, gw, code, team_id, source, level, committed_at, fetched_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)


def norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    for a, b in (("ø", "o"), ("ß", "ss"), ("æ", "ae"), ("ł", "l"), ("đ", "d")):
        s = s.replace(a, b)
    s = re.sub(r"[\-\.'’`]", " ", s)
    s = re.sub(r"[^a-z ]", "", s)
    return " ".join(s.split())


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _token_set(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    inter = " ".join(sorted(ta & tb))
    s1 = (inter + " " + " ".join(sorted(ta - tb))).strip()
    s2 = (inter + " " + " ".join(sorted(tb - ta))).strip()
    if inter:
        return max(_ratio(inter, s1), _ratio(inter, s2), _ratio(s1, s2))
    return _ratio(s1, s2)


def score(src: str, cand: dict) -> float:
    """Similarity of a normalized source name to one FPL player (normalized
    first/second/web/full). Same rules as the validated offline matcher."""
    st, full = src.split(), cand["full"]
    ft = full.split()
    if len(st) >= 2 and len(st[0]) == 1:            # "m fernandez"
        rest = " ".join(st[1:])
        if cand["first"][:1] == st[0] and (
            rest in (cand["second"], cand["web"]) or set(rest.split()) <= set(ft)
        ):
            return 1.0
    sc = max(_token_set(src, full), _token_set(src, cand["web"]) if cand["web"] else 0.0,
             _ratio(src, cand["web"]), _ratio(src, full))
    if len(st) == 1 and src != cand["web"] and src not in ft:
        sc = min(sc, _ratio(src, cand["web"]))     # lone short token must hit web_name
    if len(st) >= 2 and len(st[0]) > 1 and st[0] not in ft:
        firsts = cand["first"].split()               # "santi bueno" vs Hugo Bueno
        if firsts and all(f[:1] != st[0][:1] for f in firsts) and st[0] not in cand["web"].split():
            sc = min(sc, 0.7)
    return sc


def match_team(names: dict[str, float], roster: pd.DataFrame) -> dict[int, float]:
    """{source player name: level} -> {code: level}, one-to-one, greedy by score."""
    cands = {
        int(r.code): {"first": norm(r.first_name), "second": norm(r.second_name),
                      "web": norm(r.web_name),
                      "full": f"{norm(r.first_name)} {norm(r.second_name)}".strip()}
        for r in roster.itertuples()
    }
    pairs = []
    for name, level in names.items():
        nn = norm(name)
        nn = PLAYER_ALIAS.get(nn, nn)
        for code, c in cands.items():
            pairs.append((score(nn, c), _ratio(nn, c["full"]), name, code, level))
    pairs.sort(key=lambda p: (-p[0], -p[1]))
    used_n, used_c, out = set(), set(), {}
    for sc, _tb, name, code, level in pairs:
        if sc < MATCH_THRESHOLD:
            break
        if name in used_n or code in used_c:
            continue
        used_n.add(name)
        used_c.add(code)
        out[code] = float(level)
    return out


def _get(url: str, headers: dict | None = None) -> bytes:
    with httpx.Client(timeout=30.0, follow_redirects=True,
                      headers={"User-Agent": "fpl-ai-scout/1.0", **(headers or {})}) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.content


def _commit_time() -> datetime:
    headers = {}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    info = json.loads(_get(COMMITS, headers))
    return datetime.fromisoformat(info[0]["commit"]["committer"]["date"].replace("Z", "+00:00"))


def sync_rotowire(con: duckdb.DuckDBPyConnection, season: str, gw: int,
                  deadline: datetime, now: datetime | None = None,
                  data: dict | None = None, committed_at: datetime | None = None) -> dict:
    """Fetch, guard, match and archive the RotoWire file for decision gameweek
    `gw`. Returns a summary. Never raises for a source problem -- a missing
    overlay just means the plain model."""
    now = now or datetime.now(UTC)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    if now >= deadline:
        return {"note": "deadline passed"}
    try:
        committed_at = committed_at or _commit_time()
        data = data if data is not None else json.loads(_get(RAW))
    except Exception as e:  # noqa: BLE001
        return {"note": f"fetch failed: {e}"[:200]}
    if deadline - committed_at > MAX_AGE:
        return {"note": f"snapshot {committed_at:%Y-%m-%d %H:%M} older than 48h before deadline"}

    teams = con.execute("SELECT team_id, name FROM teams WHERE season = ?", [season]).df()
    tid_by_name = dict(zip(teams["name"], teams["team_id"], strict=False))
    prev_kick = dict(con.execute(
        "SELECT team_id, MAX(kickoff_time) FROM ("
        " SELECT team_h AS team_id, kickoff_time FROM fixtures"
        "  WHERE season = ? AND kickoff_time < ?"
        " UNION ALL"
        " SELECT team_a, kickoff_time FROM fixtures"
        "  WHERE season = ? AND kickoff_time < ?)"
        " GROUP BY team_id",
        [season, now.replace(tzinfo=None), season, now.replace(tzinfo=None)],
    ).fetchall())
    roster = con.execute(
        "SELECT ps.code, ps.team_id, p.first_name, p.second_name, p.web_name "
        "FROM player_season ps JOIN players p USING (code) WHERE ps.season = ?",
        [season],
    ).df()

    rows, teams_used, skipped, listed, matched = [], 0, [], 0, 0
    for team_name, players in data.items():
        tid = tid_by_name.get(TEAM_ALIAS.get(team_name, team_name))
        if tid is None:
            skipped.append(f"{team_name}: unknown team")
            continue
        if any(float(v) >= 1.0 for v in players.values()):
            skipped.append(f"{team_name}: confirmed values (post-kickoff file)")
            continue
        pk = prev_kick.get(tid)
        if pk is not None and committed_at < pk.replace(tzinfo=UTC) + AFTER_KICKOFF:
            skipped.append(f"{team_name}: file predates its last match")
            continue
        levels = match_team({k: float(v) for k, v in players.items()},
                            roster[roster["team_id"] == tid])
        listed += len(players)
        matched += len(levels)
        teams_used += 1
        for code, level in levels.items():
            rows.append((season, gw, code, int(tid), "rotowire", level,
                         committed_at.replace(tzinfo=None), now.replace(tzinfo=None)))
    con.execute("DELETE FROM lineup_snapshots WHERE season = ? AND gw = ? AND source = 'rotowire'",
                [season, gw])
    db.executemany(con, UPSERT, rows)
    return {"teams": teams_used, "listed": listed, "matched": matched,
            "committed_at": committed_at.isoformat(), "skipped": skipped}


def lineup_inputs(con: duckdb.DuckDBPyConnection, season: str, gw: int
                  ) -> tuple[dict[int, float], set[int]]:
    """(code -> level, team_ids whose predicted XI matched in full) for `gw`.

    A team is "complete" when exactly 11 of its matched players sit at an XI
    level (0.8/0.5); only then does absence from the list mean "not starting".
    """
    df = con.execute(
        "SELECT code, team_id, level FROM lineup_snapshots "
        "WHERE season = ? AND gw = ? AND source = 'rotowire'",
        [season, gw],
    ).df()
    if df.empty:
        return {}, set()
    xi = df[df["level"].isin(XI_LEVELS)].groupby("team_id").size()
    complete = set(xi.index[xi == 11].astype(int))
    return dict(zip(df["code"].astype(int), df["level"].astype(float), strict=True)), complete


# Logistic overlay, fit on 29,707 matched player-GWs (2025-26 GW5-38 +
# 2026-27 GW1-5, out-of-sample model predictions); see docs/lineups_fpl_2026-10.md.
_C60 = {0.8: 1.660, 0.5: 1.323, 0.2: 0.086, 0.0: -1.853, None: 0.0}
_C1 = {0.8: 1.188, 0.5: 0.410, 0.2: 0.097, 0.0: -1.148, None: 0.0}


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def _level_key(lvl: float | None) -> float | None:
    """Coefficient bucket. 0.4 is RotoWire's "in the XI but tagged OUT", which
    the fit grouped with 0.0 (out), not with 0.5 (questionable)."""
    if lvl is None:
        return None
    if abs(lvl - 0.4) < 1e-6:
        return 0.0
    return min((k for k in _C60 if k is not None), key=lambda k: abs(k - lvl))


def apply_overlay(proba: np.ndarray, codes, team_ids,
                  levels: dict[int, float], complete: set[int]) -> np.ndarray:
    """Adjust [P0, P1-59, P60+] rows with the predicted line-up.

    Rows of teams not covered by the snapshot are untouched. In a covered
    team, a listed player uses his level; an unlisted one uses the "not
    listed" term only if that team's XI matched in full.
    """
    out = proba.copy()
    codes = np.asarray(codes)
    team_ids = np.asarray(team_ids)
    covered_teams = complete | {t for t in set(team_ids.tolist())
                                if any(levels.get(int(c)) is not None
                                       for c in codes[team_ids == t])}
    for i, (code, tid) in enumerate(zip(codes, team_ids, strict=True)):
        if tid not in covered_teams:
            continue
        lvl = levels.get(int(code))
        if lvl is None and tid not in complete:
            continue
        key = _level_key(lvl)
        p60 = 1 / (1 + np.exp(-(-0.989 + 0.733 * _logit(proba[i, 2]) + _C60[key])))
        p1 = 1 / (1 + np.exp(-(-0.299 + 0.872 * _logit(1 - proba[i, 0]) + _C1[key])))
        p1 = max(p1, p60)
        out[i] = [1 - p1, p1 - p60, p60]
    return out
