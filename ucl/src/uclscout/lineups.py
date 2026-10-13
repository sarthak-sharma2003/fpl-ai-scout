"""Predicted line-ups -> P(start), for the next open UCL matchday only.

WHY THIS EXISTS
---------------
Every other minutes source here is a season average: a player's share of his
club's minutes, last season's share, a guess from price. None of them can see
that a manager is resting his centre-back on Tuesday, and minutes uncertainty
is the largest early-season projection error. Predicted line-ups are the one
free, match-specific signal.

Source: three scrapes (RotoWire, Sportsgambler, FutbolFantasy) republished as
JSON by github.com/pabloroldan98/knapsack-football-formations and refreshed a
few times a day. That repo carries no licence, so the files are fetched at run
time and NEVER vendored or committed here.

Three facts shape the module:

* Each file holds each club's NEXT match, in any competition. Once a UCL
  matchday is played it describes a league game. So the pipeline only asks
  for clubs whose UCL kick-off is close enough that nothing can sit in between
  (`fresh_clubs`), and only for the next open matchday -- never later ones.
* Names do not match UEFA's ("Vinicius Junior" vs "Vinícius Júnior",
  "K. Kvaratskhelia", "Rüdiger"). Matching runs within one club, so a surname
  only has to be unique among ~30 players, never across 1,171.
* Every source is optional. A failed fetch, a reshaped file or an unmapped club
  drops that piece and nothing else; with no sources at all the result is empty
  and projections fall back to observed minutes exactly as before. A publish
  must never die on a third-party scrape.
"""
from __future__ import annotations

import difflib
import itertools
import json
import re
import unicodedata
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .feeds import Matchday, Player, Team

RAW = ("https://raw.githubusercontent.com/pabloroldan98/"
       "knapsack-football-formations/master/json_files")
SOURCES = {
    name: f"{RAW}/{name}_champions_players_start_probabilities.json"
    for name in ("rotowire", "sportsgambler", "futbolfantasy")
}
_UA = "ucl-scout/0.1 (+https://github.com/)"
TIMEOUT = 20

# Raw source level -> P(start), piecewise-linear between these anchors. The
# sources use a few fixed levels: 0.8 "in the predicted XI" (all three), 1.0 an
# announced XI (FutbolFantasy), 0.6/0.5/0.4 a two-way doubt, 0.2 a named
# substitute, 0.0 out or not starting. A predicted XI is the best free signal
# there is but still a guess at someone else's team sheet, so 0.8 maps to 0.90
# rather than 1.0, and only an announced XI gets near certainty. Deliberately
# conservative: there is no archive of past predictions to calibrate against.
CALIBRATION = ((0.0, 0.0), (0.2, 0.10), (0.4, 0.50), (0.5, 0.50),
               (0.8, 0.90), (1.0, 0.98))
# A source that names a club's whole XI and leaves a player out is predicting
# he does not start -- the same reading as listing him on the bench. Inferred
# only when EVERY starter that source named matched a UEFA player: one failed
# match would otherwise bench a real starter.
IMPLIED_BENCH = 0.2
STARTER_LEVEL = 0.8
XI_SIZE = 11
# Levels a source publishes that do NOT describe the upcoming match. The
# FutbolFantasy scraper merges its team pages (the predicted XI for the next
# match: 0.8, or 0.6/0.4 for a two-way doubt) with match pages for the current
# AND following round, and the current round's "confirmed" XI -- 1.0 for its
# starters, 0.0 for everyone else -- is usually the round ALREADY PLAYED.
# Checked on the MD2 file (2026-10-13): every 1.0 was an MD1 starter, e.g.
# PSG's Akliouche, Dro, Zabarnyi and Ferran Torres, all missing from FF's own
# MD2 XI; and "Iván Fresneda" 0.0 sat beside "Ivan Fresneda" 0.8. So its 1.0s
# and 0.0s are dropped; its predicted XI still counts, and the implied-bench
# rule covers the rest of the squad.
IGNORE_LEVELS = {"futbolfantasy": frozenset({0.0, 1.0})}

# How far ahead a club's UCL kick-off may be for its "next match" file to be
# read as THIS match. A club's last league game before a Tuesday tie is at the
# latest on Sunday night; 40h before a Tuesday-evening kick-off is the small
# hours of Monday, after that game AND after the scrapes' nightly refresh. At
# 48h a Sunday-night club's file could still be showing the league game.
FRESH_HOURS = 40.0

