"""The squad you actually hold, as a small text file the deploy keeps current.

UEFA's team pages need a login, so the planner cannot read your real team. This
file stands in for it: one UEFA player id per line, plus a few `# key: value`
header lines (matchday, bank, chips used). After each deadline the deploy calls
`advance`, which applies the moves the site recommended for that matchday --
the file then describes the squad going into the next one. If you deviate from
the recommendation, edit the file; it is the source of truth.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

_HEADER = re.compile(
    r"^#\s*(matchday|bank|wildcard_used|limitless_used|free_transfers)\s*:\s*(\S+)")
FREE_PER_MATCHDAY, MAX_CARRY = 2, 1


@dataclass(frozen=True)
class HeldSquad:
    ids: frozenset[int]
    matchday: int                  # the matchday this squad goes INTO
    bank: float = 0.0              # EUR m left over after buying the 15
    wildcard_used: bool = False
    limitless_used: bool = False
    free_transfers: int = FREE_PER_MATCHDAY   # 2, or 3 with one banked
    comments: tuple[str, ...] = field(default=(), compare=False)


def _yes(v: str) -> bool:
    return v.strip().lower() in {"yes", "true", "1", "y"}


def read(path: Path) -> HeldSquad | None:
    if not path or not path.exists():
        return None
    ids: set[int] = set()
    hdr: dict[str, str] = {}
    comments: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = _HEADER.match(raw.strip())
        if m:
            hdr[m.group(1)] = m.group(2)
            continue
        line = raw.split("#")[0].strip()
        if line.isdigit():
            ids.add(int(line))
        elif raw.startswith("#") and not ids:
            comments.append(raw)
    if not ids:
        return None
    return HeldSquad(
        ids=frozenset(ids),
        matchday=int(hdr.get("matchday", 0)),
        bank=float(hdr.get("bank", 0.0)),
        wildcard_used=_yes(hdr.get("wildcard_used", "no")),
        limitless_used=_yes(hdr.get("limitless_used", "no")),
        free_transfers=int(hdr.get("free_transfers", FREE_PER_MATCHDAY)),
        comments=tuple(c for c in comments
                       if not _HEADER.match(c.strip()) and c.strip() != "#"),
    )


def write(path: Path, sq: HeldSquad, names: dict[int, str] | None = None) -> None:
    names = names or {}
    lines = list(sq.comments) or ["# Your UCL Fantasy squad (see squadfile.py)."]
    lines += [
        "#",
        f"# matchday: {sq.matchday}",
        f"# bank: {sq.bank:.1f}",
        f"# wildcard_used: {'yes' if sq.wildcard_used else 'no'}",
        f"# limitless_used: {'yes' if sq.limitless_used else 'no'}",
        f"# free_transfers: {sq.free_transfers}",
    ]
    for pid in sorted(sq.ids, key=lambda i: names.get(i, str(i))):
        label = names.get(pid)
        lines.append(f"{pid:<10} # {label}" if label else str(pid))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def advance(sq: HeldSquad, plan_json: dict, *, now: datetime,
            next_matchday: int) -> tuple[HeldSquad, str] | None:
    """Apply the published recommendation once its deadline has passed.

    Only acts when the plan was published for exactly the squad's matchday and
    BEFORE that deadline (so it is the advice that was actually on the site),
    and the deadline is now behind us. Returns (new squad, description) or None.
    """
    if int(plan_json.get("matchday", -1)) != sq.matchday:
        return None
    deadline = datetime.fromisoformat(plan_json["deadline"])
    generated = datetime.fromisoformat(plan_json["generatedAt"])
    if now < deadline or generated > deadline:
        return None
    act = next((a for a in plan_json.get("actions", [])
                if a.get("matchday") == sq.matchday), None)
    if act is None:
        return None
    sells = {p["id"]: p for p in act.get("sells", [])}
    buys = {p["id"]: p for p in act.get("buys", [])}
    if not set(sells) <= sq.ids:
        # the plan was built off a different squad; never guess
        return None
    ids = (set(sq.ids) - set(sells)) | set(buys)
    if len(ids) != len(sq.ids):
        return None
    bank = sq.bank + sum(p["value"] for p in sells.values()) - sum(
        p["value"] for p in buys.values())
    chip = act.get("chip")
    # one unused free transfer carries; a chip or a hit leaves none spare
    unused = 0 if chip else max(0, sq.free_transfers - len(buys))
    new = replace(
        sq, ids=frozenset(ids), matchday=next_matchday, bank=round(bank, 1),
        wildcard_used=sq.wildcard_used or chip == "wildcard",
        limitless_used=sq.limitless_used or chip == "limitless",
        free_transfers=FREE_PER_MATCHDAY + min(MAX_CARRY, unused),
    )
    moves = ", ".join(f"{sells[s]['name']} -> {buys[b]['name']}"
                      for s, b in zip(sells, buys)) or "no transfers"
    return new, f"MD{sq.matchday}: {moves}" + (f" [{chip}]" if chip else "")


def load_plan(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
