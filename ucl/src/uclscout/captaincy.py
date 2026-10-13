"""Captaincy with the mid-matchday switch priced in.

UCL lets you move the armband ONCE per matchday, irreversibly, to an XI player
who has not played yet. So the pre-deadline captain is not simply the highest
mean: a captain who plays on an EARLIER day carries an option -- if he blanks,
the armband moves to the best player still to play. Captaining X is worth, in
single points (the armband doubles both sides, so nothing changes),

    E[max(X, mu_L)]    L = the best captainable XI player kicking off LATER

since you switch exactly when X scores below mu_L, and then expect mu_L. A
captain on the matchday's last day has no switch left: he is worth mu_X. As
E[max(X, mu_L)] >= mu_L, an early captain with the best later player as cover
is never worse than captaining that player outright -- which is why top
managers switch 44.6% of the time (plan.matchday_checklist). Not modelled: a
switch is a manual change and so turns auto-subs off for the matchday; you are
then making the subs by hand anyway.

DISTRIBUTION: X ~ Normal(mu, sd = 0.9 * mu + 1). Fantasy points are a
blank-heavy, right-skewed count and nothing here models per-player variance yet
(docs/GAPS.md #13), so this is a stand-in: spread grows with the mean (an
8-point striker blanks often, a 2-point defender rarely hauls) and the +1 stops
a low projection looking certain. It overstates the upside, so `value` is
optimistic; what is used is the ranking between early candidates and the rule
-- switch when the captain scores below mu_L -- which holds under any
distribution. Only the region above mu_L enters, so the negative tail is moot.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from .project import Projection

SD_SLOPE, SD_FLOOR = 0.9, 1.0


def points_sd(mu: float) -> float:
    return SD_SLOPE * max(0.0, mu) + SD_FLOOR


def expected_max(mu: float, sd: float, c: float) -> float:
    """E[max(X, c)] for X ~ Normal(mu, sd), in closed form."""
    if sd <= 0:
        return max(mu, c)
    z = (mu - c) / sd
    cdf = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    pdf = math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
    return c + (mu - c) * cdf + sd * pdf


@dataclass(frozen=True)
class CaptainSwitch:
    captain: Projection
    switch_to: Projection | None     # best captainable XI player on a later day
    switch_if_below: float | None    # captain's own points: switch if fewer
    value: float                     # E[captain's points], the switch included

    def as_json(self) -> dict:
        return {
            "captainId": self.captain.player.id,
            "captainDay": self.captain.kickoff_day,
            "switchTo": self.switch_to.player.id if self.switch_to else None,
            "switchDay": self.switch_to.kickoff_day if self.switch_to else None,
            "switchIfBelow": (round(self.switch_if_below, 1)
                              if self.switch_if_below is not None else None),
            "expected": round(self.value, 2),
        }


def plan_captaincy(xi: list[Projection]) -> CaptainSwitch | None:
    """The pre-deadline captain worth most once the later switch is counted.

    Only captainable players (observed minutes, likely to start) qualify, as
    the armband or as its switch target; if none in the XI do, all may.
    """
    dated = [p for p in xi if p.kickoff_day]
    pool = [p for p in dated if p.can_captain] or dated
    best: tuple[tuple[float, float], CaptainSwitch] | None = None
    for x in pool:
        later = [q for q in pool if q.kickoff_day > x.kickoff_day]
        target = max(later, key=lambda q: q.points, default=None)
        value = (expected_max(x.points, points_sd(x.points), target.points)
                 if target else x.points)
        key = (value, x.points)
        if best is None or key > best[0]:
            best = (key, CaptainSwitch(x, target,
                                       target.points if target else None,
                                       value))
    return best[1] if best else None


def _day(iso: str | None) -> str:
    try:
        return date.fromisoformat(iso or "").strftime("%a")
    except ValueError:
        return iso or "?"


def checklist_line(cs: CaptainSwitch | None) -> str | None:
    """The one operational instruction the switch needs, or None."""
    if cs is None or cs.switch_to is None or cs.switch_if_below is None:
        return None
    floor = max(0, math.ceil(cs.switch_if_below) - 1)
    return (f"Captain {cs.captain.player.name} ({_day(cs.captain.kickoff_day)}). "
            f"If he scores {floor} or fewer, switch the armband to "
            f"{cs.switch_to.player.name} before {_day(cs.switch_to.kickoff_day)}"
            "'s kick-offs — the switch is one-way.")


def demo():
    """Self-check: the switch option beats a slightly higher late mean."""
    from .feeds import Player
    from .project import Minutes, MinutesSource

    def mk(i, pts, day, trusted=True):
        pl = Player(id=i, name=f"P{i}", team_id=i, team="T", pos="FWD",
                    value=9.0, status="", selected_pct=1, total_points=0,
                    minutes=900, goals=0, assists=0, clean_sheets=0, saves=0,
                    recoveries=0, motm=0, as_of_matchday=2)
        src = (MinutesSource.OBSERVED_UCL if trusted
               else MinutesSource.IMPUTED_PRICE)
        return Projection(pl, Minutes(85, src, p_start=0.95), 6.0, 1.0, pts,
                          1, True, day)

    tue, wed = "2026-10-13", "2026-10-14"
    early, late = mk(1, 7.0, tue), mk(2, 7.5, wed)
    cs = plan_captaincy([early, late, mk(3, 3.0, wed)])
    assert cs is not None and cs.captain is early, cs
    assert cs.switch_to is late and cs.switch_if_below == 7.5
    assert cs.value > late.points, "the option must be worth something"

    # with no later day there is nothing to switch to: captain the best mean
    one_day = plan_captaincy([mk(1, 7.0, tue), mk(2, 7.5, tue)])
    assert one_day.captain.player.id == 2 and one_day.switch_to is None

    # an imputed-minutes player is never the armband or the switch target
    guarded = plan_captaincy([early, mk(9, 20.0, wed, trusted=False)])
    assert guarded.captain is early and guarded.switch_to is None

    line = checklist_line(cs)
    assert line and "7 or fewer" in line and "P2" in line, line
    assert abs(expected_max(5.0, 1e-9, 3.0) - 5.0) < 1e-6
    print(f"captaincy.py self-check OK — captain P1 (Tue, mean 7.0) worth "
          f"{cs.value:.1f} with the switch vs 7.5 for the Wednesday striker")


if __name__ == "__main__":
    demo()
