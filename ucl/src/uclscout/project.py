"""Player projections, with honest provenance on the input that matters.

WHY THIS MODULE IS SHAPED LIKE THIS
-----------------------------------
The first version of this projector captained Anthony Gordon for MD1 2026/27.
Gordon had just moved to Barcelona for EUR80m, was benched for their La Liga
opener, and had 244 league minutes. The projector gave him 79 expected minutes
because his prior-season UCL rates were Newcastle's and the club-normalisation
broke, so it fell back to a price prior: (value - 4) / 7 * 90.

There WAS a minutes gate, and it worked exactly as designed. It just consumed a
number nobody had validated. A guard is only as good as its input.

So minutes carry a `MinutesSource`, and imputed minutes are second-class:
they cannot take an XI place over an observed alternative, and they can never
captain. If we are guessing, the guess is visible and it is constrained.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from statistics import median

from .feeds import Player, Team

# Shrinkage: pseudo-observations pulling a small-sample rate toward the
# positional mean. 270 min = three full matches.
# 540 = six matches. At 270 a single 90-minute haul still out-rated a
# full-season performer, which is exactly what shrinkage exists to prevent.
PSEUDO_MINUTES = 540.0
# Opponent adjustment, from UEFA seeding pot until real Elo lands.
POT_COEF = 0.09
HOME_EDGE = 0.45
ADJ_FLOOR, ADJ_CEIL = 0.70, 1.30


class MinutesSource(enum.StrEnum):
    """Where an expected-minutes number came from. Ordered best to worst."""
    PREDICTED_LINEUP = "predicted_lineup"    # a published XI for THIS match
    OBSERVED_DOMESTIC = "observed_domestic"  # current-season league minutes
    OBSERVED_UCL = "observed_ucl"            # prior UCL minutes, same club
    IMPUTED_PRICE = "imputed_price"          # a guess from price. Constrained.

    @property
    def observed(self) -> bool:
        return self is not MinutesSource.IMPUTED_PRICE


@dataclass(frozen=True)
class Minutes:
    expected: float
    source: MinutesSource
    note: str = ""

    @property
    def trusted(self) -> bool:
        return self.source.observed


@dataclass(frozen=True)
class Projection:
    player: Player
    minutes: Minutes
    points_per90: float
    opponent_adj: float
    points: float
    opponent_id: int | None
    home: bool | None
    kickoff_day: str | None

    @property
    def can_captain(self) -> bool:
        """Never hand the armband to a player whose minutes we invented."""
        return self.minutes.trusted and self.minutes.expected >= 60

    @property
    def value_per_million(self) -> float:
        return self.points / self.player.value if self.player.value else 0.0


def price_prior_minutes(value: float) -> float:
    """Last-resort minutes guess. Deliberately the WORST source available."""
    return max(0.0, min(85.0, (value - 4.0) / 7.0 * 90.0))


# Minutes a player needs before his own share is trusted over a guess from
# price. An absolute count for a full prior season; a share of the sample that
# ACTUALLY EXISTS early in one. UEFA zeroes every cumulative stat the moment
# MD1 locks, and no one can hold 270 minutes of a season one matchday old, so
# an absolute floor marks the entire game imputed exactly when the first real
# starts land -- which left zero eligible keepers and zero defenders, and an
# infeasible optimizer (docs/GAPS.md P0).
TRUSTED_MINUTES = 270.0
TRUSTED_SHARE = 0.6


def observed_minutes_needed(team_peak_minutes: float) -> float:
    """How many minutes count as a real sample, given the season's length."""
    return min(TRUSTED_MINUTES, TRUSTED_SHARE * max(0.0, team_peak_minutes))


def _positional_base(players: list[Player]) -> dict[str, float]:
    """Median points/90 per position among players with a real sample."""
    out = {}
    for pos in ("GK", "DEF", "MID", "FWD"):
        rates = [p.total_points / (p.minutes / 90)
                 for p in players if p.pos == pos and p.minutes >= 450]
        out[pos] = median(rates) if rates else 3.0
    return out


def shrunk_points_per90(p: Player, base: float) -> float:
    """Rate regressed toward the positional mean by sample size.

    A player with no minutes lands on the prior; a player with a full season
    keeps almost all of his own rate.
    """
    prior = base * (0.6 + 0.4 * min(2.0, p.value / 8.0))
    return ((p.total_points + prior * (PSEUDO_MINUTES / 90))
            / ((p.minutes + PSEUDO_MINUTES) / 90))


