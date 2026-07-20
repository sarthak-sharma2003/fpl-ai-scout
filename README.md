# Xabi's Long-Xo

Local-first system that predicts FPL points and optimizes squad/transfer/captain/chip
decisions for one team, 2026/27 season. Full build plan: [`FPL_AI_SCOUT_PLAN.md`](FPL_AI_SCOUT_PLAN.md).
**Working on this repo? Read [`ROADMAP_2627.md`](ROADMAP_2627.md) first** — current
state, invariants (leak/tuning discipline), review protocol for delegated issues,
and the season-kickoff runbook.

$0 spent on data: free FPL API + free historical data (vaastav/Fantasy-Premier-League)
only. No paid solvers, no paid hosting.

## Build status

| Phase | What | Status |
|---|---|---|
| 0 | Scaffold + FPL API client | ✅ done |
| 1 | Data layer (DuckDB + historical loader) | ✅ done |
| 2 | Feature store | ✅ done |
| 3 | Prediction models (minutes / team goals / points) | ✅ done — beats both baselines, see validation report |
| 4 | MILP optimizer | ✅ done — golden-case tests, <2s solve at ~600-player scale |
| 5 | Chip planner | ✅ done |
| 6 | Backtest simulator (go/no-go gate) | ✅ done — see honest results below; both seasons currently below the plan's 2,400 target |
| 7 | Static site pipeline + React frontend | ✅ done — `fplscout publish` renders site/public/data/, Pages deploy |
| 8 | Automation + weekly ops | ✅ done — `fplscout report` one-command weekly op, daily season-reset watcher, runbook below |
| 9 | Season kickoff checklist | ready — `ROADMAP_2627.md` §5, waiting for FPL data to go from API to pipeline |

## Quickstart

```bash
# macOS only: LightGBM needs the OpenMP runtime, not installed by pip
brew install libomp

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# schema-validate + smoke-test every endpoint against the live API (no DB writes)
fplscout refresh --raw

# populate DuckDB from vaastav historical data (2021-22 .. 2025-26), idempotent
fplscout refresh

# train minutes/team-goals/points models, write a validation report to data/reports/
fplscout train

# run tests (no live network calls — uses recorded fixtures)
pytest -q

# lint
ruff check src/ tests/
```

## Repo layout

```
config/           settings.yaml (team_id, horizon, decay...), rules.yaml (UI rules)
data/             fpl.duckdb + raw/ + models/ + reports/ — all gitignored, all
                   regenerable via `fplscout refresh` then `fplscout train`
src/fplscout/
  db.py           DuckDB connection + schema (players, teams, fixtures, gameweeks,
                   player_gw_history, our_entry/picks/transfers, projections, recommendations)
  ingest/         fpl_api.py (typed client), schemas.py (pydantic models),
                   vaastav.py (historical loader, 2021-22 .. 2025-26)
  features/       feature store (Phase 2)
  models/         minutes / team_goals / points models (Phase 3)
  decide/         optimizer.py, rules_engine.py, chip_planner.py (Phase 4-5)
  backtest/       season replay simulator (Phase 6)
  api/            FastAPI backend (Phase 7)
  report/         weekly "DO THIS" report generator (Phase 8)
  preflight.py    sanity gate: FPL legality, availability flags, EV sanity, freshness
  cli.py          fplscout refresh|train|backtest|project|optimize|preflight|publish|report|kickoff
site/             our React app (Vite + Tailwind), fully static, GitHub Pages
tests/            pytest suite; tests/fixtures/ holds recorded API payloads for offline tests
```

## Design notes

- **Schema drift is fatal, by design.** `ingest/schemas.py` uses strict pydantic
  models (`extra="forbid"`) so any FPL API change — especially the 26/27 season
  reset — breaks ingestion loudly instead of silently dropping fields. If
  `refresh` starts raising `SchemaDriftError`, re-record `tests/fixtures/*.json`
  from the live API and update the model that failed.
- **Caching.** `FplApiClient` caches every response to `data/raw/*.json` with a
  per-endpoint TTL (bootstrap/fixtures/element-summary: 6h, entry: 1h, live: 5min).
  Throttled to ≤1 req/sec with a browser User-Agent, per plan politeness rules.
- **No authenticated endpoints.** The app tracks squad state itself
  (`decide/squad_state.py`, Phase 4) rather than depending on the flaky
  login-walled `my-team` endpoint; it reconciles weekly against the public
  `entry/{id}/event/{gw}/picks/` endpoint instead.