# Source club name (normalised) -> UEFA short code, for names too far from
# UEFA's to match on their own ("PSG" vs "Paris", "Glimt" vs "Bodø/Glimt").
TEAM_ALIASES = {
    "psg": "PSG",
    "atletico": "ATM",
    "glimt": "BOD",
    "betis": "BET",
    "sabah fk": "SAB",
    "aek athen": "AEK",
    "bayern munich": "BAY",
    "manchester city": "MCI",
    "manchester united": "MUN",
    "sporting": "SPO",
    "sporting portugal": "SPO",
    "slavia prague": "SLA",
    "slavia praga": "SLA",
    "slovan bratislava": "SBR",
    "slo bratislava": "SBR",
    "shakhtar donetsk": "SHK",
    "fc shakhtar donetsk": "SHK",
    "borussia dortmund": "BVB",
}
_CLUB_NOISE = {"fc", "cf", "fk", "sc", "afc", "ac", "cp", "sk", "club", "vfb",
               "rb", "the"}

# Source player name (normalised) -> UEFA name (normalised, full or display),
# for misses fuzzy matching cannot bridge: nicknames, dropped or extra names.
# Add to this when the publish log's unmatched sample shows a real player.
ALIASES: dict[str, str] = {
    "magalhaes": "gabriel",                    # Arsenal: UEFA lists "Gabriel"
    "pierre ganiou": "ismaelo ganiou",         # Lens
    "tanguy kouassi": "tanguy nianzou",        # Lille: Tanguy Nianzou Kouassi
    "nianzou kouassi": "tanguy nianzou",
    "romulo cruz": "romulo cardoso",           # Leipzig: Rômulo Cardoso da Cruz
    "zambo anguissa": "frank anguissa",        # Napoli: André-Frank Zambo Anguissa
    "anastasios douvikas": "tasos douvikas",   # Como
    "wesley vinicius": "wesley franca",        # Roma: Wesley Vinícius França
    "ez abde": "abde ezzalzouli",              # Betis
    "francisco garcia": "fran garcia",         # Betis
}

# Name matching. A token pair scores its string similarity when that is at
# least TOKEN_MIN; a lone initial scores INITIAL against a token it starts.
TOKEN_MIN = 0.8
INITIAL = 0.6
ACCEPT = 0.75        # a candidate must score this ...
MARGIN = 0.05        # ... and beat the club's next-best by this, unless exact


# --------------------------------------------------------------------------
# names

_FOLD = str.maketrans({"ø": "o", "Ø": "o", "đ": "d", "Đ": "d", "ł": "l",
                       "Ł": "l", "ß": "ss", "æ": "ae", "Æ": "ae", "œ": "oe",
                       "Œ": "oe", "ı": "i", "ð": "d", "Ð": "d", "þ": "th",
                       "Þ": "th"})


