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
  * Pre-season — English Wikipedia's per-club "2026-27 <club> season" articles,
    "Pre-season and friendlies" section, which uses the standardised
    {{Football box collapsible}} template. This is scraped rather than fetched
    from a feed because no free structured feed for club friendlies exists:
    fbref and worldfootball both 403 on automated requests, TheSportsDB's free
    tier no longer returns the friendlies league, and openfootball's friendlies
    dataset is national teams only. Wikipedia is the source these tools all
    reprint anyway, and its match template is machine-readable.

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
WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "fpl-ai-scout/0.1 (https://github.com/; personal FPL project)"

POLITE_INTERVAL = 0.3
TTL = 24 * 3600  # both sources are finished history by GW1; a daily poll is plenty

# FPL `teams.name` -> English Wikipedia article title. Explicit rather than
# derived: FPL's names are abbreviations ("Spurs", "Nott'm Forest") and the
# article titles carry club suffixes that vary ("F.C." / "A.F.C." / prefixed
# "AFC"), so there is no rule to derive, only a table to write down. All 20
# verified to resolve without a redirect.
SEASON_PAGES = {
    "Arsenal": "2026–27 Arsenal F.C. season",
    "Aston Villa": "2026–27 Aston Villa F.C. season",
    "Bournemouth": "2026–27 AFC Bournemouth season",
    "Brentford": "2026–27 Brentford F.C. season",
    "Brighton": "2026–27 Brighton & Hove Albion F.C. season",
    "Chelsea": "2026–27 Chelsea F.C. season",
    "Coventry City": "2026–27 Coventry City F.C. season",
    "Crystal Palace": "2026–27 Crystal Palace F.C. season",
    "Everton": "2026–27 Everton F.C. season",
    "Fulham": "2026–27 Fulham F.C. season",
    "Hull City": "2026–27 Hull City A.F.C. season",
    "Ipswich Town": "2026–27 Ipswich Town F.C. season",
    "Leeds": "2026–27 Leeds United F.C. season",
    "Liverpool": "2026–27 Liverpool F.C. season",
    "Man City": "2026–27 Manchester City F.C. season",
    "Man Utd": "2026–27 Manchester United F.C. season",
    "Newcastle": "2026–27 Newcastle United F.C. season",
    "Nott'm Forest": "2026–27 Nottingham Forest F.C. season",
    "Spurs": "2026–27 Tottenham Hotspur F.C. season",
    "Sunderland": "2026–27 Sunderland A.F.C. season",
}

# The heading is written both ways across articles ("==Pre-season and
# friendlies==", "== Pre-season ==", "==Friendlies==").
_PRESEASON_HEADING = re.compile(r"^==\s*[^=\n]*(?:pre-season|friendl)[^=\n]*==\s*$", re.I | re.M)
_NEXT_HEADING = re.compile(r"^==[^=]", re.M)
# Boxes close with `}}` on its own line; every nested template ({{goal|14}}) is
# inline, so this never terminates early.
_BOX = re.compile(r"\{\{\s*football box collapsible\b(.*?)\n\}\}", re.I | re.S)
_FIELD = re.compile(r"^\|\s*([A-Za-z0-9_]+)\s*=[ \t]*(.*?)(?=^\|\s*[A-Za-z0-9_]+\s*=|\Z)",
                    re.M | re.S)
_GOAL_TMPL = re.compile(r"\{\{\s*goal\s*\|([^}]*)\}\}", re.I)
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
_DISAMBIG = re.compile(r"\s*\([^)]*\)\s*$")


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


def _goal_count(line: str) -> int:
    """Goals in one `*[[Player]] {{goal|12|45+2}}` bullet.

    One {{goal}} template can carry several minutes — `{{goal|57||59}}` is a
    brace, not one goal — so goals are counted per non-empty argument. A
    template with no usable minute at all (`{{goal|}}`, seen where the source
    didn't record one) is still one goal, not zero.
    """
    total = 0
    for args in _GOAL_TMPL.findall(line):
        total += max(1, sum(1 for a in args.split("|") if a.strip()))
    return total


def _scorer_name(line: str) -> str:
    """Player name from a goal bullet: the wikilink TARGET where there is one
    (`[[Kai Havertz|Havertz]]` -> "Kai Havertz" — the full name matches FPL far
    more often than the display surname), else the bare text before the first
    template. Disambiguators are dropped: `[[Nico González (footballer, born
    2002)|Nico]]` -> "Nico González"."""
    link = _WIKILINK.search(line)
    raw = link.group(1) if link else line.split("{{")[0]
    return _DISAMBIG.sub("", raw.lstrip("* ").strip()).strip()


