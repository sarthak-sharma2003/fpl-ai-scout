from datetime import UTC, datetime
from types import SimpleNamespace

from fplscout import db
from fplscout.ingest import predeadline


def _bootstrap(deadline):
    past = datetime(2026, 10, 10, 10, tzinfo=UTC)
    ev = [SimpleNamespace(id=6, is_next=False, deadline_time=past),
          SimpleNamespace(id=7, is_next=True, deadline_time=deadline)]
    els = [
        SimpleNamespace(code=101, status="a", chance_of_playing_next_round=None, news="",
                        ep_next="5.5"),
        SimpleNamespace(code=102, status="i", chance_of_playing_next_round=0,
                        news="Knee injury", ep_next="0.0"),
        SimpleNamespace(code=103, status="d", chance_of_playing_next_round=75,
                        news="Knock", ep_next=None),
    ]
    return SimpleNamespace(events=ev, elements=els)


def test_live_snapshot_encodes_like_the_training_history():
    con = db.connect(":memory:")
    deadline = datetime(2026, 10, 17, 10, tzinfo=UTC)
    n = predeadline.snapshot_live(con, _bootstrap(deadline), "2026-27",
                                  now=datetime(2026, 10, 15, 12, tzinfo=UTC))
    assert n == 3
    rows = {r[0]: r[1:] for r in con.execute(
        "SELECT code, gw, status_risk, chance_playing, has_news, ep_next "
        "FROM predeadline_signals").fetchall()}
    assert rows[101] == (7, 0, None, 0.0, 5.5)
    assert rows[102] == (7, 3, 0.0, 1.0, 0.0)     # a real 0 ep_next stays 0
    assert rows[103] == (7, 1, 75.0, 1.0, None)


def test_no_snapshot_after_the_deadline():
    con = db.connect(":memory:")
    deadline = datetime(2026, 10, 17, 10, tzinfo=UTC)
    n = predeadline.snapshot_live(con, _bootstrap(deadline), "2026-27",
                                  now=datetime(2026, 10, 17, 10, 1, tzinfo=UTC))
    assert n == 0
    assert con.execute("SELECT count(*) FROM predeadline_signals").fetchone()[0] == 0


def test_later_snapshot_before_deadline_replaces_earlier():
    con = db.connect(":memory:")
    deadline = datetime(2026, 10, 17, 10, tzinfo=UTC)
    b = _bootstrap(deadline)
    predeadline.snapshot_live(con, b, "2026-27", now=datetime(2026, 10, 15, tzinfo=UTC))
    b.elements[2].chance_of_playing_next_round = 25
    predeadline.snapshot_live(con, b, "2026-27", now=datetime(2026, 10, 16, tzinfo=UTC))
    assert con.execute(
        "SELECT chance_playing FROM predeadline_signals WHERE code = 103").fetchone()[0] == 25.0
