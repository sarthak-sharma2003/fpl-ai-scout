# 26/27 Roadmap & Operator Handbook

Written 2026-07-13 for whoever picks this repo up next — human, Claude session, or
worker model — with zero prior conversation context. Read this before touching
anything. The original build plan is `FPL_AI_SCOUT_PLAN.md`; this file is the
operational continuation and supersedes it where they disagree.

## 1. Where things stand

| Done | Commit | What |
|---|---|---|
| P0-A over-trading controls | b9daca2 | `transfer_penalty=1.5`/transfer + `max_hits=1`/GW in optimizer & simulator |
| P0-B season cold-start | b9daca2 | `prev_season_*` features joined on persistent `code` |
| #1 availability overlay | 39aa0ed | live status/chance scales minutes probas at inference only |
| #2 live per-GW ingestion | 5875c75 | `ingest/live_gw.py`, activates automatically for seasons vaastav doesn't cover |

Update 2026-07-13: issues #3, #4 and #5 are DONE (implemented in-session rather
than delegated — user call). §3's review protocols and §4's design are kept for
the record; they were followed. Honest holdout result for #3+#4: 2025-26
one-shot 1,552 pts / 14 hits vs 1,543 before — the +349 tuning-season gain did
not transfer; total-neutral on unseen data, shipped on principle (drift
tracking; armband priced at current-GW value; vice-captain no longer
solver-arbitrary). See `data/reports/p1_captaincy_sweep.md`. Nothing is open;
the system is mechanically ready for the 26/27 reset — next action is §5's
kickoff runbook the day the API flips, then §7's backlog (own-archive ep_next
first).

Backtest reality (full grids in `data/reports/p0a_sweep.md`, regenerable):
2024-25 ≈ 1,512-1,578 pts; 2025-26 validation 1,543 pts / 17 hits. Real
average-manager benchmark for 2025-26 is 1,895. The gap is **prediction
quality** (mean per-GW Spearman ~0.22-0.27), not decision logic.

## 2. Invariants — do not break these, reject work that does

1. **Leak discipline.** Every historical feature is computed from strictly
   prior data (`.shift(1)` contract, `features/build.py`). Any new feature
   needs the same proof. `tests/features/test_build.py::test_leakage_*` is the
   pattern.
2. **`fpl_xp` stays dead.** vaastav's `xP` is post-match-contaminated
   (documented in `models/points.py` docstring). It is not a feature anywhere.
   A change that reintroduces it will produce a spectacular backtest number and
   is wrong.
