"""Last season at the same club as a prior, and P(start) driving the XI gate."""
from uclscout.feeds import Player
from uclscout.project import (
    MinutesSource,
    PriorSeason,
    estimate_minutes,
    shrunk_points_per90,
)


def _p(mins, pts, value=8.0, pid=1):
    return Player(id=pid, name="X", team_id=1, team="T", pos="MID", value=value,
                  status="", selected_pct=1, total_points=pts, minutes=mins,
                  goals=0, assists=0, clean_sheets=0, saves=0, recoveries=0,
                  motm=0, as_of_matchday=2)


def test_prior_season_lifts_a_proven_scorer_after_a_quiet_md1():
    quiet = _p(90, 2)
    star = PriorSeason(points=110, minutes=1100, club_peak_minutes=1200)
    assert shrunk_points_per90(quiet, 4.0, star) > shrunk_points_per90(quiet, 4.0)


def test_benched_md1_but_nailed_last_season_is_still_a_likely_starter():
    # 20-minute cameo at MD1 alone is "imputed"; with a full prior season at
    # this club he is observed and most likely starts
    cameo = _p(20, 1)
    alone = estimate_minutes(cameo, 90.0)
    assert alone.source is MinutesSource.IMPUTED_PRICE
    nailed = PriorSeason(points=60, minutes=1000, club_peak_minutes=1080)
    m = estimate_minutes(cameo, 90.0, prior=nailed)
    assert m.source is MinutesSource.OBSERVED_UCL
    assert m.p_start is not None and m.p_start > 0.75


def test_subbed_winger_clears_the_gate_on_start_probability():
    # GAPS #3: a starter subbed at ~70' every week has a 0.73 minutes share.
    # Expected minutes (66) fail a 70 gate; P(start) must not.
    winger = _p(1141, 80)
    m = estimate_minutes(winger, 1560.0)
    assert m.expected < 70
    assert m.start_equiv >= 70
