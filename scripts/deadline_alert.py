"""Open a GitHub issue before each FPL / UCL deadline with the moves to make.

Runs at the end of every deploy (3x daily). Within ALERT_HOURS of a deadline it
opens one issue per deadline, @mentioning the owner so GitHub pushes it to
their phone/email. If a later run (team news, price moves) changes the advice,
it comments the new version instead of opening a second issue -- comments
notify, silent body edits do not. Issues close themselves once the deadline
passes.

usage: python scripts/deadline_alert.py [--dry-run]
Needs GH_TOKEN and GITHUB_REPOSITORY in CI; with --dry-run prints instead.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ALERT_HOURS = 40.0
LABEL = "deadline-alert"
OWNER = "sarthak-sharma2003"
SITE = "https://sarthak-sharma2003.github.io/fpl-ai-scout/"


@dataclass
class Alert:
    key: str               # stable per deadline, e.g. "FPL 2026-27 GW8"
    deadline: datetime
    body: str              # markdown advice

    @property
    def fingerprint(self) -> str:
        return hashlib.sha1(self.body.encode()).hexdigest()[:10]

    def title(self) -> str:
        return f"{self.key} — DO THIS before {self.deadline:%a %d %b %H:%M} UTC"


def _hours_left(deadline: datetime, now: datetime) -> float:
    return (deadline - now).total_seconds() / 3600


# --------------------------------------------------------------------------
# FPL


def fpl_alert(now: datetime) -> Alert | None:
    import duckdb

    db = REPO_ROOT / "data" / "fpl.duckdb"
    if not db.exists():
        return None
    con = duckdb.connect(str(db), read_only=True)
    row = con.execute(
        "SELECT season, event, deadline_time FROM gameweeks "
        "WHERE deadline_time > ? ORDER BY deadline_time LIMIT 1",
        [now.replace(tzinfo=None)],
    ).fetchone()
    con.close()
    if row is None:
        return None
    season, gw, deadline = row
    deadline = deadline.replace(tzinfo=UTC)
    if _hours_left(deadline, now) > ALERT_HOURS:
        return None
    sheet_path = REPO_ROOT / "data" / "reports" / f"weekly_{season}_gw{gw}.md"
    if sheet_path.exists():
        body = sheet_path.read_text()
    else:
        sheets = sorted((REPO_ROOT / "data" / "reports").glob(f"weekly_{season}_gw*.md"))
        body = (
            f"**WARNING: no recommendation was produced for GW{gw}.** "
            + (f"The latest sheet is `{sheets[-1].name}` — it is for a different "
               "gameweek, do not follow it blindly." if sheets else
               "The pipeline produced no sheet at all.")
            + " Check the deploy run."
        )
    return Alert(key=f"FPL {season} GW{gw}", deadline=deadline, body=body)


# --------------------------------------------------------------------------
# UCL


def _ucl_body(squad: dict, plan: dict) -> str:
    s = squad.get("squad") or {}
    moves = s.get("moves") or {}
    picks = s.get("picks") or []
    cap = next((p for p in picks if p.get("captain")), None)
    xi = [p for p in picks if p.get("starting")]
    bench = sorted((p for p in picks if not p.get("starting")),
                   key=lambda p: -p.get("points", 0))
    out = [f"# DO THIS — UCL MD{squad['matchday']}", ""]
    if moves.get("chip"):
        out.append(f"**Play {moves['chip'].upper()}.**")
    if moves.get("buys") or moves.get("sells"):
        out.append("## Transfers")
        for o, i in zip(moves.get("sells", []), moves.get("buys", []), strict=False):
            out.append(f"- OUT {o['name']} ({o['team']}) → IN **{i['name']}** "
                       f"({i['team']}, €{i['value']}m)")
        if moves.get("hits"):
            out.append(f"- ⚠ {moves['hits']} hit(s) = -{4 * moves['hits']}")
    elif s.get("held"):
        out.append(f"**No transfers — bank** ({moves.get('freeAvailable', '?')} free "
                   "available).")
    if s.get("bankAfter") is not None:
        out.append(f"\nBank after moves: €{s['bankAfter']}m")
    if cap:
        out.append(f"\n**Captain:** {cap['name']} ({cap['team']}, plays {cap.get('day')}) — "
                   f"{cap['points']:.1f} projected")
    sw = s.get("captainSwitch")
    if sw and sw.get("switchTo"):
        name = {p["id"]: p["name"] for p in picks}
        out.append(f"**Captain switch:** if {name.get(sw.get('captainId'), '?')} scores "
                   f"under {sw.get('switchIfBelow')} points, move the armband to "
                   f"{name.get(sw['switchTo'], '?')} before the next day's kick-offs.")
    out.append("\n## Starting XI")
    out += [f"- {p['pos']} {p['name']} ({p['team']}) {p['points']:.1f} — {p.get('day')}"
            for p in xi]
    out.append("\n## Bench (sub order)")
    out += [f"{k}. {p['pos']} {p['name']} ({p['team']}) {p['points']:.1f}"
            for k, p in enumerate(bench, 1)]
    if squad.get("checklist"):
        out.append("\n## Matchday checklist")
        out += [f"- {c}" for c in squad["checklist"]]
    nxt = plan.get("actions", [])[1:4]
    if nxt:
        out.append("\n## Coming up")
        out += [f"- {a['summary']}" for a in nxt]
    if s.get("held"):
        out.append("\n_Planned off your tracked squad (`ucl/config/squad.txt`). If you "
                   "did something different, edit that file — it advances automatically "
                   "after each deadline assuming you made the moves above._")
    return "\n".join(out)


def ucl_alert(now: datetime) -> Alert | None:
    data = REPO_ROOT / "ucl" / "site" / "public" / "data"
    try:
        squad = json.loads((data / "squad.json").read_text())
        plan = json.loads((data / "plan.json").read_text())
    except (OSError, ValueError):
        return None
    deadline = datetime.fromisoformat(squad["deadline"])
    if not 0 < _hours_left(deadline, now) <= ALERT_HOURS:
        return None
    generated = datetime.fromisoformat(squad["generatedAt"])
    if (now - generated).total_seconds() > 12 * 3600:
        body = ("**WARNING: the UCL data is more than 12h old — the refresh "
                "failed.** Advice below may be stale.\n\n" + _ucl_body(squad, plan))
    else:
        body = _ucl_body(squad, plan)
    return Alert(key=f"UCL {squad['season']} MD{squad['matchday']}",
                 deadline=deadline, body=body)


# --------------------------------------------------------------------------
# GitHub


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True,
                          text=True).stdout


def _open_issues() -> list[dict]:
    out = _gh("issue", "list", "--label", LABEL, "--state", "open",
              "--json", "number,title,body,comments", "--limit", "50")
    return json.loads(out)


_FP = re.compile(r"<!-- fp:(\w+) -->")
_DL = re.compile(r"<!-- deadline:(\S+) -->")


def sync(alerts: list[Alert], now: datetime, dry_run: bool) -> None:
    if dry_run:
        for a in alerts:
            print(f"=== {a.title()}\n{a.body}\n")
        return
    try:
        _gh("label", "create", LABEL, "--color", "D93F0B",
            "--description", "Pre-deadline DO THIS", "--force")
    except subprocess.CalledProcessError:
        pass
    issues = _open_issues()
    for a in alerts:
        mine = next((i for i in issues if i["title"].startswith(a.key + " ")), None)
        stamp = f"\n\n<!-- fp:{a.fingerprint} -->\n<!-- deadline:{a.deadline.isoformat()} -->"
        if mine is None:
            body = (f"@{OWNER} deadline in {_hours_left(a.deadline, now):.0f}h. "
                    f"Site: {SITE}\n\n{a.body}{stamp}")
            _gh("issue", "create", "--title", a.title(), "--label", LABEL,
                "--body", body)
            print(f"opened: {a.title()}")
            continue
        seen = _FP.findall(mine["body"]) + [
            fp for c in mine.get("comments", []) for fp in _FP.findall(c.get("body", ""))]
        if seen and seen[-1] == a.fingerprint:
            print(f"unchanged: {a.key}")
            continue
        _gh("issue", "comment", str(mine["number"]), "--body",
            f"@{OWNER} **UPDATED advice** ({_hours_left(a.deadline, now):.0f}h to "
            f"deadline) — this replaces the version above.\n\n{a.body}{stamp}")
        print(f"updated: {a.key}")
    for i in issues:
        m = _DL.search(i["body"])
        if m and datetime.fromisoformat(m.group(1)) < now:
            _gh("issue", "close", str(i["number"]), "--comment", "Deadline passed.")
            print(f"closed: {i['title']}")


def main() -> None:
    dry = "--dry-run" in sys.argv
    now = datetime.now(UTC)
    alerts = []
    for fn in (fpl_alert, ucl_alert):
        try:
            a = fn(now)
        except Exception as e:  # noqa: BLE001 - one side failing must not block the other
            print(f"{fn.__name__} failed: {e}", file=sys.stderr)
            continue
        if a:
            alerts.append(a)
    if not os.environ.get("GH_TOKEN") and not dry:
        print("no GH_TOKEN; dry run")
        dry = True
    sync(alerts, now, dry)


if __name__ == "__main__":
    main()