def opponent_adjustment(own_pot: int, opp_pot: int, home: bool) -> float:
    """Crude strength gap from UEFA seeding pots. Replaced by Elo in phase 2.

    ponytail: pot is a coarse, season-start prior -- it cannot see form or
    injuries. It is here because it costs nothing and exists at MD1; swap in
    ClubElo once ingested and delete this.
    """
    gap = (5 - own_pot) - (5 - opp_pot) + (HOME_EDGE if home else -HOME_EDGE)
    return max(ADJ_FLOOR, min(ADJ_CEIL, 1 + POT_COEF * gap))


def estimate_minutes(
    p: Player,
    team_peak_minutes: float,
    *,
    predicted_lineups: dict[int, bool] | None = None,
    domestic_minutes: dict[int, float] | None = None,
    changed_club: bool = False,
) -> Minutes:
    """Expected minutes for one player, preferring observed sources.

    Order of preference is the whole point: a published XI beats current-season
    domestic minutes, which beat prior UCL minutes at the same club, which beat
    a guess from price.
    """
    if p.status in {"NIS", "I", "S"}:
        return Minutes(0.0, MinutesSource.OBSERVED_UCL, f"status={p.status}")

    doubt = 0.5 if p.status == "D" else 1.0

    if predicted_lineups is not None and p.id in predicted_lineups:
        starting = predicted_lineups[p.id]
        return Minutes(
            (82.0 if starting else 15.0) * doubt,
            MinutesSource.PREDICTED_LINEUP,
            "published XI" if starting else "named on bench",
        )

    if domestic_minutes is not None and p.id in domestic_minutes:
        return Minutes(
            min(90.0, domestic_minutes[p.id]) * doubt,
            MinutesSource.OBSERVED_DOMESTIC,
            "current-season league minutes per appearance",
        )

    # Prior UCL minutes only mean anything if he was at THIS club for them.
    # UEFA reports a player's CURRENT club next to his PRIOR stats, with no
    # club-of-record field -- which is exactly how Gordon's Newcastle minutes
    # got read as Barcelona minutes. `changed_club` comes from diffing last
    # season's archive, and demotes him to a guess, where he belongs.
    if changed_club:
        return Minutes(
            price_prior_minutes(p.value) * doubt,
            MinutesSource.IMPUTED_PRICE,
            "changed clubs since last season -- prior minutes were elsewhere",
        )

    # team_peak_minutes ~= the club's games * 90, so this normalises share.
    if team_peak_minutes > 0 and p.minutes > 0:
        share = min(90.0, 90.0 * p.minutes / team_peak_minutes)
        if p.minutes >= observed_minutes_needed(team_peak_minutes):
            return Minutes(share * doubt, MinutesSource.OBSERVED_UCL,
                           "UCL minutes share")

    return Minutes(
        price_prior_minutes(p.value) * doubt,
        MinutesSource.IMPUTED_PRICE,
        "no observed minutes -- guessed from price; cannot captain",
    )


def project(
    players: list[Player],
    teams: list[Team],
    opponents: dict[int, tuple[int, bool, str]],
    *,
    predicted_lineups: dict[int, bool] | None = None,
    domestic_minutes: dict[int, float] | None = None,
    moved_clubs: set[int] | None = None,
    strength_adj=None,
) -> list[Projection]:
    """Project every available player for one matchday.

    `opponents` maps team_id -> (opponent_team_id, is_home, kickoff_day).
    `strength_adj(own_team_id, opp_team_id, home) -> float` overrides the crude
    seeding-pot adjustment; pass the Elo-backed one from `strength.py`.
    """
    base = _positional_base(players)
    pot = {t.id: t.pot for t in teams}
    peak: dict[int, float] = {}
    for p in players:
        peak[p.team_id] = max(peak.get(p.team_id, 0.0), float(p.minutes))

    out = []
    for p in players:
        if not p.available or p.team_id not in opponents:
            continue
        opp_id, home, day = opponents[p.team_id]
        mins = estimate_minutes(
            p, peak.get(p.team_id, 0.0),
            predicted_lineups=predicted_lineups,
            domestic_minutes=domestic_minutes,
            changed_club=bool(moved_clubs and p.id in moved_clubs),
        )
        rate = shrunk_points_per90(p, base[p.pos])
        adj = (strength_adj(p.team_id, opp_id, home) if strength_adj
               else opponent_adjustment(pot.get(p.team_id, 3),
                                        pot.get(opp_id, 3), home))
        out.append(Projection(
            player=p,
            minutes=mins,
            points_per90=rate,
            opponent_adj=adj,
            points=rate * (mins.expected / 90.0) * adj,
            opponent_id=opp_id,
            home=home,
            kickoff_day=day,
        ))
    return sorted(out, key=lambda x: -x.points)