- **Cross-season player identity uses FPL's persistent `code` field**, not fuzzy
  name matching. Every vaastav `players_raw.csv` carries both a per-season numeric
  `id` (resets each season) and a `code` (stable forever — verified Salah/Haaland
  across all 5 loaded seasons). `code` is the primary join key; name-based fuzzy
  matching is only a fallback for rows an exact join can't resolve. See
  `ingest/vaastav.py` module docstring for what else changed vs. the original plan
  sketch after grounding against the real repo (`cleaned_merged_seasons.csv` is
  stale — missing 2024-25/2025-26 — so every season loads from its own per-season
  files instead).
- **2024-25 vaastav data mixes in "Assistant Manager" rows** (element_type 5 —
  e.g. Arteta, Emery — a 24/25 novelty-chip entity, not a squad player) and uses
  an inconsistent goalkeeper label (`"GK"` vs. `"GKP"` in a handful of 2021-22
  rows). Both are filtered/normalized in `ingest/vaastav.py` — caught because the
  Phase 3 by-position validation report initially had a phantom `AM` "position"
  and a near-empty `GKP` bucket.
- **Points model trains one direct per-position regressor** on `total_points`
  rather than the plan's literal 8-component decomposition (attacking/CS/DEFCON/
  bonus/appearance/saves/cards as separate sub-models combined by formula). Same
  signals (team-goals-model output, minutes-model probabilities, rolling DEFCON/
  xG/xA) are fed in as features either way — see `models/points.py` docstring for
  the full reasoning.
- **`fpl_xp` (vaastav's historical `xP` column) is not a model feature, at all,
  anywhere.** Full history in `models/points.py` and `models/train.py`'s module
  docstrings; summary here because it shaped several other design decisions:
  - *First pass:* pooled Spearman over the whole holdout is dominated by "can
    you tell reserves from starters" (~61% of rows are 0-minute players) —
    replaced by mean per-GW Spearman on a decision-relevant subset (plausible
    starters). A points model trained WITH `fpl_xp` included looked extremely
    strong (mean per-GW Spearman up to 0.75+) and carried 61-65% of its total
    gain on that single feature (measured directly via LightGBM gain
    importances, not estimated).
  - *What that strength actually was:* vaastav's own README documents `xP` as
    scraped from bootstrap-static's `ep_this` **after** each gameweek ends,
    with an empirically observed same-GW correlation to actual points the
    README itself calls "unusually high for a genuinely pre-match feature" —
    i.e. confirmed post-match-contaminated, not merely inconvenient. An earlier
    pass here had already suspected something was off (via a backtest total
    that exceeded the real-world all-time FPL season record) and tried routing
    between an "xp-included" and "xp-excluded" variant per gameweek — the
    wrong fix, because it treated the feature as *sometimes missing* rather
    than *contaminated where present*. `fpl_xp` is now excluded from
    `models/dataset.py`'s query entirely; there is one points model, not a
    choice between variants.
  - **Honest Phase 3 result without it:** the model clearly beats the naive
    5-GW-average baseline overall on both backtest splits (2024-25: mean
    per-GW Spearman 0.259 vs 0.218; 2025-26: 0.218 vs 0.143) — but not
    uniformly by position: it's roughly tied with naive for GKP (0.119 vs
    0.119, 2024-25) and loses to it for FWD (0.097 vs 0.130, 2024-25). Real,
    specific weaknesses, not smoothed over. Full numbers in
    `data/reports/phase3_validation_*.md` (regenerated by `fplscout train`).
  - **The raw FPL field itself isn't the problem** — only vaastav's *scrape
    timing* is. Values *we* fetch pre-deadline don't have this issue, because
    we control when we fetch them; see the archiving note below.
- **`ingest/health.py::check_ep_next_health`** runs on every `fplscout refresh`:
  warns if bootstrap-static's `ep_next` distribution goes degenerate (near-zero
  per-position mean, or an abnormally high null rate). **`archive_ep_next`**
  (same module, same command) persists a timestamped snapshot of every player's
  live `ep_next` to `ep_next_archive` on every refresh, effective immediately —
  not waiting for the 26/27 season to start. Once that archive is deep enough
  to validate on, `ep_next` can be legitimately reintroduced as a feature;
  vaastav's historical data never can be, no matter how it's post-processed,
  because the problem is in how it was collected.
