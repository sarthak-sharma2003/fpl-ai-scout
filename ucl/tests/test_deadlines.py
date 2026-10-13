"""Transfer lock vs the feed's moving `deadline` (2026-10-13: an hour after MD2's
first kick-off the feed's deadline jumped to the next gameday, and the planner
re-advised MD2's transfers)."""
from datetime import UTC, datetime
from types import SimpleNamespace

from uclscout import pipeline
from uclscout.feeds import Match, Matchday


def _md(mid, deadline, kickoffs):
    ms = [Match(id=mid * 100 + i, matchday=mid, kickoff=k, home_id=1, away_id=2,
                lineup_announced=False, status="0") for i, k in enumerate(kickoffs)]
    return Matchday(id=mid, deadline=deadline, subs_allowed=2, is_current=False,
                    is_locked=False, matches=ms)


def _t(day, hh, mm=0):
    return datetime(2026, 10, day, hh, mm, tzinfo=UTC)


MD2 = [_t(13, 16, 45), _t(13, 19), _t(14, 16, 45), _t(14, 19)]
MD3 = [_t(20, 16, 45), _t(21, 19)]


def _ctx(md2_deadline):
    return SimpleNamespace(
        constraints=SimpleNamespace(matchday=2),
        matchdays=[_md(2, md2_deadline, MD2), _md(3, _t(20, 16, 45), MD3)])


def test_transfer_deadline_is_first_kickoff_not_the_feeds_moving_deadline():
    moved = _md(2, _t(14, 16, 45), MD2)          # feed after day 1 began
    assert moved.transfer_deadline == _t(13, 16, 45)
    before = _md(2, _t(13, 16, 45), MD2)
    assert before.transfer_deadline == _t(13, 16, 45)
    # a deadline set earlier than the first kick-off is kept
    early = _md(2, _t(13, 15, 0), MD2)
    assert early.transfer_deadline == _t(13, 15, 0)
    # undrawn: no matches, the feed's deadline is all there is
    assert _md(9, _t(1, 12), []).transfer_deadline == _t(1, 12)


def test_matchday_is_not_reopened_once_its_first_game_has_kicked_off():
    ctx = _ctx(md2_deadline=_t(14, 16, 45))      # what the feed said after 16:45 Tue
    assert pipeline.next_open_matchday(ctx, now=_t(13, 12)) == 2
    assert pipeline.next_open_matchday(ctx, now=_t(13, 17)) == 3     # was 2: the bug
    assert pipeline.next_open_matchday(ctx, now=_t(14, 12)) == 3
    assert pipeline.next_open_matchday(ctx, now=_t(20, 17)) == 4


def test_subs_lock_is_the_next_gamedays_first_kickoff():
    ctx = _ctx(md2_deadline=_t(14, 16, 45))
    assert pipeline.live_matchday(ctx, now=_t(13, 12)) is None        # not started
    m, lock = pipeline.live_matchday(ctx, now=_t(13, 23))
    assert (m.id, lock) == (2, _t(14, 16, 45))                        # Wed 16:45
    assert pipeline.live_matchday(ctx, now=_t(14, 17)) is None        # last gameday began
    assert pipeline.live_matchday(ctx, now=_t(15, 9)) is None