def normalise(name: str) -> str:
    """'Vinícius Júnior' -> 'vinicius junior', "N'Golo" -> 'ngolo'.

    NFKD splits accents off as combining marks; a few letters (ø, ł, ß, ı)
    are not composed characters at all and must be folded by hand.
    """
    s = unicodedata.normalize("NFKD", name.translate(_FOLD))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"['’`]", "", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def _token_score(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if a[0] != b[0]:
        # transliterations keep the first letter (Ilya/Illia, Willian/William);
        # Hernandez/Fernandez are different men
        return 0.0
    if len(a) == 1 or len(b) == 1:
        return INITIAL
    r = difflib.SequenceMatcher(None, a, b).ratio()
    return r if r >= TOKEN_MIN else 0.0


def name_score(src: str, uefa: str) -> float:
    """Similarity of two normalised names, 0..1. Only an exact match is 1.0.

    Every token of the shorter name must find a partner in the longer one
    (surname-only "rudiger" fits "antonio rudiger"; "k kvaratskhelia" fits
    "khvicha kvaratskhelia"), discounted a little for tokens left unexplained
    so that a full-name hit outranks a partial one. Initials alone never match.
    """
    if not src or not uefa:
        return 0.0
    if src == uefa:
        return 1.0
    a, b = src.split(), uefa.split()
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    free = list(long_)
    total, anchored = 0.0, False
    for t in short:
        best = max(free, key=lambda u: _token_score(t, u), default=None)
        s = _token_score(t, best) if best is not None else 0.0
        if s > 0:
            free.remove(best)
            anchored |= len(t) > 1 and len(best) > 1
        total += s
    whole = difflib.SequenceMatcher(None, src, uefa).ratio()
    tokens = (total / len(short)) * (0.85 + 0.15 * len(short) / len(long_))
    score = max(tokens if anchored else 0.0, whole if whole >= 0.85 else 0.0)
    return min(0.95, score)


def match_club(names: Iterable[str], roster: list[Player]) -> dict[str, int]:
    """Source names -> UEFA ids within one club; ambiguous names stay unmatched.

    One-to-one: two source names cannot claim the same UEFA player.
    """
    # UEFA sometimes carries one man twice -- a stale NIS row beside the live
    # one (Aston Villa's Wan-Bissaka, MD2) -- which would make him ambiguous.
    keys: dict[int, frozenset[str]] = {}
    for p in sorted(roster, key=lambda p: p.status == "NIS"):
        ks = frozenset({normalise(p.name), normalise(p.full_name)} - {""})
        if ks and ks not in keys.values():
            keys[p.id] = ks
    pairs = []
    for name in names:
        n = normalise(name)
        n = ALIASES.get(n, n)
        scored = sorted(((max(name_score(n, k) for k in ks), pid)
                         for pid, ks in keys.items()), reverse=True)
        if not scored or scored[0][0] < ACCEPT:
            continue
        best, pid = scored[0]
        second = scored[1][0] if len(scored) > 1 else 0.0
        if best - second >= MARGIN or best == 1.0 > second:
            pairs.append((best, name, pid))
    out: dict[str, int] = {}
    taken: set[int] = set()
    for _, name, pid in sorted(pairs, reverse=True):
        if pid not in taken:
            out[name] = pid
            taken.add(pid)
    return out


def map_clubs(names: Iterable[str], teams: list[Team]) -> dict[str, int]:
    """Source club names -> UEFA team ids: alias, exact, token subset, fuzzy."""
    by_short = {t.short.upper(): t.id for t in teams}
    norm = {t.id: normalise(t.name) for t in teams}
    toks = {tid: set(n.split()) - _CLUB_NOISE for tid, n in norm.items()}
    out: dict[str, int] = {}
    for name in names:
        n = normalise(name)
        tid = by_short.get(TEAM_ALIASES.get(n, ""))
        if tid is None:
            tid = next((i for i, v in norm.items() if v == n), None)
        if tid is None:
            mine = set(n.split()) - _CLUB_NOISE
            subset = [i for i, t in toks.items()
                      if mine and t and (t <= mine or mine <= t)]
            tid = subset[0] if len(subset) == 1 else None
        if tid is None:
            close = difflib.get_close_matches(n, list(norm.values()), 1, 0.75)
            tid = next((i for i, v in norm.items() if close and v == close[0]),
                       None)
        if tid is not None:
            out[name] = tid
    return out


# --------------------------------------------------------------------------
# probabilities

def calibrate(level: float) -> float:
    """A source's raw level -> P(start), by the CALIBRATION anchors."""
    level = max(0.0, min(1.0, level))
    for (x0, y0), (x1, y1) in itertools.pairwise(CALIBRATION):
        if level <= x1:
            return y0 + (y1 - y0) * (level - x0) / (x1 - x0)
    return CALIBRATION[-1][1]


def fresh_clubs(md: Matchday | None, now: datetime,
                hours: float = FRESH_HOURS) -> set[int]:
    """Clubs whose kick-off in `md` is still ahead, and within `hours`.

    Only for these can a file of "each club's next match" be describing the
    UCL match rather than a league game in between.
    """
    if md is None:
        return set()
    horizon = now + timedelta(hours=hours)
    out: set[int] = set()
    for m in md.matches:
        if now < m.kickoff <= horizon:
            out |= {m.home_id, m.away_id}
    return out


# --------------------------------------------------------------------------
# fetching

def fetch_source(url: str, *, timeout: float = TIMEOUT) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _clean(payload: object) -> dict[str, dict[str, float]]:
    """Keep only well-formed {club: {player: level in [0, 1]}} entries."""
    if not isinstance(payload, dict):
        raise TypeError(f"expected an object, got {type(payload).__name__}")
    out: dict[str, dict[str, float]] = {}
    for club, roster in payload.items():
        if not isinstance(club, str) or not isinstance(roster, dict):
            continue
        rows = {n: float(v) for n, v in roster.items()
                if isinstance(n, str) and isinstance(v, int | float)
                and not isinstance(v, bool) and 0.0 <= v <= 1.0}
        if rows:
            out[club] = rows
    if not out:
        raise ValueError("no usable club entries")
    return out


def fetch_all(sources: dict[str, str] = SOURCES, *,
              fetch: Callable[[str], object] | None = None,
              ) -> tuple[dict[str, dict[str, dict[str, float]]], dict[str, str]]:
    """Every source that answers with usable data, and why each other did not."""
    fetch = fetch or fetch_source
    got, failed = {}, {}
    for name, url in sources.items():
        try:
            got[name] = _clean(fetch(url))
        except Exception as e:                               # noqa: BLE001
            failed[name] = f"{type(e).__name__}: {e}"[:200]  # drop this source only
    return got, failed


# --------------------------------------------------------------------------
# the result

@dataclass
class LineupReport:
    probs: dict[int, float] = field(default_factory=dict)  # UEFA id -> P(start)
    sources: list[str] = field(default_factory=list)       # answered
    failed: dict[str, str] = field(default_factory=dict)   # source -> reason
    clubs: int = 0             # clubs with at least one usable reading
    listed: int = 0            # (source, club, player) rows on wanted clubs
    matched: int = 0           # ...of which mapped to a UEFA player
    implied: int = 0           # implied-bench readings added
    unmatched: list[str] = field(default_factory=list)
    unmapped_clubs: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def match_rate(self) -> float:
        return self.matched / self.listed if self.listed else 0.0

    def summary(self) -> str:
        if not self.sources:
            why = "; ".join(f"{k}: {v}" for k, v in self.failed.items())
            why = why or self.note or "no sources"
            return f"predicted line-ups: none used ({why})"
        tail = f"; failed {', '.join(sorted(self.failed))}" if self.failed else ""
        return (f"predicted line-ups: {', '.join(self.sources)} -> "
                f"{len(self.probs)} players over {self.clubs} clubs; matched "
                f"{self.matched}/{self.listed} names ({self.match_rate:.0%})"
                f"{tail}")

    def as_json(self) -> dict:
        return {"sources": self.sources, "failed": sorted(self.failed),
                "players": len(self.probs), "clubs": self.clubs,
                "matched": self.matched, "listed": self.listed,
                "matchRate": round(self.match_rate, 3), "note": self.note}


def start_probabilities(
    players: list[Player],
    teams: list[Team],
    *,
    clubs: set[int] | None = None,
    data: dict[str, dict[str, dict[str, float]]] | None = None,
    failed: dict[str, str] | None = None,
) -> LineupReport:
    """P(start) per UEFA player id: the mean of every source's calibrated reading.

    `clubs` restricts to the clubs whose next match IS the UCL match (see
    `fresh_clubs`); `data` skips the network, for tests.
    """
    if data is None:
        data, failed = fetch_all()
    rep = LineupReport(sources=sorted(data), failed=dict(failed or {}))
    roster: dict[int, list[Player]] = {}
    for p in players:
        roster.setdefault(p.team_id, []).append(p)

    readings: dict[int, list[float]] = {}
    seen_clubs: set[int] = set()
    for src in sorted(data):
        payload = data[src]
        club_ids = map_clubs(payload, teams)
        ignore = IGNORE_LEVELS.get(src, frozenset())
        for club, all_rows in payload.items():
            tid = club_ids.get(club)
            if tid is None:
                rep.unmapped_clubs.append(f"{club} ({src})")
                continue
            if clubs is not None and tid not in clubs:
                continue
            rows = {n: v for n, v in all_rows.items() if v not in ignore}
            found = match_club(rows, roster.get(tid, []))
            rep.listed += len(rows)
            rep.matched += len(found)
            rep.unmatched += [f"{club}: {n} ({src})" for n in rows
                              if n not in found]
            for name, pid in found.items():
                readings.setdefault(pid, []).append(calibrate(rows[name]))
                seen_clubs.add(tid)
            starters = [n for n, v in rows.items() if v >= STARTER_LEVEL]
            if len(starters) >= XI_SIZE and all(n in found for n in starters):
                named = set(found.values())
                for p in roster.get(tid, []):
                    if p.id not in named:
                        readings.setdefault(p.id, []).append(
                            calibrate(IMPLIED_BENCH))
                        rep.implied += 1
    rep.probs = {pid: sum(v) / len(v) for pid, v in readings.items()}
    rep.clubs = len(seen_clubs)
    return rep
