"""UEFA Champions League Fantasy feeds.

Public JSON, no auth, no key. Base path is stable across seasons; `tour` selects
the season and steps by 10: 30=2020/21 ... 80=2025/26, 90=2026/27.

Two deliberate choices, both learned from fpl-ai-scout's failures:

1. We validate ONLY the fields we consume and ignore unknown new ones. That repo
   used pydantic `extra="forbid"` and took four production outages from UEFA-
   equivalent APIs *adding* fields. Drift still has to be caught -- but by
   `tests/test_schema_fingerprint.py` at review time, not by a 3am outage.

2. Deadlines are parsed from two feeds that disagree on format and neither
   carries a timezone (`"09/08/2026 18:45:00"` vs `"09/08/26 06:45:00 PM"`).
   `deadline_utc` parses both, attaches Europe/Zurich explicitly, and asserts
   they agree. The same naive-timestamp bug silently shifted every deadline in
   the FPL repo by the writing machine's UTC offset.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

BASE = "https://gaming.uefa.com/en/uclfantasy/services/feeds"
CURRENT_TOUR = 90                     # 2026/27
SEASONS = {30: "2020-21", 40: "2021-22", 50: "2022-23", 60: "2023-24",
           70: "2024-25", 80: "2025-26", 90: "2026-27"}

# UEFA serves match/deadline times in continental Europe local time. feedTime
# exposes utcTime and cestTime side by side, which is how this was pinned.
UEFA_TZ = ZoneInfo("Europe/Zurich")

SKILL = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
# "" fit | NIS not in squad | I injured | D doubtful | S suspended
UNAVAILABLE = {"NIS", "I", "S"}

_UA = "ucl-scout/0.1 (+https://github.com/)"
# feeds are CDN-cached at max-age=95; polling faster gains nothing
MIN_POLL_SECONDS = 95


class FeedError(RuntimeError):
    pass


def _req(d: dict, key: str, ctx: str) -> Any:
    """Fetch a field we depend on, failing loudly if UEFA drops it."""
    if key not in d:
        raise FeedError(f"{ctx}: required field {key!r} missing "
                        f"(have: {sorted(d)[:12]}...)")
    return d[key]


def fetch(path: str, *, timeout: int = 30) -> dict:
    req = urllib.request.Request(f"{BASE}/{path}", headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _value(payload: dict, ctx: str) -> Any:
    return _req(_req(payload, "data", ctx), "value", ctx)


# --------------------------------------------------------------------------
# time

_FORMATS = ("%m/%d/%Y %H:%M:%S", "%m/%d/%y %I:%M:%S %p", "%m/%d/%Y %I:%M:%S %p")


def parse_uefa_time(s: str) -> datetime:
    """Parse a UEFA timestamp into an aware UTC datetime.

    Handles both published formats. Never returns a naive datetime -- that is
    the whole point of this function existing.
    """
    s = s.strip()
    for fmt in _FORMATS:
        try:
            naive = datetime.strptime(s, fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=UEFA_TZ).astimezone(UTC)
    raise FeedError(f"unrecognised UEFA timestamp {s!r}")


# --------------------------------------------------------------------------
# feeds

@dataclass(frozen=True)
class Team:
    id: int
    short: str
    name: str
    pot: int                       # UEFA seeding pot 1-4; 1 is strongest
    eliminated: bool = False


@dataclass(frozen=True)
class Match:
    id: int
    matchday: int
    kickoff: datetime              # aware, UTC
    home_id: int
    away_id: int
    lineup_announced: bool
    status: str


@dataclass(frozen=True)
class Matchday:
    id: int
    deadline: datetime             # aware, UTC
    subs_allowed: int              # per-matchday, NOT a constant (MD1 = 2)
    is_current: bool
    is_locked: bool
    matches: list[Match] = field(default_factory=list)

    @property
    def gamedays(self) -> list[str]:
        """Distinct calendar days this matchday spans (2-3; MD1 spans 3)."""
        return sorted({m.kickoff.date().isoformat() for m in self.matches})


@dataclass(frozen=True)
class Player:
    id: int
    name: str
    team_id: int
    team: str
    pos: str                       # GK / DEF / MID / FWD
    value: float                   # EUR millions
    status: str                    # "" | NIS | I | D | S
    selected_pct: float
    # Cumulative stats. NOTE: at MD1 these are LAST season's; from MD2 they are
    # this season's running totals. Always carry as_of_matchday alongside.
    total_points: int
    minutes: int
    goals: int
    assists: int
    clean_sheets: int
    saves: int
    recoveries: int                # raw ball recoveries; 1 fantasy pt per 3
    motm: int
    as_of_matchday: int

    @property
    def available(self) -> bool:
        return self.status not in UNAVAILABLE

    @property
    def recoveries_per90(self) -> float | None:
        if self.minutes < 270:     # too small a sample to rate
            return None
        return self.recoveries / (self.minutes / 90)


def teams(tour: int = CURRENT_TOUR) -> list[Team]:
    ctx = "teams"
    return [
        Team(
            id=int(_req(t, "id", ctx)),
            short=_req(t, "shortName", ctx),
            name=_req(t, "webName", ctx),
            pot=int(str(t.get("htPtName") or "Pot 3").replace("Pot ", "")),
            eliminated=bool(t.get("isEliminated") or False),
        )
        for t in _value(fetch(f"teams/teams_{tour}_en.json"), ctx)
    ]


def matchdays(tour: int = CURRENT_TOUR) -> list[Matchday]:
    ctx = "fixtures"
    out = []
    for md in _value(fetch(f"fixtures/fixtures_{tour}_en.json"), ctx):
        mid = int(_req(md, "mdId", ctx))
        matches = [
            Match(
                id=int(_req(m, "mId", ctx)),
                matchday=mid,
                kickoff=parse_uefa_time(_req(m, "dateTime", ctx)),
                home_id=int(_req(m, "htId", ctx)),
                away_id=int(_req(m, "atId", ctx)),
                lineup_announced=bool(int(m.get("lineupAnnounced") or 0)),
                status=str(m.get("matchStatus") or ""),
            )
            for m in (md.get("match") or [])
        ]
        out.append(Matchday(
            id=mid,
            deadline=parse_uefa_time(_req(md, "deadline", ctx)),
            subs_allowed=int(md.get("subsAllowed") or 0),
            is_current=bool(int(md.get("mdIsCurrent") or 0)),
            is_locked=bool(int(md.get("mdIsLocked") or 0)),
            matches=matches,
        ))
    return out


def players(tour: int = CURRENT_TOUR, matchday: int = 1) -> list[Player]:
    ctx = "players"
    payload = fetch(f"players/players_{tour}_en_{matchday}.json")
    rows = _req(_value(payload, ctx), "playerList", ctx)
    out = []
    for p in rows:
        skill = int(_req(p, "skill", ctx))
        if skill not in SKILL:
            raise FeedError(f"unknown skill {skill!r} -- position map changed")
        out.append(Player(
            id=int(_req(p, "id", ctx)),
            name=_req(p, "pDName", ctx),
            team_id=int(_req(p, "tId", ctx)),
            team=_req(p, "tName", ctx),
            pos=SKILL[skill],
            value=float(_req(p, "value", ctx)),
            status=str(p.get("pStatus") or ""),
            selected_pct=float(p.get("selPer") or 0),
            total_points=int(p.get("totPts") or 0),
            minutes=int(p.get("minsPlyd") or 0),
            goals=int(p.get("gS") or 0),
            assists=int(p.get("assist") or 0),
            clean_sheets=int(p.get("cS") or 0),
            saves=int(p.get("saves") or 0),
            recoveries=int(p.get("bR") or 0),
            motm=int(p.get("mOM") or 0),
            as_of_matchday=matchday,
        ))
    return out


@dataclass(frozen=True)
class Constraints:
    matchday: int
    last_matchday: int
    budget: float
    max_per_club: int
    deadline: datetime             # aware, UTC


def constraints(tour: int = CURRENT_TOUR) -> Constraints:
    ctx = "constraints"
    v = _value(fetch(f"constraints/constraints_{tour}.json"), ctx)
    return Constraints(
        matchday=int(_req(v, "matchdayId", ctx)),
        last_matchday=int(_req(v, "lastMatchdayId", ctx)),
        budget=float(_req(v, "maxTeamValue", ctx)),
        max_per_club=int(_req(v, "maxTeamPlayers", ctx)),
        deadline=parse_uefa_time(_req(v, "deadline", ctx)),
    )


def deadline_utc(tour: int = CURRENT_TOUR) -> datetime:
    """The upcoming deadline, cross-checked across both feeds that publish it.

    They use different formats and neither carries a timezone, so agreement
    between them is the only available proof the parse is right.
    """
    c = constraints(tour)
    md = next((m for m in matchdays(tour) if m.id == c.matchday), None)
    if md is None:
        return c.deadline
    drift = abs((md.deadline - c.deadline).total_seconds())
    if drift > 60:
        raise FeedError(
            f"deadline disagreement for MD{c.matchday}: constraints says "
            f"{c.deadline.isoformat()}, fixtures says {md.deadline.isoformat()} "
            f"({drift:.0f}s apart) -- do not trust either until resolved"
        )
    return c.deadline
