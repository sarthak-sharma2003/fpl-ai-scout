"""Predicted line-ups -> P(start). Inline fixtures only; nothing touches the network."""
import itertools
import urllib.request
from datetime import UTC, datetime, timedelta

import pytest

from uclscout import lineups, pipeline
from uclscout.feeds import Constraints, Match, Matchday, Player, Team
from uclscout.lineups import (
    calibrate,
    fetch_all,
    fresh_clubs,
    map_clubs,
    match_club,
    normalise,
    start_probabilities,
)
from uclscout.project import MinutesSource, estimate_minutes

RMA, PSG, BAY = 50051, 52747, 50037
TEAMS = [Team(RMA, "RMA", "Real Madrid", 1), Team(PSG, "PSG", "Paris", 1),
         Team(BAY, "BAY", "Bayern München", 1), Team(50124, "ATM", "Atleti", 1),
         Team(52758, "BVB", "B. Dortmund", 2), Team(52498, "SLA", "Slavia Praha", 4)]


def _p(pid, name, full="", team=RMA, mins=900, status="", pos="MID"):
    return Player(id=pid, name=name, team_id=team, team="T", pos=pos, value=8.0,
                  status=status, selected_pct=1, total_points=60, minutes=mins,
                  goals=0, assists=0, clean_sheets=0, saves=0, recoveries=0,
                  motm=0, as_of_matchday=2, full_name=full)


def _club(team, n=11, prefix="Player"):
    """n players, P1..Pn, at one club; names 'Player 1' etc."""
    base = team * 100
    return [_p(base + i, f"{prefix} {i}", f"{prefix} {i}", team=team)
            for i in range(1, n + 1)]


# -- names ---------------------------------------------------------------------

def test_normalise_strips_accents_case_and_punctuation():
    assert normalise("Vinícius Júnior") == "vinicius junior"
    assert normalise("Martin Ødegaard") == "martin odegaard"
    assert normalise("Jean-Clair Todibo") == "jean clair todibo"
    assert normalise("N'Golo Kanté") == "ngolo kante"
    assert normalise("K. Kvaratskhelia") == "k kvaratskhelia"
    assert normalise("İlkay Gündoğan") == "ilkay gundogan"


def test_match_within_club_handles_accents_initials_and_surnames():
    roster = [
        _p(1, "Vinícius Júnior", "Vinícius Júnior"),
        _p(2, "K. Kvaratskhelia", "Khvicha Kvaratskhelia"),
        _p(3, "A. Rüdiger", "Antonio Rüdiger"),
        _p(4, "Gabriel", "Gabriel"),
        _p(5, "G. Jesus", "Gabriel Jesus"),
    ]
    got = match_club(["Vinicius Junior", "Khvicha Kvaratskhelia", "Rüdiger",
                      "Gabriel", "Gabriel Jesus"], roster)
    assert got == {"Vinicius Junior": 1, "Khvicha Kvaratskhelia": 2,
                   "Rüdiger": 3, "Gabriel": 4, "Gabriel Jesus": 5}


def test_ambiguous_and_wrong_names_stay_unmatched():
    roster = [_p(1, "B. Silva", "Bernardo Silva"), _p(2, "A. Silva", "André Silva"),
              _p(3, "T. Hernández", "Theo Hernández")]
    got = match_club(["Silva", "Lucas Fernandez", "Nobody Atall"], roster)
    assert got == {}, "a shared surname or a near-miss must not guess"


def test_aliases_bridge_nicknames():
    roster = [_p(1, "Gabriel", "Gabriel"), _p(2, "B. Saka", "Bukayo Saka")]
    assert match_club(["Magalhães"], roster) == {"Magalhães": 1}


def test_stale_nis_duplicate_does_not_make_a_player_ambiguous():
    roster = [_p(1, "A. Wan-Bissaka", "Aaron Wan-Bissaka", status="NIS"),
              _p(2, "A. Wan-Bissaka", "Aaron Wan-Bissaka")]
    assert match_club(["Aaron Wan-Bissaka"], roster) == {"Aaron Wan-Bissaka": 2}


def test_team_names_map_by_alias_and_fuzzy_fallback():
    got = map_clubs(["PSG", "Bayern Munich", "Atlético", "Borussia Dortmund",
                     "Slavia Prag", "Real Madrid", "Nowhere United"], TEAMS)
    assert got == {"PSG": PSG, "Bayern Munich": BAY, "Atlético": 50124,
                   "Borussia Dortmund": 52758, "Slavia Prag": 52498,
                   "Real Madrid": RMA}