def parse_preseason(wikitext: str) -> dict[tuple[str, str], tuple[str, int]]:
    """One club's article -> {(date, normalised scorer): (display name, goals)}.

    KEYED BY DATE-AND-SCORER, NOT BY FIXTURE, and that is the whole trick. Two
    Premier League clubs meeting in pre-season are written up on BOTH clubs'
    articles, and the two copies don't spell the teams the same way — a club
    names itself plainly ("| team2 = Newcastle") and links its opponent
    ("| team1 = [[Newcastle United F.C.|Newcastle United]]"). A fixture key
    built from team names therefore fails to collapse the duplicate and counts
    every goal in that match twice (caught in a real run: Harvey Barnes came out
    at 3 pre-season goals having scored 2). A player plays at most one match on
    a given date, so (date, player) names the same goals in both copies and
    dedupes by construction, with no team-name resolution needed at all.

    Only the `goals1`/`goals2` fields are read, which keeps penalty-shootout
    scorers ({{pengoal}} in `penalties1`/`penalties2`) out: a shootout is not a
    goal in anyone's records, least of all FPL's.

    Restricted to the pre-season section. The same template renders the club's
    Premier League and cup matches further down the article, and sweeping the
    whole page would quietly fold real competitive goals into a "pre-season"
    score.
    """
    heading = _PRESEASON_HEADING.search(wikitext)
    if heading is None:
        return {}
    rest = wikitext[heading.end():]
    end = _NEXT_HEADING.search(rest)
    section = rest[: end.start()] if end else rest

    scorers: dict[tuple[str, str], tuple[str, int]] = {}
    for body in _BOX.findall(section):
        fields = {k: v for k, v in _FIELD.findall("\n" + body.strip())}
        date = normalise_name(fields.get("date", ""))
        for side in ("goals1", "goals2"):
            for line in fields.get(side, "").splitlines():
                line = line.strip()
                # "o.g." appears as {{o.g.|30}} or as a {{goal}} argument; either
                # way the goal belongs to the other team, so drop the bullet.
                if not line.startswith("*") or "o.g." in line.lower():
                    continue
                count = _goal_count(line)
                name = _scorer_name(line)
                if count and name:
                    scorers[(date, normalise_name(name))] = (name, count)
    return scorers


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
    no cached copy — a Wikipedia outage must not take `refresh` down."""
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


def _wikitext(client: httpx.Client, title: str, cache_dir: Path) -> str | None:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")
    body = _fetch(
        client,
        WIKI_API,
        {"action": "parse", "page": title, "prop": "wikitext",
         "format": "json", "formatversion": "2"},
        cache_dir / f"{slug}.json",
    )
    if body is None:
        return None
    try:
        return json.loads(body)["parse"]["wikitext"]
    except (json.JSONDecodeError, KeyError):
        return None  # article not created yet -> {"error": ...}


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
    preseason: dict[tuple[str, str], tuple[str, int]] = {}
    pages_read = 0
    with httpx.Client(
        timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        payload = _fetch(client, WORLD_CUP_URL, None, cache_dir / "worldcup_2026.json")
        if payload is not None:
            try:
                wc_goals = parse_world_cup(json.loads(payload))
            except json.JSONDecodeError:
                wc_goals = Counter()

        for title in SEASON_PAGES.values():
            wikitext = _wikitext(client, title, cache_dir)
            if wikitext is None:
                continue
            pages_read += 1
            # dict update, not Counter merge: a (date, scorer) seen on two clubs'
            # articles is one player-day, and must count once (see parse_preseason).
            preseason.update(parse_preseason(wikitext))

    preseason_goals: Counter[str] = Counter()
    for name, goals in preseason.values():
        preseason_goals[name] += goals

    rows: dict[int, dict] = {}
    for source, column in ((wc_goals, "wc_goals"), (preseason_goals, "preseason_goals")):
        for name, goals in source.items():
            code = lookup.get(normalise_name(name))
            if code is None:
                continue  # not a Premier League player this season, or ambiguous
            row = rows.setdefault(
                code, {"code": code, "player_name": name, "wc_goals": 0.0,
                       "preseason_goals": 0.0}
            )
            row[column] += float(goals)

    con.execute("DELETE FROM summer_form")
    if rows:
        summer_df = pd.DataFrame(list(rows.values()))
        con.register("summer_rows", summer_df)
        con.execute("INSERT INTO summer_form BY NAME SELECT * FROM summer_rows")
        con.unregister("summer_rows")
    return {
        "pages": pages_read,
        "wc_scorers": len(wc_goals),
        "preseason_scorer_days": len(preseason),
        "matched_players": len(rows),
    }
