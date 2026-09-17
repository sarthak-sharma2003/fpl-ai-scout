# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Vite + React + TypeScript, building to static files (`site/dist`) for GitHub Pages. No backend, no API keys at runtime. [Specified directly in the build brief; not a delegated choice.]

## Users

A single UEFA Champions League Fantasy 2026/27 manager (and by extension, anyone running the same tool for their own squad), opening the site in the minutes before a matchday deadline — often on a phone — to decide: who to captain, who to bench/sub, and whether to bank or make a transfer. Also consulted mid-window across a matchday's multiple gamedays to react to how day 1 went before day 2/3 lock in (auto-subs get disabled by any manual change, so the reread-before-acting moment matters). [Inferred from the brief's "opens before a deadline, reads in 30 seconds, and acts on it" framing and the MD1 3-day-gameday checklist content — no interactive confirmation round was available in this session; stated here as the working assumption.]

## Product Purpose

A decision tool, not a dashboard: it turns a Python-generated projection/optimization pipeline (`src/uclscout`) into an interface that answers, at a glance, "what do I do before this deadline" and "what's the plan for the matchdays after this one." Success is measured in seconds-to-decision, not time-on-page.

## Positioning

Two edges the model computes that a manager cannot get from UEFA's own app or generic FPL-style sites: (1) `recoveriesPer90` as a visible, weighted signal — ball recoveries are a ~4-point defensive floor most managers are pricing at zero; (2) explicit `minutesTrusted` flags — most projection tools present every player's expected points with equal confidence, this one visibly distinguishes "observed minutes" from "guessed minutes" and disables captaincy on the latter. The operational checklist (captain-switch timing, sub timing, the auto-subs trap) is itself a positioned asset: research-backed behavior most managers don't know to act on.

## Operating Context

- Data is generated offline by `src/uclscout` (feeds, optimize, plan, project, publish) and written as static JSON to `site/public/data/*.json` on a schedule (see the sibling `fpl-ai-scout` project's "nightly deploy rots silently" lesson — staleness must never be hidden).
- Consulted right before a deadline, often on a phone with a spotty connection at a match venue or on the go — loading and failure states must be legible, not blank.
- A UCL matchday can span multiple gamedays (MD1: 3 days) with its own sub/captain-switch logic; the checklist and gameday split are load-bearing, not decorative.
- Deployed as a GitHub Pages static site; all data fetches are relative (`./data/<name>.json`) so the app works from a project subpath.

## Capabilities and Constraints

- Four views: Now (default), Plan, Players, Teams — in that priority order.
- Data contract is fixed and already generated (schema in `site/public/data/*.json`); the frontend reads it, never mutates or re-derives projections.
- Must handle: loading state, fetch failure (name what failed), stale data (~36h threshold, shown persistently, not just on one view), deadline passed (must say so loudly, never silently disappear).
- Responsive (phone-first for the Now view), keyboard-navigable, visible focus, real semantic HTML, both light and dark themes.
- No user accounts, no write-back, no personalization beyond what's baked into the generated JSON (the JSON already represents "your" squad/plan).

## Evidence on Hand

Real, already-generated data at `site/public/data/{meta,squad,plan,players,fixtures,teams}.json` for 2026/27 season, tour 90, matchday 1 (deadline 2026-09-08T16:45 UTC). Player names, teams, values, projections, and the operational checklist are real pipeline output, not placeholder content — build against these files directly rather than inventing sample data.

## Product Principles

1. Scanned, not read — the highest-priority information (deadline, checklist, captain) must be legible in the first viewport with no scrolling on mobile.
2. Never hide an edge case silently — expired deadline, stale data, and failed fetches are stated loudly, in place, not swallowed.
3. The model's real edges (recoveriesPer90, minutesTrusted) get more visual weight than generic stats, because that's the actual product advantage.
4. Banking is a decision, not an absence of one — the UI must never let "nothing to do" and "chose to do nothing" look the same.
5. Operational reminders (the checklist) outrank aesthetic restraint when the two compete for prime real estate.

## Accessibility & Inclusion

Real semantic HTML, keyboard-navigable sortable/filterable controls, visible focus states, sufficient contrast in both themes. [Explicit build requirement, not inferred.]