- **`decide/optimizer.py` solves one gameweek per call**, using horizon-collapsed
  EV (`sum_t decay^t * ev[p][t]`, computed by the caller) rather than true
  multi-period time-indexed variables — the plan's own sanctioned fallback for
  keeping the solve fast. Verified: <2s at ~600-player scale (full wildcard
  draft) and ~1.3s for a realistic single-transfer decision, both far under the
  60s budget. Chip modes (`wildcard`/`free_hit`/`bench_boost`/`triple_captain`)
  are solver re-runs with mode flags, not extra binary variables, per plan §7.
  Bench has 4 slots (not the plan's literal 3): the backup goalkeeper doesn't
  compete with the 3 outfield bench spots for autosub priority (FPL only
  auto-subs a GK for a GK), so it gets ~0 weight in the objective outside
  `bench_boost` mode. `decide/squad_state.py` tracks bank/free-transfers/squad
  in the `our_entry`/`our_picks`/`our_transfers` tables and reconciles against
  live picks (`reconcile()` — returns discrepancy warnings, doesn't
  auto-correct). Free transfers cap at 5, verified live in bootstrap-static's
  `game_config.rules.max_extra_free_transfers: 4` (+1 base).
- **`decide/chip_planner.py` reads chip validity windows from the live API's
  `chips` array at call time, never hardcoded** — grounding against the live
  25/26 API (2026-07-08) showed bench boost/triple captain are valid from GW1
  while wildcard/free hit only from GW2, a distinction a hardcoded "GW2-19"
  window (the plan's own shorthand) would have missed entirely for two of the
  four chips. DGW/BGW detection is a plain fixture-count groupby per plan §6.7.
  Split into pure EV/scheduling functions (fully unit-tested, no solver calls)
  and `evaluate_chip_windows()`, which does drive the optimizer across
  candidate gameweeks — expensive, so Phase 6's backtest calls it once per GW
  using projections it already has rather than a standalone horizon scan.
- **`backtest/simulator.py`'s horizon-EV computation went through three
  versions**, the first two caught by concrete evidence, not review-by-inspection:
  1. Summed each *future* gameweek's own row from the features table into the
     current decision's horizon EV. Real bug: a future row's rolling-form
     features reflect actual match results from the gameweeks in between,
     which a real-time decision-maker can't know yet (verified directly —
     Salah's `roll5_points` at gw10 differs from what it'd compute as of gw3).
     Combined with the not-yet-understood `fpl_xp` contamination above, this
     produced a 2024-25 backtest total (3,401 pts) exceeding the real-world
     all-time FPL season record — the tell that started this whole
     investigation.
  2. Fixed the leak by flattening to "current gameweek's own EV, decayed
     forward" — but that discarded all fixture-awareness (can't tell a DGW
     from a BGW ahead of time), and the model started transferring almost
     every single gameweek chasing single-week EV noise (up to 249 hits in
     one season).
  3. **`models/horizon.py`** (current): freezes every player-*form* feature at
     the decision gameweek and swaps in only what's legitimately knowable in
     advance per target gameweek — the fixture list itself (opponent, venue,
     DGW/BGW count, rest days, all from the `fixtures` table) and the
     Dixon-Coles model's opponent-specific attack/defense parameters (fit once
     on training data, doesn't peek at results). Minutes-model uncertainty
     widens with horizon distance via a linear blend toward the
     position-average probability. `decide/optimizer.py`'s `hit_cost` is
     called with a fixed +50% margin from the simulator (`DECISION_HIT_COST`
     in `backtest/simulator.py`) — matching the plan's own §7 wording ("only
     takes a -4 when EV gain over horizon > 4 + margin") — as a documented
     safety factor against residual week-to-week noise, not tuned against the
     backtest total; actual scoring always deducts the real -4/hit regardless
     of this margin.
  - **Blank-gameweek bug, also caught by evidence:** a player on a team with
    no fixture that week used to vanish entirely from the optimizer's player
    pool (not just score 0) instead of persisting with ~0 that week's EV,
    breaking squad continuity and making that gameweek's solve infeasible
    outright (2025-26 GW34, 6/20 teams blank, scored exactly 0 with an empty
    XI before the fix). `backtest/simulator.py::_player_universe_by_gw` now
    forward-fills each player's last-known position/team/price into blank
    weeks.
  - **Honest final backtest numbers** (both required samples, no further
    tuning toward any target): 2024-25 (train 2021-22..2023-24) — **1,571
    pts**, 73 hits. 2025-26 (train 2021-22..2024-25) — **1,601 pts**, 65 hits.
    Both below the plan's stated 2,400 target and below the real, grounded
    2025-26 average-manager benchmark (1,895 pts — summed live
    `average_entry_score` across all 38 finished gameweeks, notably below the
    plan's own estimated ~2,200-2,300). That target assumed a legitimate
    `ep_next` feature the historical replay no longer has; per review, judge
    these totals against the real benchmarks the report prints, not the
    single 2,400 scalar. `fplscout backtest` regenerates
    `data/reports/phase6_backtest_*.md` with the full GW-by-GW breakdown.
    Weekly transfer churn (regularly 2-8 buys/week even with the margin) is a
    concrete, unresolved lead for further model-error analysis — most likely
    the points model's week-to-week predictions for an unchanged player still
    move more than real-world stability would justify, an open question this
    build doesn't resolve.
  - **26/27-prep update (2026-07-12):** transfer churn controls
    (`transfer_penalty=1.5`/transfer + `max_hits=1`/GW in
    `backtest/simulator.py`) and season cold-start features (`prev_season_*`
    in `features/build.py`, joined on persistent `code`) are in. A tuning
    sweep on 2024-25 only (grid + honest read in `data/reports/p0a_sweep.md`)
    showed season totals are insensitive to the transfer controls within
    ±60 pts of decision-path noise — the "over-trading bleeds 100-200 pts"
    hypothesis was rejected; the old hits were roughly EV-neutral. One-shot
    2025-26 validation: 1,543 pts / 17 hits (vs 1,601 / 65 before) —
    total-neutral, ~4x less churn, slightly better model-level Spearman/RMSE
    on both splits, and the 26/27 GW1 draft no longer runs on all-NaN form.
    The remaining gap to ~2,000 is prediction quality, not decision logic;
    the live-only levers are the open workstreams (issues #1-#4:
    availability overlay, live per-GW ingestion, in-season DC refit, q90
    captaincy).
  - **All five 26/27-prep issues are now closed (2026-07-13).** #1 availability
    overlay, #2 live ingestion, #3 in-season Dixon-Coles refit, #4 ceiling
    captaincy (current-GW `cap_ev`, q90 weight 0.5, plus a real vice-captain
    objective term — the vice was solver-arbitrary before), #5 upcoming-GW
    projection path (synthetic feature rows for the next unplayed gameweek, so
    `project`/`optimize` can decide a deadline that hasn't happened — including
    26/27 GW1). Captaincy sweep: `data/reports/p1_captaincy_sweep.md`. Honest
    holdout validation of #3+#4 together: 2025-26 one-shot 1,552 pts / 14 hits
    (vs 1,543 before) — the +349 tuning-season gain did not transfer;
    total-neutral on unseen data, shipped on principle not on a points claim.
    The system is now mechanically ready for the 26/27 reset end to end:
    refresh → ingest → features (incl. upcoming GW) → train → project →
    optimize → publish.

## Weekly ops runbook

Once the 26/27 season is live, after each gameweek finishes:

```bash
fplscout refresh   # network step: ingests the finished GW + live status (~15 min first run)
fplscout report    # everything else: project -> optimize -> publish -> "DO THIS" sheet
```

`report` retrains production models (fresh data every week), writes projections
and a recommendation for the NEXT deadline's gameweek, publishes the static
site data, and prints/writes the markdown decision sheet
(`data/reports/weekly_<season>_gw<N>.md`). `--skip-pipeline` re-renders the
sheet without recomputing.

Every publish is gated by **`fplscout preflight`** — FPL legality (squad shape,
3-per-club, budget), availability flags on starters, EV sanity, projection
freshness, deadline clock. A FAIL blocks the publish (`--force` to override
while debugging); run it standalone any time before trusting a recommendation.

Season kickoff (the day the FPL API resets to 26/27 — a daily 9am scheduled
check alerts on it) is one command, safe to re-run until it passes:

```bash
fplscout kickoff   # refresh -> project -> optimize -> preflight -> publish -> DO THIS
```

On schema drift (expected at reset) it stops with the exact runbook step;
details in `ROADMAP_2627.md` §5. Once the mini-league exists, set `team_id`
and `mini_league_id` in `config/settings.yaml` — refresh then also syncs rival
squads/chips, and the site's League page (standings, ownership, differentials,
our-model EV of every rival squad) plus chip usage tracking light up
automatically. Pre-deadline snapshots of FPL's own `ep_next` accumulate in
git-tracked `data/ep_next_archive.csv` (committed by the nightly deploy) until
the archive is deep enough to become a model feature (backlog §7.1).
