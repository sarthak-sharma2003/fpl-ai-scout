"""uclscout command line."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer

from . import feeds, pipeline, plan, publish, squadfile

app = typer.Typer(add_completion=False, help="UEFA Champions League Fantasy scout.")

SITE_DATA = Path("site/public/data")


def _squad_ids(path: Path | None) -> set[int]:
    """Your current squad, one UEFA player id per line (# comments allowed)."""
    held = squadfile.read(path) if path else None
    return set(held.ids) if held else set()


@app.command()
def status() -> None:
    """Deadline, matchday and whether anything is stale."""
    c = feeds.constraints()
    now = datetime.now(UTC)
    delta = c.deadline - now
    hours = delta.total_seconds() / 3600
    state = (f"{hours:.1f}h remaining" if hours > 0
             else f"LOCKED {abs(hours):.1f}h ago")
    typer.echo(f"Matchday {c.matchday} of {c.last_matchday}")
    typer.echo(f"Deadline  {c.deadline.isoformat()}  ({state})")
    typer.echo(f"Budget    EUR{c.budget}m, max {c.max_per_club} per club")
    mds = feeds.matchdays()
    md = next((m for m in mds if m.id == c.matchday), None)
    if md:
        typer.echo(f"Gamedays  {', '.join(md.gamedays)}  "
                   f"({md.subs_allowed} manual subs allowed)")
        for line in plan.matchday_checklist(md.id, md.gamedays, md.subs_allowed):
            typer.echo(f"  - {line}")


@app.command()
def squad(
    squad_file: Path = typer.Option(None, "--squad", help="your current squad ids"),
    min_start: float = typer.Option(70.0, help="expected-minutes gate for the XI"),
) -> None:
    """Best squad for the upcoming matchday."""
    ctx = pipeline.load()
    projections = pipeline.project_matchday(ctx, ctx.constraints.matchday)
    from .optimize import optimize
    s = optimize(projections, budget=ctx.constraints.budget,
                 max_per_club=ctx.constraints.max_per_club,
                 min_start=min_start, locked=_squad_ids(squad_file))
    xi = {p.player.id for p in s.xi}
    typer.echo(f"\nMD{ctx.constraints.matchday}  "
               f"EUR{s.cost:.1f}m / {ctx.constraints.budget}m  "
               f"projected {s.total_points:.1f}\n")
    for p in s.picks:
        mark = "C" if p.player.id == s.captain.player.id else (
            "*" if p.player.id in xi else " ")
        flag = "" if p.minutes.trusted else "  [minutes imputed]"
        typer.echo(f" {mark} {p.player.pos:3} {p.player.name[:22]:24}"
                   f"{p.player.team[:14]:16}EUR{p.player.value:>5} "
                   f"{p.points:>5.2f}  {p.minutes.expected:>3.0f}min{flag}")
    typer.echo(f"\n  day split: {s.by_day()}")


@app.command("plan")
def plan_cmd(
    squad_file: Path = typer.Option(None, "--squad"),
    time_limit: int = typer.Option(120, help="solver seconds"),
) -> None:
    """Full remaining-season plan: transfers, banking, chips."""
    ctx = pipeline.load()
    p = pipeline.build_plan(ctx, current_squad=_squad_ids(squad_file) or None,
                            time_limit=time_limit)
    typer.echo(f"\nProjected total {p.total_points:.1f} over "
               f"{len(p.actions)} matchdays\n")
    typer.echo(p.summary())


@app.command()
def backtest(
    csv: Path = typer.Option(Path("data/archive_players.csv")),
    season: str = typer.Option("2024-25"),
) -> None:
    """Replay a completed season."""
    from .backtest import compare
    for r in compare(csv, season):
        typer.echo(str(r))


@app.command()
def archive(
    out: Path = typer.Option(Path("data/archive_players.csv")),
    tours: str = typer.Option("70,80", help="comma-separated tour ids"),
) -> None:
    """Download historical per-matchday data."""
    from .archive import download, verify
    ids = [int(t) for t in tours.split(",") if t.strip()]
    n = download(out, ids)
    typer.echo(f"{n} rows -> {out}")
    typer.echo(str(verify(out)))


@app.command("publish")
def publish_cmd(
    out: Path = typer.Option(SITE_DATA, help="output directory"),
    squad_file: Path = typer.Option(None, "--squad"),
    time_limit: int = typer.Option(120),
) -> None:
    """Write the static JSON the site reads."""
    ctx = pipeline.load()
    held = squadfile.read(squad_file) if squad_file else None
    files = publish.build(ctx, out, held=held, time_limit=time_limit)
    for name, path in files.items():
        typer.echo(f"  {name:16} {path.stat().st_size:>8,} bytes")
    rep = ctx.lineup_report
    if rep is not None:
        typer.echo(f"  {rep.summary()} [{rep.note}]")
        if rep.unmatched:
            typer.echo(f"  unmatched sample: {'; '.join(rep.unmatched[:8])}")


@app.command()
def advance(
    squad_file: Path = typer.Option(..., "--squad"),
    plan_file: Path = typer.Option(SITE_DATA / "plan.json", "--plan"),
) -> None:
    """After a deadline, apply the moves the site recommended to the squad file.

    Run BEFORE `publish` overwrites plan.json: the plan on disk is then the one
    that was live at the deadline. Does nothing until the deadline has passed,
    or if the plan was built for a different squad -- it never guesses.
    """
    held = squadfile.read(squad_file)
    plan_json = squadfile.load_plan(plan_file)
    if held is None or plan_json is None:
        typer.echo("advance: no squad file or no published plan; nothing to do")
        return
    mds = sorted(m.id for m in feeds.matchdays())
    nxt = next((m for m in mds if m > held.matchday), held.matchday + 1)
    res = squadfile.advance(held, plan_json, now=datetime.now(UTC),
                            next_matchday=nxt)
    if res is None:
        typer.echo(f"advance: squad stays at MD{held.matchday}")
        return
    new, desc = res
    names = {p.id: f"{p.pos:3} {p.name} ({p.team})"
             for p in feeds.players(feeds.CURRENT_TOUR,
                                    feeds.constraints().matchday)}
    squadfile.write(squad_file, new, names)
    typer.echo(f"advance: applied {desc}; squad now goes into MD{new.matchday}, "
               f"bank EUR{new.bank:.1f}m")


if __name__ == "__main__":
    app()