# -- probabilities -------------------------------------------------------------

def test_calibration_is_monotone_and_hits_the_documented_levels():
    assert calibrate(0.8) == pytest.approx(0.90)
    assert calibrate(0.5) == pytest.approx(0.50)
    assert calibrate(0.4) == pytest.approx(0.50)
    assert calibrate(0.2) == pytest.approx(0.10)
    assert calibrate(0.0) == 0.0
    assert calibrate(1.0) == pytest.approx(0.98)
    levels = [i / 20 for i in range(21)]
    assert all(calibrate(a) <= calibrate(b) + 1e-9
               for a, b in itertools.pairwise(levels))


def test_sources_are_averaged_and_a_full_xi_implies_the_bench():
    squad = _club(RMA, 13)
    xi = {f"Player {i}": 0.8 for i in range(1, 12)}
    data = {
        "a": {"Real Madrid": xi},
        "b": {"Real Madrid": {**xi, "Player 11": 0.2, "Player 12": 0.8}},
    }
    rep = start_probabilities(squad, TEAMS, data=data)
    p = rep.probs
    assert p[RMA * 100 + 1] == pytest.approx(0.90)            # both: XI
    assert p[RMA * 100 + 11] == pytest.approx((0.90 + 0.10) / 2)
    assert p[RMA * 100 + 12] == pytest.approx((0.10 + 0.90) / 2)  # a: implied
    assert p[RMA * 100 + 13] == pytest.approx(0.10)            # nobody named him
    assert rep.match_rate == 1.0 and rep.clubs == 1


def test_no_implied_bench_when_a_named_starter_failed_to_match():
    squad = _club(RMA, 13)
    xi = {f"Player {i}": 0.8 for i in range(1, 11)} | {"Zz Unknown": 0.8}
    rep = start_probabilities(squad, TEAMS, data={"a": {"Real Madrid": xi}})
    assert RMA * 100 + 12 not in rep.probs, "a missed match must not bench anyone"
    assert rep.unmatched == ["Real Madrid: Zz Unknown (a)"]


def test_futbolfantasy_previous_round_levels_are_ignored():
    # FF's 1.0 / 0.0 come from the round already played (see IGNORE_LEVELS)
    squad = _club(PSG, 12)
    xi = {f"Player {i}": 0.8 for i in range(1, 12)}
    rows = {**xi, "Player 12": 1.0, "Player 1": 0.8}
    rep = start_probabilities(squad, TEAMS,
                              data={"futbolfantasy": {"PSG": rows}})
    assert rep.probs[PSG * 100 + 12] == pytest.approx(0.10), "1.0 was dropped"


