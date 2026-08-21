"""Summer-form ingest: 2026 World Cup + club pre-season friendlies -> `summer_form`.

The gap this fills is the same one odds.py fills for teams, but for players. At
a season opening the rolling features are all NULL (windows reset per season,
features/build.py) and `prev_season_*` is the only player-level signal — which
says nothing about the two months immediately before GW1. A player who scored
four at the World Cup, or five in pre-season, spent that gap doing something the
model cannot see.

DELIBERATELY NOT A MODEL FEATURE. Odds became FEATURE_COLUMNS entries because
they exist for every fixture of every training season; summer form does not.
There is one summer per year, only ~200 PL players play a World Cup at all, and
the older seasons in the training set (2021-22..2025-26) have no comparable
column to learn a weight from. Training on that would be fitting a coefficient
to a handful of 2026 rows. So this is applied as a small, bounded, inference-only
multiplier on EV instead (pipeline.py::summer_boost) — the weight is set by hand,
low, and only clears zero for genuinely exceptional returns.

Sources, both free and public-domain, both goals-only (neither publishes minutes
or assists, so "exceptional" here means goals):

  * World Cup — openfootball/worldcup.json. One JSON file, no key, all 104
    matches with scorer names, penalty and own-goal flags.
  * Pre-season — nextxi.app's public PostgREST API (`preseason_player_stats`),
    which is Opta/Sportmonks data that nextXI licenses and states plainly is
    free to use ("Data is free - best way to say thanks for data is a follow
    @nextXI_fpl"). This REPLACED a Wikipedia wikitext scraper that could only
    ever recover goals: the API also carries minutes, starts and appearances,
    which is the signal that actually matters in pre-season (who a manager is
    picking beats who got on the end of a tap-in against a League Two side).
    `is_pl_team` also removes the opposition entirely, so the old scraper's
    whole class of mis-attribution bugs — opposition scorers, the same friendly
    written up on both clubs' articles under different team spellings — simply
    cannot occur.

    FETCHED ONCE, CACHED FOREVER (see PRESEASON_CACHE). Pre-season is finished
    and these rows are frozen, so re-polling nightly would spend someone else's
    Supabase egress to receive bytes we already have.

Everything degrades to "no boost", never to an error: a page that hasn't been
written yet, a scorer whose name doesn't resolve to an FPL code, or a total
network failure all just leave a player out of `summer_form`.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from collections import Counter
from pathlib import Path

import duckdb
import httpx
import pandas as pd

WORLD_CUP_URL = (
    "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"
)
NEXTXI_API = "https://api.nextxi.app/rest/v1/preseason_player_stats"
# nextXI's Supabase ANON key, lifted from their public JS bundle. Anon keys are
# designed to be published (they authorise exactly what row-level security
# allows and nothing more) — this is not a secret, and is checked in for the
# same reason the football-data.co.uk URL is.
NEXTXI_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFxaGlla2F0"
    "bWJ5YWZyZG9lc29wIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MjQyNTE3MjksImV4cCI6MjAzOTgyNzcyOX0"
    ".HkNdqb7SdkdHM_2Zv36fsXI32MuMibjlpN9PpezwdWY"
)
NEXTXI_PAGE = 1000  # PostgREST's own max page size
NEXTXI_FIELDS = (
    "player_name,display_name,team_name,is_pl_team,started,minutes_played,goals,assists"
)
USER_AGENT = "fpl-ai-scout/0.1 (personal FPL project; thanks @nextXI_fpl)"

POLITE_INTERVAL = 0.3
TTL = 24 * 3600  # the World Cup file; finished history, a daily poll is plenty
PRESEASON_CACHE = "nextxi_preseason.json"  # written once, never re-fetched

def normalise_name(name: str) -> str:
    """Accent-stripped, punctuation-free, lowercase — the join key between three
    sources that spell the same player three ways (Quiñones / Quinones,
    Aït-Nouri / Ait Nouri)."""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", " ", stripped.lower()).strip()


def parse_world_cup(payload: dict) -> Counter[str]:
    """worldcup.json -> {scorer name: goals}.

    Own goals are excluded — they are credited to the scorer's name in the
    OPPOSING team's goal list, so counting them would hand a defender a boost
    for the single worst thing they did all summer. Penalties are kept: they
    are goals, and FPL pays for them.
    """
    goals: Counter[str] = Counter()
    for match in payload.get("matches", []):
        for side in ("goals1", "goals2"):
            for goal in match.get(side) or []:
                if goal.get("owngoal"):
                    continue
                name = (goal.get("name") or "").strip()
                if name:
                    goals[name] += 1
    return goals


def name_to_code(con: duckdb.DuckDBPyConnection, season: str) -> dict[str, int]:
    """Normalised name -> FPL `code`, for players registered in `season`.

    Scoped to the season on purpose: `players` accumulates every code ever seen
    (~1900 rows), and a departed player now at a foreign club would otherwise
    match an opposition scorer in a pre-season fixture and collect a boost for a
    team they no longer play for.

    Three key forms are tried in descending specificity — full name, web name,
    surname. A key that maps to more than one player is dropped rather than
    guessed at: two Premier League players share a surname often enough that a
    coin flip would misattribute goals, and a missing boost is a much cheaper
    error than a boost on the wrong player.
    """
    rows = con.execute(
        """
        SELECT DISTINCT p.code, p.first_name, p.second_name, p.web_name
        FROM players p JOIN player_season s ON s.code = p.code
        WHERE s.season = ?
        """,
        [season],
    ).fetchall()
    tiers: list[dict[str, set[int]]] = [{}, {}, {}]
    for code, first, second, web in rows:
        keys = [
            normalise_name(f"{first or ''} {second or ''}"),
            normalise_name(web or ""),
            normalise_name(second or ""),
        ]
        for tier, key in zip(tiers, keys, strict=True):
            if key:
                tier.setdefault(key, set()).add(int(code))

    lookup: dict[str, int] = {}
    for tier in tiers:  # specific tiers win; a later tier never overwrites
        for key, codes in tier.items():
            if len(codes) == 1 and key not in lookup:
                lookup[key] = next(iter(codes))
    return lookup


def _fetch(client: httpx.Client, url: str, params: dict | None, cache: Path) -> str | None:
    """GET with an on-disk cache. Returns None (never raises) on any failure with
    no cached copy — an upstream outage must not take `refresh` down."""
    if cache.exists() and time.time() - cache.stat().st_mtime < TTL:
        return cache.read_text()
    try:
        response = client.get(url, params=params)
        response.raise_for_status()
    except httpx.HTTPError:
        return cache.read_text() if cache.exists() else None
    time.sleep(POLITE_INTERVAL)
    cache.write_text(response.text)
    return response.text


def fetch_preseason(client: httpx.Client, cache_dir: Path) -> list[dict]:
    """All pre-season player-fixture rows from nextXI, PL clubs only.

    Cached with no expiry, unlike the World Cup file: pre-season 2026 is over and
    these rows will never change again, so a cache hit is not staleness, it is
    the correct answer. Re-polling would burn nextXI's Supabase egress to be told
    what we already know.

    PostgREST caps a page at 1000 rows, so this walks offsets until a short page
    ends it. A failure mid-walk returns what we have rather than raising — a
    partial pre-season still boosts the players it covers, and `refresh` must not
    die because someone else's API blinked.
    """
    cache = cache_dir / PRESEASON_CACHE
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except json.JSONDecodeError:
            pass  # truncated write from an interrupted run — refetch below

    rows: list[dict] = []
    for offset in range(0, 20_000, NEXTXI_PAGE):
        try:
            response = client.get(
                NEXTXI_API,
                params={
                    "select": NEXTXI_FIELDS,
                    "is_pl_team": "eq.true",
                    "offset": offset,
                    "limit": NEXTXI_PAGE,
                },
            )
            response.raise_for_status()
            page = response.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            break
        rows.extend(page)
        if len(page) < NEXTXI_PAGE:
            break
        time.sleep(POLITE_INTERVAL)

    if rows:
        cache.write_text(json.dumps(rows))
    return rows


def aggregate_preseason(rows: list[dict]) -> dict[str, dict[str, float]]:
    """Per-fixture rows -> per-player totals, keyed by normalised name.

    `minutes_played` is NULL for an unused substitute rather than 0, so it is
    coalesced; an unused sub is a real appearance of zero minutes, and treating
    the NULL as missing-data would quietly inflate everyone's minutes-per-game.

    Starts and minutes are carried alongside goals because they are the better
    pre-season signal: goals in July say something about finishing against mixed
    opposition, but who the manager actually STARTS says who is in favour, which
    is the thing our rolling features cannot see until real gameweeks exist.
    """
    totals: dict[str, dict[str, float]] = {}
    for row in rows:
        if not row.get("is_pl_team"):
            continue  # opposition player; nothing to attribute to an FPL squad
        name = (row.get("display_name") or row.get("player_name") or "").strip()
        if not name:
            continue
        agg = totals.setdefault(
            normalise_name(name),
            {"name": name, "goals": 0.0, "assists": 0.0,
             "minutes": 0.0, "starts": 0.0, "apps": 0.0},
        )
        agg["goals"] += float(row.get("goals") or 0)
        agg["assists"] += float(row.get("assists") or 0)
        agg["minutes"] += float(row.get("minutes_played") or 0)
        agg["starts"] += 1.0 if row.get("started") else 0.0
        agg["apps"] += 1.0
    return totals


def sync_summer(
    con: duckdb.DuckDBPyConnection, cache_dir: Path, season: str
) -> dict[str, int]:
    """Loads World Cup + pre-season goals for `season`'s registered players.

    Rewrites `summer_form` wholesale rather than upserting: the two sources are
    a fixed snapshot of one summer, so a re-run should reproduce them exactly,
    and a player who drops out of a re-parse should drop out of the table.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    lookup = name_to_code(con, season)

    wc_goals: Counter[str] = Counter()
    preseason: dict[str, dict[str, float]] = {}
    with httpx.Client(
        timeout=30.0,
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "apikey": NEXTXI_ANON_KEY,
            "Authorization": f"Bearer {NEXTXI_ANON_KEY}",
        },
    ) as client:
        payload = _fetch(client, WORLD_CUP_URL, None, cache_dir / "worldcup_2026.json")
        if payload is not None:
            try:
                wc_goals = parse_world_cup(json.loads(payload))
            except json.JSONDecodeError:
                wc_goals = Counter()
        preseason = aggregate_preseason(fetch_preseason(client, cache_dir))

    rows: dict[int, dict] = {}

    def row_for(code: int, name: str) -> dict:
        return rows.setdefault(
            code,
            {"code": code, "player_name": name, "wc_goals": 0.0, "preseason_goals": 0.0,
             "preseason_assists": 0.0, "preseason_minutes": 0.0,
             "preseason_starts": 0.0, "preseason_apps": 0.0},
        )

    for name, goals in wc_goals.items():
        code = lookup.get(normalise_name(name))
        if code is not None:  # else not a PL player this season, or ambiguous
            row_for(code, name)["wc_goals"] += float(goals)

    for key, agg in preseason.items():
        code = lookup.get(key)
        if code is None:
            continue
        row = row_for(code, agg["name"])
        row["preseason_goals"] += agg["goals"]
        row["preseason_assists"] += agg["assists"]
        row["preseason_minutes"] += agg["minutes"]
        row["preseason_starts"] += agg["starts"]
        row["preseason_apps"] += agg["apps"]

    con.execute("DELETE FROM summer_form")
    if rows:
        summer_df = pd.DataFrame(list(rows.values()))
        con.register("summer_rows", summer_df)
        con.execute("INSERT INTO summer_form BY NAME SELECT * FROM summer_rows")
        con.unregister("summer_rows")
    return {
        "wc_scorers": len(wc_goals),
        "preseason_players": len(preseason),
        "matched_players": len(rows),
    }