def demo():
    """Self-check on the Gordon case: the bug that motivated this module."""
    from .feeds import Player as P

    def mk(pid, name, team_id, mins, pts, value, status=""):
        return P(id=pid, name=name, team_id=team_id, team="X", pos="MID",
                 value=value, status=status, selected_pct=1, total_points=pts,
                 minutes=mins, goals=0, assists=0, clean_sheets=0, saves=0,
                 recoveries=0, motm=0, as_of_matchday=1)

    # A pricey player with NO minutes at his current club: the Gordon shape.
    transferred = mk(1, "Just Signed", team_id=99, mins=0, pts=0, value=8.0)
    m = estimate_minutes(transferred, team_peak_minutes=1500.0)
    assert m.source is MinutesSource.IMPUTED_PRICE, m.source
    assert not m.trusted

    # ...and he must not be captainable, however good the projection looks.
    proj = Projection(transferred, m, 12.0, 1.2, 99.0, 5, True, "09/09")
    assert not proj.can_captain, "imputed minutes must never captain"

    # A settled starter at the same club IS trusted.
    settled = mk(2, "Regular", team_id=99, mins=900, pts=60, value=8.0)
    m2 = estimate_minutes(settled, team_peak_minutes=1000.0)
    assert m2.source is MinutesSource.OBSERVED_UCL and m2.trusted
    assert 75 <= m2.expected <= 90, m2.expected

    # One matchday into a season UEFA has zeroed everything, so a 90-minute
    # start IS the entire sample and must count as observed. An absolute
    # 270-minute floor marked the whole game imputed here (docs/GAPS.md P0).
    md1 = estimate_minutes(mk(6, "MD1 Starter", 99, mins=90, pts=6, value=6.0),
                           team_peak_minutes=90.0)
    assert md1.source is MinutesSource.OBSERVED_UCL, md1.source
    assert md1.expected >= 85, md1.expected

    # ...but a cameo in that same matchday is still a guess.
    cameo = estimate_minutes(mk(7, "Cameo", 99, mins=12, pts=1, value=6.0),
                             team_peak_minutes=90.0)
    assert cameo.source is MinutesSource.IMPUTED_PRICE, cameo.source

    # A published XI outranks everything.
    m3 = estimate_minutes(settled, 1000.0, predicted_lineups={2: False})
    assert m3.source is MinutesSource.PREDICTED_LINEUP
    assert m3.expected < 30, "benched in a published XI must project low"

    # A settled starter who CHANGED CLUBS is demoted to a guess: his prior
    # minutes were somewhere else. This is the Gordon case exactly.
    moved = estimate_minutes(settled, 1000.0, changed_club=True)
    assert moved.source is MinutesSource.IMPUTED_PRICE, moved.source
    assert not moved.trusted, "a transferred player's minutes are not observed"

    # Unavailable is zero regardless of price.
    out = mk(3, "Injured", team_id=99, mins=900, pts=60, value=11.0, status="I")
    assert estimate_minutes(out, 1000.0).expected == 0.0

    # Shrinkage: a tiny hot sample must not out-rate a big solid one.
    hot = mk(4, "Hot", 99, mins=90, pts=13, value=6.0)     # one big game
    solid = mk(5, "Solid", 99, mins=1200, pts=96, value=6.0)  # 7.2/90
    assert shrunk_points_per90(hot, 4.7) < shrunk_points_per90(solid, 4.7)

    # Opponent adjustment is bounded and directional.
    assert opponent_adjustment(1, 4, True) > opponent_adjustment(4, 1, False)
    assert ADJ_FLOOR <= opponent_adjustment(4, 1, False) <= ADJ_CEIL

    print("project.py self-check OK "
          "(imputed minutes cannot captain; shrinkage beats hot small samples)")


if __name__ == "__main__":
    demo()
