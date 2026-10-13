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
from dataclasses import dataclass, replace
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
    # P(starts), where we can estimate it. Gates and captaincy ask "will he
    # START", which expected minutes answers badly: a nailed winger subbed off
    # at 70' every week has 70 expected minutes and used to fail a 70-minute XI
    # gate that a centre-back sails through (docs/GAPS.md #3).
    p_start: float | None = None

    @property
    def trusted(self) -> bool:
        return self.source.observed

    @property
    def start_equiv(self) -> float:
        """Minutes-scale number for the XI gate, driven by P(start) when known.

        A certain starter maps to 84, a certain non-starter to 12, so the
        existing 70/60/45-minute gates read as P(start) ~ 0.81/0.67/0.46.
        """
        if self.p_start is None:
            return self.expected
        return self.p_start * START_MINUTES + (1 - self.p_start) * SUB_MINUTES


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
        return self.minutes.trusted and self.minutes.start_equiv >= 60

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

# A nailed starter still averages ~82% of his club's minutes (late subs, the odd
# rest), so a minutes SHARE converts to P(start) by dividing by this.
NAILED_SHARE = 0.82
START_MINUTES, SUB_MINUTES = 84.0, 12.0
# Last season's UCL at the SAME club, as a prior. UEFA zeroes every cumulative
# stat when MD1 locks, so from MD2 the live feed holds one matchday per player;
# without this the projection was a price guess plus one match of noise.
# Per minute it counts half as much as this season's; in the minutes blend the
# whole prior season is worth this many matchdays of current evidence.
PRIOR_SEASON_WEIGHT = 0.5
PRIOR_SEASON_MATCHDAYS = 3.0


@dataclass(frozen=True)
class PriorSeason:
    """One player's previous-season UCL totals, at the club he is at now."""
    points: int
    minutes: int
    club_peak_minutes: float       # his club's iron man that season

    @property
    def share(self) -> float:
        return (min(1.0, self.minutes / self.club_peak_minutes)
                if self.club_peak_minutes > 0 else 0.0)


def p_start_from_share(share: float) -> float:
    return max(0.0, min(1.0, share / NAILED_SHARE))


def observed_minutes_needed(team_peak_minutes: float) -> float:
    """How many minutes count as a real sample, given the season's length."""
    return min(TRUSTED_MINUTES, TRUSTED_SHARE * max(0.0, team_peak_minutes))


def _positional_base(players: list[Player],
                     prior: dict[int, PriorSeason] | None = None
                     ) -> dict[str, float]:
    """Median points/90 per position among players with a real sample.

    Early in a season nobody has 450 minutes, so last season's totals stand in
    -- otherwise every position fell back to the same flat 3.0.
    """
    out = {}
    for pos in ("GK", "DEF", "MID", "FWD"):
        rates = [p.total_points / (p.minutes / 90)
                 for p in players if p.pos == pos and p.minutes >= 450]
        if not rates and prior:
            rates = [prior[p.id].points / (prior[p.id].minutes / 90)
                     for p in players
                     if p.pos == pos and p.id in prior and prior[p.id].minutes >= 450]
        out[pos] = median(rates) if rates else 3.0
    return out


def shrunk_points_per90(p: Player, base: float,
                        prior: PriorSeason | None = None) -> float:
    """Rate regressed toward the positional mean by sample size.

    A player with no minutes lands on the prior; a player with a full season
    keeps almost all of his own rate. Last season at the same club, when
    known, enters as down-weighted extra evidence.
    """
    base_prior = base * (0.6 + 0.4 * min(2.0, p.value / 8.0))
    pts, mins = float(p.total_points), float(p.minutes)
    if prior is not None and prior.minutes > 0:
        pts += PRIOR_SEASON_WEIGHT * prior.points
        mins += PRIOR_SEASON_WEIGHT * prior.minutes
    return ((pts + base_prior * (PSEUDO_MINUTES / 90))
            / ((mins + PSEUDO_MINUTES) / 90))


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
    predicted_lineups: dict[int, float] | None = None,
    domestic_minutes: dict[int, float] | None = None,
    changed_club: bool = False,
    prior: PriorSeason | None = None,
) -> Minutes:
    """Expected minutes for one player, preferring observed sources.

    Order of preference is the whole point: a predicted XI for this match beats
    current-season domestic minutes, which beat prior UCL minutes at the same
    club, which beat a guess from price. The predicted XI is BLENDED with the
    best of the others rather than replacing it (`blend_lineup`). A doubtful
    ("D") player has minutes and P(start) halved, whatever the source.
    """
    if p.status in {"NIS", "I", "S"}:
        return Minutes(0.0, MinutesSource.OBSERVED_UCL, f"status={p.status}")

    m = _observed_minutes(p, team_peak_minutes,
                          domestic_minutes=domestic_minutes,
                          changed_club=changed_club, prior=prior)
    if predicted_lineups is not None and p.id in predicted_lineups:
        m = blend_lineup(m, float(predicted_lineups[p.id]))
    if p.status == "D":
        m = replace(m, expected=m.expected * 0.5,
                    p_start=None if m.p_start is None else m.p_start * 0.5)
    return m


# How far a predicted line-up outweighs the season's evidence. The line-up is
# about THIS match and the record is about earlier ones, so the line-up leads;
# but it is a prediction, and a nailed starter "benched" by one scrape should
# not vanish. Against a guess from price the line-up is nearly everything.
LINEUP_WEIGHT = 0.8
LINEUP_WEIGHT_VS_IMPUTED = 0.9


