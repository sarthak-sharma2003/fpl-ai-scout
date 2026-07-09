"""Real FPL auto-sub rules — needed by the backtest simulator (plan §Phase6) to
score a gameweek honestly: a starter who gets 0 minutes is automatically replaced
by the highest-priority bench player who actually played, and the captain armband
transfers to the vice-captain if the captain gets 0 minutes. Not in the plan's
literal file list, but the backtest can't score without it — a 0-minute starter
scoring 0 when a bench player was available and played is not what actually
happens in FPL.
"""

from __future__ import annotations

from dataclasses import dataclass

from fplscout.decide.optimizer import XI_BOUNDS


def _formation_valid(xi: set[int], position_by_code: dict[int, str]) -> bool:
    if len(xi) != 11:
        return False
    counts: dict[str, int] = {}
    for code in xi:
        pos = position_by_code[code]
        counts[pos] = counts.get(pos, 0) + 1
    for pos, (lo, hi) in XI_BOUNDS.items():
        if not (lo <= counts.get(pos, 0) <= hi):
            return False
    return True


@dataclass
class AutosubResult:
    final_xi: set[int]
    effective_captain: int | None
    subs_made: list[tuple[int, int]]  # (out, in)


def apply_autosubs(
    starting_xi: set[int],
    bench_order: list[int],  # 3 outfield, best-first priority
    bench_gk: int | None,
    captain: int | None,
    vice_captain: int | None,
    actual_minutes: dict[int, int],
    position_by_code: dict[int, str],
) -> AutosubResult:
    """actual_minutes: code -> minutes actually played that GW (0 if unused/
    unavailable/no fixture data — treated the same as "didn't play" for autosub
    purposes, since FPL doesn't distinguish the reason)."""
    final_xi = set(starting_xi)
    subs_made: list[tuple[int, int]] = []

    starting_gks = [c for c in starting_xi if position_by_code.get(c) == "GKP"]
    starting_gk = starting_gks[0] if starting_gks else None
    if (
        starting_gk is not None
        and actual_minutes.get(starting_gk, 0) == 0
        and bench_gk is not None
        and actual_minutes.get(bench_gk, 0) > 0
    ):
        final_xi.discard(starting_gk)
        final_xi.add(bench_gk)
        subs_made.append((starting_gk, bench_gk))

    zero_minute_outfield = [
        c
        for c in starting_xi
        if position_by_code.get(c) != "GKP" and actual_minutes.get(c, 0) == 0
    ]
    used_bench: set[int] = set()
    for out_player in zero_minute_outfield:
        if out_player not in final_xi:
            continue  # already swapped out somehow (shouldn't happen, but be safe)
        for bench_player in bench_order:
            if bench_player is None or bench_player in used_bench:
                continue
            if actual_minutes.get(bench_player, 0) == 0:
                continue
            candidate_xi = (final_xi - {out_player}) | {bench_player}
            if _formation_valid(candidate_xi, position_by_code):
                final_xi = candidate_xi
                used_bench.add(bench_player)
                subs_made.append((out_player, bench_player))
                break

    effective_captain = captain
    if captain is not None and actual_minutes.get(captain, 0) == 0:
        effective_captain = vice_captain

    return AutosubResult(
        final_xi=final_xi, effective_captain=effective_captain, subs_made=subs_made
    )
