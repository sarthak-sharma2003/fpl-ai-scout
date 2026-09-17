"""Offline unit tests for uclscout.feeds. No network calls."""
from datetime import UTC

import pytest

from uclscout.feeds import FeedError, _req, parse_uefa_time


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
