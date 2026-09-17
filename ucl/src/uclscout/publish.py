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


def build(ctx: pipeline.Context, out_dir: Path, *,
          current_squad: set[int] | None = None,
          time_limit: int = 120) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    md = ctx.constraints.matchday
    this_md = next((m for m in ctx.matchdays if m.id == md), None)
    projections = pipeline.project_matchday(ctx, md)

    meta = {
        "schema": SCHEMA,
        "generatedAt": now,
        "season": "2026-27",
        "tour": ctx.tour,
        "matchday": md,
        "lastMatchday": ctx.constraints.last_matchday,
        "deadline": ctx.constraints.deadline.isoformat(),
        "budget": ctx.constraints.budget,
        "maxPerClub": ctx.constraints.max_per_club,
        "gamedays": this_md.gamedays if this_md else [],
        "subsAllowed": this_md.subs_allowed if this_md else 0,
        "checklist": planning.matchday_checklist(
            md, this_md.gamedays if this_md else [],
            this_md.subs_allowed if this_md else 0),
    }

    the_plan = pipeline.build_plan(ctx, current_squad=current_squad,
                                   time_limit=time_limit)
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
            }
            for a in the_plan.actions
        ],
    }

    files = {
        "meta.json": meta,
        "squad.json": {**meta,
                       "squad": _squad(the_plan.opening_squad)
                       if the_plan.opening_squad else None},
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
