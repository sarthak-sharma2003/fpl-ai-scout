"""Emit the static JSON the site reads.

Every payload carries `generated_at` and `matchday` so the page can show
staleness instead of hiding it. fpl-ai-scout served nine-day-old data because a
failed nightly job left the last good file in place and nothing on the page
said so -- a human had to notice. Freshness is part of the contract here.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from . import pipeline
from . import plan as planning
from .optimize import Squad
from .project import Projection
from .squadfile import HeldSquad

SCHEMA = 1


def _player(p: Projection) -> dict:
    return {
        "id": p.player.id,
        "name": p.player.name,
        "pos": p.player.pos,
        "team": p.player.team,
        "teamId": p.player.team_id,
        "value": p.player.value,
        "points": round(p.points, 2),
        "minutes": round(p.minutes.expected),
        "minutesSource": p.minutes.source.value,
        "minutesTrusted": p.minutes.trusted,
        "minutesNote": p.minutes.note,
        "canCaptain": p.can_captain,
        "opponentAdj": round(p.opponent_adj, 3),
        "owned": p.player.selected_pct,
        "recoveriesPer90": (round(p.player.recoveries_per90, 2)
                            if p.player.recoveries_per90 else None),
        "status": p.player.status,
        "day": p.kickoff_day,
        "priorPoints": p.player.total_points,
    }


def _squad(s: Squad) -> dict:
    xi = {p.player.id for p in s.xi}
    return {
        "cost": round(s.cost, 1),
        "xiPoints": round(s.xi_points, 1),
        "totalPoints": round(s.total_points, 1),
        "captainId": s.captain.player.id,
        "byDay": s.by_day(),
        "picks": [{**_player(p), "starting": p.player.id in xi,
                   "captain": p.player.id == s.captain.player.id}
                  for p in s.picks],
    }


def _held_squad(a: planning.MatchdayAction, held: HeldSquad,
                budget: float) -> dict:
    """The squad you will hold this matchday, after the recommended moves."""
    xi = {p.player.id for p in a.xi}
    cap = a.captain.player.id if a.captain else None
    cost = sum(p.player.value for p in a.squad)
    return {
        "cost": round(cost, 1),
        "xiPoints": round(sum(p.points for p in a.xi), 1),
        "totalPoints": round(a.xi_points, 1),
        "captainId": cap,
        "byDay": Squad(picks=a.squad, xi=a.xi, captain=a.captain or a.xi[0],
                       cost=cost, xi_points=0.0).by_day(),
        "picks": [{**_player(p), "starting": p.player.id in xi,
                   "captain": p.player.id == cap}
                  for p in sorted(a.squad, key=lambda p: (
                      p.player.id not in xi, -p.points))],
        "held": True,
        "budget": budget,
        "bankAfter": round(budget - cost, 1),
        "moves": {
            "buys": [_player(x) for x in a.buys],
            "sells": [_player(x) for x in a.sells],
            "hits": a.hits,
            "chip": a.chip,
            "freeAvailable": a.free_available,
            "summary": a.describe(),
        },
        "trackedSince": f"squad file as of MD{held.matchday}",
    }


def build(ctx: pipeline.Context, out_dir: Path, *,
          current_squad: set[int] | None = None,
          held: HeldSquad | None = None,
          time_limit: int = 120) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    md = pipeline.next_open_matchday(ctx)
    this_md = next((m for m in ctx.matchdays if m.id == md), None)
    projections = pipeline.project_matchday(ctx, md)
    if held is not None:
        current_squad = set(held.ids)

    meta = {
        "schema": SCHEMA,
        "generatedAt": now,
        "season": "2026-27",
        "tour": ctx.tour,
        "matchday": md,
        "lastMatchday": ctx.constraints.last_matchday,
        "deadline": (this_md.deadline if this_md
                     else ctx.constraints.deadline).isoformat(),
        "budget": ctx.constraints.budget,
        "maxPerClub": ctx.constraints.max_per_club,
        "gamedays": this_md.gamedays if this_md else [],
        "subsAllowed": this_md.subs_allowed if this_md else 0,
        "checklist": planning.matchday_checklist(
            md, this_md.gamedays if this_md else [],
            this_md.subs_allowed if this_md else 0),
    }

    the_plan = pipeline.build_plan(
        ctx, current_squad=current_squad,
        bank=held.bank if held else 0.0,
        wildcard_used=held.wildcard_used if held else False,
        limitless_used=held.limitless_used if held else False,
        time_limit=time_limit)
    meta["budget"] = the_plan.budget    # held squad: its value + bank
    first = the_plan.actions[0] if the_plan.actions else None
    if the_plan.opening_squad:
        squad_payload = _squad(the_plan.opening_squad)
    elif held is not None and first is not None and first.xi:
        squad_payload = _held_squad(first, held, the_plan.budget)
    else:
        squad_payload = None
    plan_json = {
        **meta,
        "totalProjected": round(the_plan.total_points, 1),
        "limitless": ({"matchday": the_plan.limitless[0],
                       "gain": round(the_plan.limitless[1], 1)}
                      if the_plan.limitless else None),
        "actions": [
            {
                "matchday": a.matchday,
                "chip": a.chip,
                "banking": a.banking,
                "freeAvailable": a.free_available,
                "hits": a.hits,
                "projected": round(a.xi_points, 1),
                "captain": _player(a.captain) if a.captain else None,
                "buys": [_player(x) for x in a.buys],
                "sells": [_player(x) for x in a.sells],
                "summary": a.describe(),
                "xi": [x.player.id for x in a.xi],
            }
            for a in the_plan.actions
        ],
    }

    files = {
        "meta.json": meta,
        "squad.json": {**meta, "squad": squad_payload},
        "players.json": {**meta,
                         "players": [_player(x) for x in projections[:400]]},
        "plan.json": plan_json,
        "fixtures.json": {**meta, "matchdays": [
            {"matchday": m.id,
             "deadline": m.deadline.isoformat(),
             "subsAllowed": m.subs_allowed,
             "gamedays": m.gamedays,
             "matches": [{"home": m2.home_id, "away": m2.away_id,
                          "kickoff": m2.kickoff.isoformat(),
                          "lineupAnnounced": m2.lineup_announced}
                         for m2 in m.matches]}
            for m in ctx.matchdays]},
        "teams.json": {**meta, "teams": [
            {**asdict(t), "elo": round(ctx.rating(t.id))}
            for t in sorted(ctx.teams, key=lambda t: -ctx.rating(t.id))]},
    }

    written = {}
    for name, payload in files.items():
        path = out_dir / name
        path.write_text(json.dumps(payload, indent=1, default=str),
                        encoding="utf-8")
        written[name] = path
    return written