3. **Live status/`ep_next`/`penalties_order` are NEVER training features.**
   Historical snapshots of these leak (end-of-season values applied to earlier
   GWs). They are inference-time-only overlays (see `apply_availability`) —
   until OUR OWN `ep_next_archive` is deep enough to train on (it snapshots on
   every refresh; started 2026-07; needs at least ~half a season of pre-deadline
   snapshots before it's usable).
4. **Tuning discipline.** Tune on the 2024-25 replay ONLY. Touch 2025-26 once
   per change, as final validation. Never iterate constants against either
   season's total.
5. **±60 pts of backtest total is noise.** Single-replay totals carry
   decision-path noise (one different early transfer cascades). Do NOT rank
   configs or accept/reject features on total deltas smaller than that. Judge
   feature changes on the phase3 model metrics (`fplscout train` — per-GW
   Spearman/RMSE, both splits); judge decision changes on targeted metrics
   (e.g. captain-slot points for #4, hits count for transfer logic).
6. **Schema strictness is deliberate.** `ingest/schemas.py` uses
   `extra="forbid"` so the 26/27 API reset breaks loudly. Do not loosen it to
   "fix" a SchemaDriftError — update the models and re-record fixtures instead
   (§5 step 2).
7. **$0 budget.** Free data only. No paid APIs, solvers, or hosting.

## 3. Review protocol for delegated issues #3 and #4

Pattern from #1 and #2 (both needed review; #1 needed a fix): the worker's
summary describes intent, the diff describes reality. Read the full diff. Run
`ruff check src/ tests/` and `pytest -q` yourself. Then check specifically:

### Issue #3 — in-season Dixon-Coles refit
- The leak rule is `fixture.event < decision_gw AND finished`. There must be a
  test proving refit parameters are identical whether or not gw ≥ decision_gw
  results are present in the input.
- Both backtests run, all four totals reported (before/after × two seasons).
  Deltas within ±60 = wash; the change ships on code merits (drift-tracking is
  correct in principle), it does not need a points win. REJECT any constant
  that was iterated to move the total.
- Check promoted-team handling didn't regress (teams with no training-window
  history must not crash the fit or get garbage parameters).
- Live pipeline (`pipeline.py`) must apply the same rule for the current
  season's finished fixtures.

### Issue #4 — q90-blend captaincy
- `cap_ev` must be a CURRENT-GW blend, not horizon-collapsed (armband pays this
  week only). When the column is absent, optimizer output must be byte-identical
  — every pre-existing golden test passing unchanged is the proof.
- w swept on 2024-25 only ({0, 0.3, 0.5}), chosen once, validated once on
  2025-26. The w=0-vs-main delta (switching the cap term to current-GW EV at
  all) must be reported separately from the q90 effect.
- Better acceptance metric than season total: **sum of captain-slot points**
  across the replay (2x'd player's base points each GW). It isolates the thing
  being changed; ask for it if the worker only reports totals.
- Captain/vice must both remain constrained to the XI, distinct (existing
  constraints — check they weren't disturbed).

After review: commit with `Closes #N` in the message, push to main, leave a
short review comment on the issue saying what was checked and what (if
anything) was fixed on top.

## 4. Issue #5 — upcoming-GW projection path (CRITICAL, blocks the first team)

**The gap:** `features` rows are derived from `player_gw_history`, i.e. only
PLAYED fixtures. `pipeline.generate_projections(season, gw)` selects feature
rows at (season, gw) — for an unplayed gameweek those rows don't exist. At
26/27 GW1 the entire season has no rows, so `fplscout project`/`optimize`
produce nothing. In-season, the same off-by-one means we can only project "as
of the last completed GW", never FOR the upcoming deadline. The backtest never
hit this because a replayed season is fully played (its rows exist; their
shift(1) form is still leak-safe pre-match). P0-B's `prev_season_*` features
were built precisely so a GW1 row would have signal — but nothing constructs
the GW1 rows themselves.

**Design (ponytail-lazy, reuses the existing machinery):** extend
`features/build.py` to emit rows for UNPLAYED upcoming fixtures too:

1. After loading `player_gw_history`, build a synthetic frame of
   player-fixture rows for fixtures in the `fixtures` table that are not yet
   finished and belong to the next unplayed gameweek (or all future GWs of the
   current season — the horizon model can then use real rows instead of its
   merge gymnastics, but next-GW-only is the minimum). One row per
   (player, fixture) where the player's team plays.
   - Player universe per team: bootstrap-derived `player_season` is NOT
     populated for live seasons — use the `players` dimension + each element's
     current team from the most recent ingestion (`live_gw.py` keeps
     bootstrap's element→team/position/price in memory; persisting bootstrap's
     per-element team/position/price snapshot is part of this issue —
     the natural home is finally populating `player_season` from bootstrap in
     `live_gw.py`, which issue #2 explicitly deferred).
2. Concat these rows (with NaN targets/minutes/etc.) onto the played frame
   BEFORE the rolling computation. The existing `.shift(1)` machinery then
   yields exactly the right leak-safe form: for GW1 26/27 all-NaN rolling +
   `prev_season_*` joined on code; mid-season, rolling form from all played
   rows. No new leak surface — the synthetic rows have no outcome data at all.
3. `value` (price) for synthetic rows = bootstrap `now_cost`; `was_home`/
   `opponent_team_id`/`fdr`/`kickoff_time` from the fixtures table; `is_dgw`
   from the per-team fixture count in that gw.
4. Downstream: `pipeline.latest_reference_point` should return the next
   UNPLAYED gw once such rows exist; `generate_projections` then just works.
   `total_ev_for_optimizer` should pass `decision_gw = next unplayed gw` with
   these rows as `base_rows` (fixing the current subtle inclusion of the
   already-played reference gw at full decay weight h=0).
5. Tests: (a) synthetic GW1 rows for a fresh season have NaN rolling form but
   populated `prev_season_*`/price/fdr; (b) leak test — a synthetic row's form
   equals what shift(1) over played rows gives, never includes the unplayed
   fixture itself; (c) `latest_reference_point` returns the unplayed gw;
   (d) end-to-end: `project` + `optimize` produce a legal 15 for a season with
   zero played gameweeks (fixture data only).

Acceptance: with a DB containing 26/27 teams/fixtures/players but zero played
GWs, `fplscout project && fplscout optimize` emits a legal wildcard squad with
non-degenerate EVs (premiums outrank fodder; a ruled-out player via the
availability overlay gets ~0).

## 5. Season-kickoff runbook (the day the API flips to 26/27)

**Dress-rehearsed 2026-07-14 and PASSED**: a fabricated 2026-27 (zero played
GWs, all-unfinished fixtures, live prices) ran the full chain offline —
features (841 synthetic GW1 rows) → train → project (841 finite EVs) →
optimize (legal 15, 100.0m, Haaland captained at 8.5 EV) → DO THIS sheet.
The rehearsal caught a real bug (NULL availability status zeroed every
player's minutes — fixed, regression-tested), which is why it exists.
Re-run after any pipeline change before the reset:
`python scripts/dress_rehearsal.py data/fpl.duckdb /tmp/rehearsal.duckdb`

**Provisional GW1 draft from the REAL released fixture list** (2026-07-14):
`scripts/provisional_2627.py <espn_fixtures.html> data/fpl.duckdb /tmp/prov.duckdb`
parses ESPN's published 26/27 calendar (article id 49082662; the parser
self-heals ESPN's own omissions — it inferred GW8 Arsenal v Everton and
Aston Villa v Man City from the round-robin structure), fabricates the season
with real opponents, and emits a provisional draft: stand-in end-of-25/26
prices, no summer transfers, promoted teams (Coventry, Hull, Ipswich) known
to the calendar but playerless. First output captained Haaland (8.3 EV) and
triple-stacked Arsenal's defense for the GW1 home fixture vs promoted
Coventry — the horizon model visibly consuming real fixtures. Superseded at
FPL launch; delete the script after.

**Update 2026-07-20:** the runbook below is now automated as **`fplscout
kickoff`** — refresh → project → optimize → preflight → publish → DO THIS
sheet, with the schema-drift loudness of step 1/2 built in (it stops and
prints exactly what to fix, and is safe to re-run until it passes). Every
publish is gated by `fplscout preflight` (legality/availability/EV/freshness
— see `src/fplscout/preflight.py`); the dress rehearsal ends by asserting the
rehearsal draft passes it. The manual steps below remain as the explanation
of what kickoff does and the fallback if it stops.

The daily 9am scheduled task `fpl-2627-season-reset-check` alerts on the flip
(manual check: bootstrap-static's first event deadline year becomes 2026).
Then, in order:

1. `fplscout refresh --raw` — expect `SchemaDriftError` (the reset usually
   adds/renames bootstrap fields; that loudness is by design).
2. For each failing pydantic model: update `ingest/schemas.py`, re-record the
   matching `tests/fixtures/*.json` from the live API, keep `extra="forbid"`.
   `pytest -q` green before proceeding.
3. `fplscout refresh` — live path activates automatically
   (`derive_current_season` → "2026-27" ∉ `VAASTAV_SEASONS` → `live_gw` sync).
   First run takes ~15 min (one element-summary call per player, throttled).
   Pre-GW1 this ingests 0 gw rows — fine; teams/fixtures/players still land.
4. Requires issue #5 merged: `fplscout train` (production models retrain on
   all history), then `fplscout project` + `fplscout optimize` → **first
   predicted GW1 squad** in `recommendations`. `fplscout publish` renders it
   to the site.
5. Sanity-check the draft by hand before trusting it: premium attackers
   captained, no flagged players (availability overlay working), promoted-team
   players priced sanely, 3-per-club respected. Compare against a couple of
   the community sites (fplreview.com, fploptimized.com) — big disagreements
   mean a bug, not an edge.
6. Register the actual FPL team, put its `team_id` in `config/settings.yaml`,
   and after GW1's deadline run `decide/squad_state.py`'s reconcile path so
   the tracked squad matches reality.
7. When the mini-league is created, put its id in `mini_league_id`
   (settings.yaml). From then on `refresh` also syncs rival squads, chips and
   standings (`ingest/league.py` — dormant until configured), and the site's
   League page (ownership, differentials, our-model EV of every rival squad)
   plus chips usage tracking populate automatically. This is intel for the
   human — it deliberately does NOT feed the optimizer (§7.6 stands:
   differential-chasing optimizes rank variance, not points; in an 8-manager
   league points decide it).

## 6. Weekly in-season loop (until Phase 8 automates it)

Post-deadline+results each GW: `fplscout refresh` (ingests the finished GW,
rebuilds features incl. next-GW rows) → `fplscout project` → `fplscout
optimize` → `fplscout publish` → follow or override the recommendation, then
reconcile squad state. Total runtime is dominated by the element-summary sweep.

## 7. Backlog after #3/#4/#5, in priority order

1. **`ep_next` as a feature (from OUR archive)** — once `ep_next_archive` has
   ~15+ GWs of pre-deadline snapshots. This is the single highest-expected-value
   model improvement (the contaminated version carried 60%+ of feature gain;
   the clean version will carry a real fraction of that). Needs a joinable
   (season, gw, code, ep_next_as_of_deadline) view + retrain + the usual two-split
   validation. **Precondition solved 2026-07-20:** the archive now survives the
   nightly deploy's ephemeral runner via git-tracked `data/ep_next_archive.csv`
   (two-way synced in every refresh, committed by deploy.yml) — before this fix
   every CI snapshot was silently discarded and the archive could never have
   reached training depth.
2. **Penalty/set-piece overlay** — live `penalties_order` as an inference-time
   EV bump (like availability). Low effort, modest win; FPL xG already includes
   penalties historically so the model isn't fully blind, but the taker's
   *identity* changes (transfers, form) and only the live field knows.
3. **Minutes-model depth** — predicted-lineup signal (free sources:
   premierinjuries.com, rotation patterns), European-fixture congestion flags.
   P(60+) drives everything; its ceiling caps the system.
4. **Bench-weight coupling** — replace static (0.25/0.15/0.05) with weights
   from the XI's actual P(miss). Cheap once minutes probas are trustworthy.
5. **Price-change modeling** — team-value growth funds premiums; skip until
   the above are done.
6. NOT worth doing on current evidence: further transfer-logic tuning (noise,
   see §2.5), ownership/differential modeling (optimizes rank, not points),
   full points-model decomposition (revisit only if a specific component — e.g.
   defender clean-sheet timing — shows up as the weak spot in validation).

## 8. Delegation protocol (hybrid workflow)

Write issues the way #1-#4 were written: self-contained context (no
conversation references), explicit design, hard constraints called out (leak
rules, what must stay byte-identical), acceptance criteria a reviewer can
execute, and a coordination note about rebasing. Workers implement; a
reviewer (Claude) reads the full diff against §2's invariants before landing.
Both delegated deliveries so far were high quality but one contained a
cross-module interaction bug (#1's NULL last_seen_season) that only the
review caught — keep the review step.