def test_only_requested_clubs_are_read():
    squad = _club(RMA, 11) + _club(PSG, 11)
    data = {"a": {"Real Madrid": {f"Player {i}": 0.8 for i in range(1, 12)},
                  "PSG": {f"Player {i}": 0.8 for i in range(1, 12)}}}
    rep = start_probabilities(squad, TEAMS, clubs={PSG}, data=data)
    assert rep.probs and all(pid // 100 == PSG for pid in rep.probs)


# -- failure never propagates --------------------------------------------------

def test_failed_fetch_returns_empty_without_raising(monkeypatch):
    def down(*a, **k):
        raise OSError("network unreachable")
    monkeypatch.setattr(urllib.request, "urlopen", down)
    data, failed = fetch_all()
    assert data == {} and set(failed) == set(lineups.SOURCES)
    rep = start_probabilities(_club(RMA), TEAMS)
    assert rep.probs == {} and rep.sources == []
    assert "none used" in rep.summary()


def test_malformed_payload_drops_only_that_source():
    good = {"Real Madrid": {"Player 1": 0.8}}
    payloads = {"u1": ["not", "a", "dict"], "u2": good, "u3": {"X": "junk"}}
    data, failed = fetch_all({"bad": "u1", "ok": "u2", "junk": "u3"},
                             fetch=payloads.__getitem__)
    assert list(data) == ["ok"] and set(failed) == {"bad", "junk"}


# -- freshness and the pipeline ------------------------------------------------

NOW = datetime(2026, 10, 13, 6, 0, tzinfo=UTC)


def _md(md_id, kickoff):
    return Matchday(md_id, deadline=kickoff - timedelta(hours=2), subs_allowed=2,
                    is_current=False, is_locked=False,
                    matches=[Match(md_id * 10, md_id, kickoff, RMA, PSG, False, "")])


def test_fresh_clubs_only_inside_the_window():
    assert fresh_clubs(_md(2, NOW + timedelta(hours=30)), NOW) == {RMA, PSG}
    assert fresh_clubs(_md(2, NOW + timedelta(hours=72)), NOW) == set()
    assert fresh_clubs(_md(2, NOW - timedelta(hours=1)), NOW) == set()
    assert fresh_clubs(None, NOW) == set()


def _ctx():
    squad = _club(RMA, 11) + _club(PSG, 11)
    return pipeline.Context(
        tour=90,
        constraints=Constraints(2, 17, 100.0, 3, NOW + timedelta(hours=10)),
        teams=TEAMS, players=squad, elo={}, moved_clubs=set(),
        matchdays=[_md(2, NOW + timedelta(hours=12)),
                   _md(3, NOW + timedelta(days=7))])


def test_lineups_apply_to_the_next_matchday_only():
    ctx = _ctx()
    benched = {f"Player {i}": 0.8 for i in range(2, 12)} | {"Player 1": 0.0}
    pipeline.attach_lineups(ctx, now=NOW, data={"a": {"Real Madrid": benched}})
    assert ctx.lineup_matchday == 2 and ctx.lineup_probs
    md2 = {x.player.id: x for x in pipeline.project_matchday(ctx, 2)}
    md3 = {x.player.id: x for x in pipeline.project_matchday(ctx, 3)}
    assert md2[RMA * 100 + 1].minutes.source is MinutesSource.PREDICTED_LINEUP
    assert md2[RMA * 100 + 1].minutes.expected < 40
    assert md3[RMA * 100 + 1].minutes.source is not MinutesSource.PREDICTED_LINEUP


def test_no_fetch_when_no_club_kicks_off_soon():
    ctx = _ctx()
    pipeline.attach_lineups(ctx, now=NOW - timedelta(days=4),
                            data={"a": {"Real Madrid": {"Player 1": 0.8}}})
    assert ctx.lineup_probs == {} and ctx.lineup_matchday is None
    assert "not fetched" in ctx.lineup_report.note


# -- the blend in estimate_minutes ---------------------------------------------

def test_blend_moves_minutes_without_overriding_the_record():
    nailed = _p(1, "Nailed", mins=900)          # 900 of the club's 900
    rotation = _p(2, "Rotation", mins=300)
    peak = 900.0
    base_n = estimate_minutes(nailed, peak)
    base_r = estimate_minutes(rotation, peak)

    benched = estimate_minutes(nailed, peak, predicted_lineups={1: 0.1})
    assert benched.source is MinutesSource.PREDICTED_LINEUP and benched.trusted
    assert 0.1 < benched.p_start < base_n.p_start, "blended, not obeyed"
    assert benched.expected < 0.5 * base_n.expected

    named = estimate_minutes(rotation, peak, predicted_lineups={2: 0.9})
    assert named.p_start > base_r.p_start and named.expected > base_r.expected

    # unanimous XI for a nailed player barely moves him
    kept = estimate_minutes(nailed, peak, predicted_lineups={1: 0.9})
    assert kept.expected > 0.9 * base_n.expected


def test_blend_keeps_the_players_own_start_length():
    # a centre-back plays 90 when he starts; a winger is hooked at ~70
    cb = _p(1, "CB", mins=900, pos="DEF")
    winger = _p(2, "W", mins=630)
    m_cb = estimate_minutes(cb, 900.0, predicted_lineups={1: 0.9})
    m_w = estimate_minutes(winger, 900.0, predicted_lineups={2: 0.9})
    assert m_cb.expected > m_w.expected + 10


def test_unavailable_is_zero_and_doubtful_halves_with_lineups():
    for status in ("I", "S", "NIS"):
        m = estimate_minutes(_p(1, "X", status=status), 900.0,
                             predicted_lineups={1: 0.9})
        assert m.expected == 0.0
    fit = estimate_minutes(_p(1, "X"), 900.0, predicted_lineups={1: 0.9})
    doubt = estimate_minutes(_p(1, "X", status="D"), 900.0,
                             predicted_lineups={1: 0.9})
    assert doubt.expected == pytest.approx(fit.expected / 2)
    assert doubt.p_start == pytest.approx(fit.p_start / 2)