def blend_lineup(observed: Minutes, p_lineup: float) -> Minutes:
    """Mix a predicted-line-up P(start) into an observed minutes estimate.

    P(start) = w * line-up + (1 - w) * the record's own P(start), and expected
    minutes follow from it with the player's own minutes-when-starting (a
    centre-back plays 90, a winger is hooked at 70) and SUB_MINUTES for a
    non-start -- itself already an EXPECTED figure: about half of benched
    players get on, for ~25 minutes. Without the player's own start length
    every predicted starter would drift to 84, moving defenders and attackers
    for no reason but the formula.
    """
    p_lineup = max(0.0, min(1.0, p_lineup))
    span = START_MINUTES - SUB_MINUTES
    if observed.p_start is not None:
        p_obs = observed.p_start
    else:
        p_obs = max(0.0, min(1.0, (observed.expected - SUB_MINUTES) / span))
    start_len = START_MINUTES      # a fringe record says little about starts
    if p_obs >= 0.5:
        start_len = (observed.expected - (1 - p_obs) * SUB_MINUTES) / p_obs
        start_len = max(60.0, min(90.0, start_len))
    w = LINEUP_WEIGHT if observed.trusted else LINEUP_WEIGHT_VS_IMPUTED
    p = w * p_lineup + (1 - w) * p_obs
    return Minutes(
        p * start_len + (1 - p) * SUB_MINUTES,
        MinutesSource.PREDICTED_LINEUP,
        f"predicted line-ups P(start) {p_lineup:.0%}, "
        f"blended with {observed.source.value} ({p_obs:.0%})",
        p_start=p,
    )


def _observed_minutes(
    p: Player,
    team_peak_minutes: float,
    *,
    domestic_minutes: dict[int, float] | None = None,
    changed_club: bool = False,
    prior: PriorSeason | None = None,
) -> Minutes:
    """The best season-level estimate, before line-ups and before doubt."""
    if domestic_minutes is not None and p.id in domestic_minutes:
        return Minutes(
            min(90.0, domestic_minutes[p.id]),
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
            price_prior_minutes(p.value),
            MinutesSource.IMPUTED_PRICE,
            "changed clubs since last season -- prior minutes were elsewhere",
        )

    # Last season at this club, blended with this season's share. This
    # season's matchdays count one each (a 0 when he did not play is real
    # evidence once his club has played); the prior season counts as
    # PRIOR_SEASON_MATCHDAYS. (`changed_club` already returned above, so a
    # prior here is always same-club.)
    if prior is not None and prior.minutes > 0:
        n_now = team_peak_minutes / 90.0
        share_now = (min(1.0, p.minutes / team_peak_minutes)
                     if team_peak_minutes > 0 else 0.0)
        share = ((share_now * n_now + prior.share * PRIOR_SEASON_MATCHDAYS)
                 / (n_now + PRIOR_SEASON_MATCHDAYS))
        return Minutes(90.0 * share, MinutesSource.OBSERVED_UCL,
                       "UCL minutes share, this season + last at this club",
                       p_start=p_start_from_share(share))

    # team_peak_minutes ~= the club's games * 90, so this normalises share.
    if team_peak_minutes > 0 and p.minutes > 0:
        share = min(90.0, 90.0 * p.minutes / team_peak_minutes)
        if p.minutes >= observed_minutes_needed(team_peak_minutes):
            return Minutes(share, MinutesSource.OBSERVED_UCL,
                           "UCL minutes share",
                           p_start=p_start_from_share(share / 90.0))

    return Minutes(
        price_prior_minutes(p.value),
        MinutesSource.IMPUTED_PRICE,
        "no observed minutes -- guessed from price; cannot captain",
    )


def project(
    players: list[Player],
    teams: list[Team],
    opponents: dict[int, tuple[int, bool, str]],
    *,
    predicted_lineups: dict[int, float] | None = None,
    domestic_minutes: dict[int, float] | None = None,
    moved_clubs: set[int] | None = None,
    strength_adj=None,
    prior_season: dict[int, PriorSeason] | None = None,
) -> list[Projection]:
    """Project every available player for one matchday.

    `opponents` maps team_id -> (opponent_team_id, is_home, kickoff_day).
    `strength_adj(own_team_id, opp_team_id, home) -> float` overrides the crude
    seeding-pot adjustment; pass the Elo-backed one from `strength.py`.
    """
    prior_season = prior_season or {}
    base = _positional_base(players, prior_season)
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
            prior=prior_season.get(p.id),
        )
        rate = shrunk_points_per90(p, base[p.pos], prior_season.get(p.id))
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

    # A predicted XI leads everything -- but is blended, not obeyed: benched
    # by every source, a settled starter projects low and is still not zero.
    m3 = estimate_minutes(settled, 1000.0, predicted_lineups={2: 0.0})
    assert m3.source is MinutesSource.PREDICTED_LINEUP and m3.trusted
    assert m3.expected < 30, "benched in a predicted XI must project low"
    assert m3.p_start is not None and 0.0 < m3.p_start < 0.3, m3.p_start
    # ...and a price-guess player named in the XI becomes a likely starter.
    guess = estimate_minutes(transferred, 1500.0, predicted_lineups={1: 0.9})
    assert guess.source is MinutesSource.PREDICTED_LINEUP
    assert guess.start_equiv >= 70, guess

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
