"""Offline unit tests for uclscout.feeds. No network calls."""
from datetime import UTC

import pytest

from uclscout.feeds import (
    UNAVAILABLE,
    FeedError,
    _req,
    parse_matchdays,
    parse_uefa_time,
)


def test_parse_uefa_time_both_formats_agree():
    a = parse_uefa_time("09/08/2026 18:45:00")
    b = parse_uefa_time("09/08/26 06:45:00 PM")
    assert a == b


def test_parse_uefa_time_returns_aware_utc():
    dt = parse_uefa_time("09/08/2026 18:45:00")
    assert dt.tzinfo is not None
    assert dt.tzinfo == UTC


def test_parse_uefa_time_garbage_raises_feed_error():
    with pytest.raises(FeedError):
        parse_uefa_time("not a timestamp")


def test_req_missing_key_raises_feed_error():
    with pytest.raises(FeedError):
        _req({"a": 1}, "b", "ctx")


def test_req_present_key_returns_value():
    assert _req({"a": 1}, "a", "ctx") == 1


def _fixtures(**match):
    m = {"mId": 1, "dateTime": "10/13/2026 18:45:00", "htId": 10, "atId": 20,
         **match}
    return {"data": {"value": [{"mdId": 2, "deadline": "10/13/2026 18:45:00",
                                "match": [m]}]}}


def test_scores_parse_when_played():
    m = parse_matchdays(_fixtures(htScore="3", atScore="0", matchStatus="2",
                                  isLive=2))[0].matches[0]
    assert (m.home_goals, m.away_goals) == (3, 0)


@pytest.mark.parametrize("extra", [
    {},                                                   # fields absent
    {"htScore": "", "atScore": "", "matchStatus": "0"},   # not kicked off
    {"htScore": "1:1", "atScore": "1:4"},                 # aggregate-style junk
    {"htScore": "1", "atScore": "0", "isLive": 1},        # in progress
])
def test_odd_or_missing_scores_never_raise(extra):
    m = parse_matchdays(_fixtures(**extra))[0].matches[0]
    assert m.home_goals is None and m.away_goals is None


def test_eliminated_status_is_unavailable():
    assert "E" in UNAVAILABLE
