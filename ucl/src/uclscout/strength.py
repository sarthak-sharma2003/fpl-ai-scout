"""Team strength from UEFA's own historical results.

The seeding pot (1-4) that `project.py` started with is a season-start prior: it
cannot see form, a manager change, or that a Pot 3 club is actually good. This
module replaces it with Elo fitted on real Champions League results.

WHY NOT ClubElo: its Fixtures API returns "Fixtures API deactivated" and the
per-club endpoint 502s (checked 2026-09-08). Only the HTML site responds, and
scraping it is a fragile dependency for something we can compute ourselves.

WHY NOT Dixon-Coles: the FPL project fitted DC per-club and a promoted side with
two clean sheets produced defence = -12.1, i.e. certainty of shutting everyone
out. Elo with a pot-based prior degrades gracefully for clubs with few matches,
which is most of them -- a club plays 8-17 UCL games a season at most.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .feeds import SEASONS, fetch, parse_uefa_time

K = 24.0                 # Elo update rate; UCL samples are small, keep it low
HOME_ADV = 55.0          # Elo points
REGRESS_TO_MEAN = 0.25   # pull toward 1500 between seasons
BASE = 1500.0
# Seeding-pot priors, from where pots sit on the fitted scale.
POT_PRIOR = {1: 1720.0, 2: 1620.0, 3: 1540.0, 4: 1450.0}
# Elo difference -> expected goals. Calibrated so a level tie ~1.35 each,
# which is the long-run UCL average.
MEAN_GOALS = 1.35
GOALS_PER_ELO = 0.0016


@dataclass(frozen=True)
class Result:
    date: str
    home_id: int
    away_id: int
    home_goals: int
    away_goals: int
    season: str
    # Cross-league calibration weight. Only continental matches tell you how two
    # LEAGUES compare; domestic ones only order clubs within one. Fitting both at
    # full weight put Bodo/Glimt 2nd and AEK Athens 5th in Europe, because
    # dominating a weak league looks identical to dominating a strong one when
    # nothing anchors the scales together.
    weight: float = 1.0


def historical_results(tours: list[int]) -> list[Result]:
    """Every finished UCL match with a score, across the archived seasons."""
    out: list[Result] = []
    for tour in tours:
        try:
            payload = fetch(f"fixtures/fixtures_{tour}_en.json")
        except Exception:                                    # noqa: BLE001
            continue
        for md in payload["data"]["value"]:
            for m in md.get("match") or []:
                hs, as_ = str(m.get("htScore", "")), str(m.get("atScore", ""))
                if not hs.strip().isdigit() or not as_.strip().isdigit():
                    continue                                  # unplayed
                out.append(Result(
                    date=parse_uefa_time(m["dateTime"]).isoformat(),
                    home_id=int(m["htId"]), away_id=int(m["atId"]),
                    home_goals=int(hs), away_goals=int(as_),
                    season=SEASONS.get(tour, str(tour)),
                ))
    return sorted(out, key=lambda r: r.date)


def fit_elo(results: list[Result], pots: dict[int, int] | None = None
            ) -> dict[int, float]:
    """Sequential Elo over historical results, seeded from seeding pots."""
    pots = pots or {}
    rating: dict[int, float] = {}

    def get(tid: int) -> float:
        if tid not in rating:
            rating[tid] = POT_PRIOR.get(pots.get(tid, 3), BASE)
        return rating[tid]

    season = None
    for r in results:
        if season is not None and r.season != season:
            # between seasons, regress toward the mean: squads turn over
            for t, v in rating.items():
                rating[t] = BASE + (v - BASE) * (1 - REGRESS_TO_MEAN)
        season = r.season

        rh, ra = get(r.home_id), get(r.away_id)
        exp_h = 1.0 / (1.0 + 10 ** (-((rh + HOME_ADV) - ra) / 400.0))
        score_h = (1.0 if r.home_goals > r.away_goals
                   else 0.0 if r.home_goals < r.away_goals else 0.5)
        # margin-of-victory multiplier, capped so 6-0 games don't dominate
        mov = math.log1p(abs(r.home_goals - r.away_goals)) + 1.0
        delta = K * r.weight * mov * (score_h - exp_h)
        rating[r.home_id] = rh + delta
        rating[r.away_id] = ra - delta
    return rating


def expected_goals(att_elo: float, def_elo: float, home: bool
                   ) -> float:
    """Goals the attacking side is expected to score. Floored, never negative."""
    diff = (att_elo + (HOME_ADV if home else -HOME_ADV)) - def_elo
    return max(0.25, MEAN_GOALS + GOALS_PER_ELO * diff)


def clean_sheet_prob(opp_expected_goals: float) -> float:
    """P(opponent scores zero) under a Poisson goal model."""
    return math.exp(-opp_expected_goals)


def win_prob(home_elo: float, away_elo: float) -> float:
    return 1.0 / (1.0 + 10 ** (-((home_elo + HOME_ADV) - away_elo) / 400.0))


def rating_or_prior(elo: dict[int, float], team_id: int, pot: int) -> float:
    return elo.get(team_id, POT_PRIOR.get(pot, BASE))


def demo():
    """Self-check on synthetic results, then a live sanity pass."""
    # A club that wins everything must end up rated above one that loses.
    res = [Result(f"2024-01-{d:02d}", 1, 2, 3, 0, "2024-25") for d in range(1, 13)]
    e = fit_elo(res)
    assert e[1] > e[2] + 200, (e[1], e[2])

    # Home advantage is directional, and expected goals stay positive.
    assert win_prob(1500, 1500) > 0.5
    assert expected_goals(1400, 1800, home=False) >= 0.25
    assert expected_goals(1800, 1400, True) > expected_goals(1400, 1800, False)
    # Clean sheets get harder as the opponent gets better.
    assert clean_sheet_prob(0.5) > clean_sheet_prob(2.5)
    assert 0.0 < clean_sheet_prob(1.35) < 1.0

    # A down-weighted match must move ratings less than a full-weight one.
    light = [Result(f"2024-01-{d:02d}", 5, 6, 3, 0, "2024-25", weight=0.2)
             for d in range(1, 13)]
    el = fit_elo(light)
    assert abs(el[5] - BASE) < abs(e[1] - BASE), "weight is not applied"

    # Between-season regression must actually pull toward the mean.
    two = res + [Result("2025-01-01", 3, 4, 1, 1, "2025-26")]
    e2 = fit_elo(two)
    assert abs(e2[1] - BASE) < abs(e[1] - BASE), "no between-season regression"

    print("strength.py self-check OK")


if __name__ == "__main__":
    demo()
