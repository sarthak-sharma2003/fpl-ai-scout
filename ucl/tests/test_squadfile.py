from datetime import UTC, datetime

from uclscout import squadfile
from uclscout.squadfile import HeldSquad


def _plan(md=2, gen="2026-10-13T14:00:00+00:00", sells=(), buys=(), chip=None):
    return {
        "matchday": md,
        "deadline": "2026-10-13T16:45:00+00:00",
        "generatedAt": gen,
        "actions": [{
            "matchday": md, "chip": chip,
            "sells": [{"id": i, "name": f"S{i}", "value": v} for i, v in sells],
            "buys": [{"id": i, "name": f"B{i}", "value": v} for i, v in buys],
        }],
    }


SQ = HeldSquad(ids=frozenset(range(1, 16)), matchday=2, bank=0.5)
AFTER = datetime(2026, 10, 13, 17, 0, tzinfo=UTC)
BEFORE = datetime(2026, 10, 13, 16, 0, tzinfo=UTC)


def test_roundtrip(tmp_path):
    f = tmp_path / "squad.txt"
    squadfile.write(f, SQ, {1: "GK Someone"})
    back = squadfile.read(f)
    assert back.ids == SQ.ids and back.matchday == 2 and back.bank == 0.5
    assert not back.wildcard_used and not back.limitless_used


def test_reads_committed_header(tmp_path):
    f = tmp_path / "squad.txt"
    f.write_text("# hi\n# matchday: 3\n# bank: 1.5\n# wildcard_used: yes\n"
                 + "\n".join(f"{i}  # x" for i in range(100, 115)) + "\n")
    sq = squadfile.read(f)
    assert sq.matchday == 3 and sq.bank == 1.5 and sq.wildcard_used
    assert len(sq.ids) == 15


def test_advance_applies_moves_after_deadline():
    new, desc = squadfile.advance(
        SQ, _plan(sells=[(3, 6.0)], buys=[(99, 6.4)]), now=AFTER, next_matchday=3)
    assert 99 in new.ids and 3 not in new.ids and len(new.ids) == 15
    assert new.matchday == 3 and abs(new.bank - 0.1) < 1e-9
    assert "S3 -> B99" in desc


def test_advance_waits_for_deadline():
    assert squadfile.advance(SQ, _plan(), now=BEFORE, next_matchday=3) is None


def test_advance_ignores_post_deadline_plan():
    late = _plan(gen="2026-10-13T23:00:00+00:00")
    assert squadfile.advance(SQ, late, now=AFTER, next_matchday=3) is None


def test_advance_refuses_plan_for_other_squad():
    # selling someone we do not own means the plan was built off another squad
    p = _plan(sells=[(500, 6.0)], buys=[(99, 6.0)])
    assert squadfile.advance(SQ, p, now=AFTER, next_matchday=3) is None


def test_advance_bank_and_chip():
    new, _ = squadfile.advance(SQ, _plan(chip="wildcard"), now=AFTER,
                               next_matchday=3)
    assert new.ids == SQ.ids and new.wildcard_used and new.matchday == 3


def test_banked_free_transfer_carries_one():
    new, _ = squadfile.advance(SQ, _plan(), now=AFTER, next_matchday=3)
    assert new.free_transfers == 3          # banked both, at most one carries


def test_hit_leaves_two_next_matchday():
    p = _plan(sells=[(1, 5.0), (2, 5.0), (3, 5.0)], buys=[(97, 5.0), (98, 5.0), (99, 5.0)])
    new, _ = squadfile.advance(SQ, p, now=AFTER, next_matchday=3)
    assert new.free_transfers == 2


def _at(md):
    return HeldSquad(ids=frozenset(range(1, 16)), matchday=md, bank=0.5,
                     free_transfers=3)


def test_into_the_knockouts_bank_rises_and_carry_stops():
    # MD8 -> MD9: EUR100m becomes EUR105m, added to the bank exactly once
    new, _ = squadfile.advance(_at(8), _plan(md=8), now=AFTER, next_matchday=9)
    assert new.bank == 5.5
    # MD9 -> MD10: no rise again; MD10's own allowance (2), nothing carried
    nxt, _ = squadfile.advance(new, _plan(md=9), now=AFTER, next_matchday=10)
    assert nxt.bank == 5.5 and nxt.free_transfers == 2


def test_knockout_allowances_do_not_roll():
    for nxt, free in ((12, 3), (13, 5), (14, 3), (17, 5)):
        new, _ = squadfile.advance(_at(nxt - 1), _plan(md=nxt - 1), now=AFTER,
                                   next_matchday=nxt)
        assert new.free_transfers == free, nxt
