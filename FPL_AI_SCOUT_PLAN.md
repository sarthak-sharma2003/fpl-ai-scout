# FPL AI SCOUT — Build Plan (First Draft, for Opus)

> **Mission:** Maximize total FPL points for ONE team over the 2026/27 season. Sarthak
> will execute every transfer, captain pick, bench order, and chip the app recommends,
> without question. The app's recommendations ARE the team.
>
> **Hard constraint:** $0 spent on data. Free APIs, free scraping, free hosting only.
> **Everything else is unconstrained** — use whatever tech wins.
>
> **This is Sarthak's own project, built from scratch.** The design reference
> screenshots (an "FPL AI Scout" mock a relative made — purple theme, dashboard /
> transfers / rules pages) are a *benchmark to beat*, not an asset to reuse: it was a
> pretty UI with no real brain. We build both the brain (data pipeline → prediction
> models → optimizer → API) and our own frontend, and both must be better.

---

## 0. Ground truth (verified live on 2026-07-08 — do NOT trust training data over this)

Facts checked against the live API today. Re-verify each at build time:

1. **The FPL API is still serving the completed 2025/26 season.** `bootstrap-static`
   shows GW1 deadline `2025-08-15`. The 2026/27 game has NOT launched yet (usually
   resets mid-to-late July; GW1 ~mid-August 2026). Consequence: we have a full,
   finished season of ground truth available *right now* for backtesting, and a
   pre-season window to build and validate before a single real decision is due.
2. **Chips (25/26 rules, confirmed in API `chips` array):** TWO of each chip per
   season — wildcard, free hit, bench boost, triple captain — one usable GW2–19, the
   second GW20–38. Unused first-half chips do NOT carry over. Re-check this array when
   26/27 launches; rules may change again.
3. **Defensive contribution points exist** (introduced 25/26): API exposes
   `defensive_contribution`, `clearances_blocks_interceptions`, `tackles`,
   `recoveries`. DEF: 2pts at 10 CBIT; MID/FWD: 2pts at 12 CBIRT. Must be in the
   points model — it moved cheap-defender and holding-mid valuations materially.
4. **The FPL API natively exposes xG** now: `expected_goals`, `expected_assists`,
   `expected_goal_involvements`, `expected_goals_conceded` (+ per-90 variants), per
   player, and per-match in `element-summary`. Understat/FBref scraping is a
   nice-to-have enrichment, NOT a dependency.
5. **vaastav/Fantasy-Premier-League** (github.com/vaastav/Fantasy-Premier-League) is
   alive (pushed 2026-06-24), free, and has per-GW per-player data for seasons
   2016-17 → 2025-26 including `cleaned_merged_seasons.csv`. This is the training set.
6. **841 players, 20 teams** currently in the API. Promoted/relegated churn for 26/27
   means newly promoted teams have no PL history — the model needs a promoted-team
   prior (use Championship-season strength discount, see §6.6).

### Key API endpoints (no auth, JSON)
| Endpoint | Contents |
|---|---|
| `bootstrap-static/` | all players, teams, gameweeks/deadlines, chips, scoring rules |
| `fixtures/` | full fixture list, FDR, kickoff times |
| `element-summary/{player_id}/` | per-match history (this + past seasons), upcoming fixtures |
| `event/{gw}/live/` | live points for every player in a GW |
| `entry/{team_id}/` | our team's public info: rank, points, value |
| `entry/{team_id}/event/{gw}/picks/` | our picks for a GW (public after deadline) |
| `entry/{team_id}/history/` | our GW-by-GW history + chips used |
| `entry/{team_id}/transfers/` | our transfer log |

Authenticated `my-team` endpoint is flaky/login-walled — do NOT depend on it. Instead
the app tracks our squad state itself (it decides every move, so it always knows the
squad; reconcile weekly against the public `picks` endpoint after each deadline).

Politeness: cache aggressively (bootstrap changes ~daily outside match days), send a
browser User-Agent, throttle to ≤1 req/sec, never hammer during matches.

---

## 1. What we're building (one paragraph)

A local-first Python system that: (a) ingests FPL API + historical data nightly, (b)
predicts expected points per player per gameweek over an 8-GW horizon using a
decomposed model (minutes × appearance/attacking/clean-sheet/bonus/DEFCON components),
(c) feeds those projections into a MILP optimizer that solves squad selection,
transfers (with hit costs), captaincy, formation, bench order, and chip timing under
all FPL rules PLUS user-defined "Rules" from the UI, (d) serves everything through a
FastAPI backend that our React dashboard consumes, and (e) runs automatically
on a weekly cadence with a human-readable recommendation report before every deadline.

**The formation suggestion the user loved ("3-4-3 if mids outscore forwards") falls out
of the optimizer for free** — the MILP picks the XI that maximizes projected points
subject to valid-formation constraints, so formation is an output, not an input.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  DATA LAYER (nightly cron + pre-deadline refresh)                │
│  fpl_api client ──► DuckDB (single file, columnar, free, fast)  │
│  vaastav loader ──► historical training tables                  │
│  understat scraper (optional enrichment, Selenium fallback)      │
└──────────────┬──────────────────────────────────────────────────┘
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  MODEL LAYER                                                     │
│  features.py: rolling form, per-90s, FDR-adjusted, venue,        │
│               rest days, team strength, set-piece orders         │
│  minutes model (LightGBM classifier → P(0), P(1-59), P(60+))     │
│  team goals model (Dixon-Coles Poisson → CS prob, team xG)       │
│  player points model (LightGBM per position, per component)      │
│  → projections table: E[pts] per player per GW for next 8 GWs   │
│    + uncertainty (quantiles) for captaincy risk assessment       │
└──────────────┬──────────────────────────────────────────────────┘
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  DECISION LAYER                                                  │
│  optimizer.py: MILP (PuLP + HiGHS/CBC solver, both free)         │
│    squad / transfers / captain / formation / bench / chips       │
│  rules_engine.py: UI "Rules" → extra MILP constraints/filters    │
│  chip_planner.py: season-level chip EV scan (DGW/BGW aware)      │
└──────────────┬──────────────────────────────────────────────────┘
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  SERVING LAYER                                                   │
│  FastAPI backend (REST, endpoints in §8)                         │
│  Our React frontend (original design)                            │
│  Weekly markdown/HTML report artifact (the "do this" sheet)      │
│  GitHub Actions: nightly refresh + pre-deadline run              │
└─────────────────────────────────────────────────────────────────┘
```

**Stack decisions (locked):**
- Python 3.11+, `httpx` for API calls, **DuckDB** for storage (single file, SQL,
  zero-ops, brilliant for feature engineering; SQLite acceptable fallback)
- **LightGBM** for ML components (fast, tabular king, free)
- **PuLP + HiGHS** for MILP (HiGHS is the best free solver; `highspy` pip-installable)
- **FastAPI + uvicorn** backend
- Frontend (locked): our own **Vite + React + Tailwind** app, original design. The
  benchmark screenshots are a feature checklist to exceed, not a spec to copy.
- Orchestration: plain Python CLI (`python -m fplscout refresh|project|optimize|report`)
  + GitHub Actions cron. No Airflow/Prefect — single-user app, keep it lean.
- Tests: pytest. Every module gets tests; the optimizer gets golden-case tests.

---

## 3. Repo layout (new repo/dir: `fpl-ai-scout/`)

```
fpl-ai-scout/
├── README.md                  # quickstart + weekly ops runbook
├── pyproject.toml
├── .github/workflows/
│   ├── nightly_refresh.yml    # data pull + projections, commits DuckDB stats
│   └── pre_deadline.yml       # T-24h before each GW deadline: full run + report
├── config/
│   ├── settings.yaml          # team_id, horizon, decay, hit threshold, paths
│   └── rules.yaml             # persisted user rules (mirrors UI Rules page)
├── data/
│   ├── fpl.duckdb             # everything lives here
│   └── raw/                   # cached JSON snapshots (gitignored)
├── src/fplscout/
│   ├── __init__.py
│   ├── cli.py                 # entrypoints: refresh, train, project, optimize, report, serve
│   ├── ingest/
│   │   ├── fpl_api.py         # typed client, caching, retry, throttle
│   │   ├── vaastav.py         # historical loader (one-time + season append)
│   │   ├── understat.py       # OPTIONAL enrichment; Selenium fallback
│   │   └── schemas.py         # pydantic models for every payload
│   ├── features/
│   │   ├── build.py           # feature store builder (SQL-first in DuckDB)
│   │   └── definitions.py     # every feature documented + versioned
│   ├── models/
│   │   ├── minutes.py         # P(0), P(1-59), P(60+) classifier
│   │   ├── team_goals.py      # Dixon-Coles: score-rate, CS prob per fixture
│   │   ├── points.py          # per-position component models → E[pts]
│   │   ├── train.py           # training harness, time-series CV
│   │   └── registry.py        # model versioning (just pickle + metadata json)
│   ├── decide/
│   │   ├── optimizer.py       # MILP: full formulation in §7
│   │   ├── rules_engine.py    # UI rules → constraints
│   │   ├── chip_planner.py    # season chip EV scan
│   │   └── squad_state.py     # our team: picks, bank, FTs, chips used
│   ├── backtest/
│   │   ├── simulator.py       # replay a season GW-by-GW
│   │   └── report.py          # season points vs benchmarks
│   ├── api/
│   │   ├── main.py            # FastAPI app
│   │   └── routers/           # dashboard, transfers, fixtures, rules, analytics
│   └── report/
│       └── weekly.py          # "DO THIS" markdown/HTML report generator
├── frontend/                  # our React app (Vite + Tailwind)
└── tests/
```

---

## 4. Build phases — DO THEM IN ORDER, each has a Definition of Done

### Phase 0 — Scaffold + FPL client (½ day)
- Repo scaffold as §3, pyproject with pinned deps, ruff + pytest configured.
- `ingest/fpl_api.py`: typed client for every endpoint in §0, with on-disk JSON cache
  (respect `ETag`/short TTL), 1 req/s throttle, retry w/ backoff, browser UA.
- `ingest/schemas.py`: pydantic models for bootstrap, fixtures, element-summary,
  entry endpoints. Fail loudly on schema drift (the API WILL change at season reset —
  we want to know the day it happens).
- **DoD:** `python -m fplscout refresh --raw` snapshots all endpoints to `data/raw/`;
  tests pass with recorded fixtures (use `respx` or saved JSON, no live calls in CI).

### Phase 1 — Data layer (1 day)
- DuckDB schema: `players`, `teams`, `fixtures`, `gameweeks`, `player_gw_history`
  (long table: one row per player per GW per season — the core table),
  `our_entry`, `our_picks`, `our_transfers`, `projections`, `recommendations`.
- `ingest/vaastav.py`: pull `cleaned_merged_seasons.csv` + per-season `gws/merged_gw.csv`
  for 2021-22 → 2025-26 (5 seasons is enough; pre-2021 game rules differ too much).
  Normalize player IDs across seasons via `master_team_list.csv` + name matching
  (fuzzy match with manual override file for the ~2% that fail).
- **Season-boundary handling:** current-season rows come from FPL API
  `element-summary`; historical rows from vaastav. Same long-table schema so features
  compute uniformly across the boundary.
- `understat.py`: stub with interface defined; implement only if a backtest shows FPL's
  native xG is insufficient (it likely isn't — don't gold-plate).
- **DoD:** `refresh` populates DuckDB; row-count sanity tests (e.g. 25/26 season has
  ~38 × ~600 player-GW rows); ID-mapping spot checks (Salah, Haaland, one promoted-team
  player traced across 3 seasons).

### Phase 2 — Feature store (1 day)
Features per player per GW (computed as-of deadline, NO leakage — this is the #1 bug
risk; every feature must only use data available before that GW's deadline):
- Rolling windows (3/5/10 GW, decayed): points, minutes, xG, xA, xGI, shots, BPS,
  CBIT/CBIRT (defensive contribution counts), saves, goals conceded
- Per-90 versions of the above, share-of-team versions (player xG ÷ team xG)
- Availability: `status`, `chance_of_playing_next_round`, news recency
- Fixture context: opponent strength (from team model), home/away, FDR, rest days,
  DGW/BGW flags, kickoff congestion
- Team form: rolling team xG for/against, possession proxies
- Set pieces: penalty/corner/FK order (from bootstrap `penalties_order` etc.)
- Price/ownership: `now_cost`, `selected_by_percent`, transfer momentum (for template
  awareness in captaincy risk, NOT as a points predictor)
- Position, price band, promoted-team flag
- **DoD:** `features` table materialized for all 5 historical seasons + current;
  leakage test: for a sampled (player, GW), recompute features using only prior data
  and assert equality with stored row.

### Phase 3 — Models (2-3 days, the heart)
Decomposed expected points, per player per future GW (t+1 … t+8):

1. **Minutes model** (`models/minutes.py`): LightGBM multiclass →
   P(plays 0), P(1-59), P(60+). Features: recent starts, minutes trend, status flags,
   rotation risk (fixture congestion, cup games), manager patterns proxy (team-level
   rotation rate). This is the single highest-leverage model in FPL — a benched premium
   is 0 points. Calibrate probabilities (isotonic).
2. **Team goals model** (`models/team_goals.py`): Dixon-Coles bivariate Poisson fitted
   on ~3 seasons of match scores with time decay, giving per-fixture: E[goals for],
   E[goals against], P(clean sheet). Promoted teams: prior = blend of historical
   promoted-team average + market signal (see §6.6). Pure statsmodels/scipy, no ML.
3. **Attacking points** (`models/points.py`): per position, E[goals] = f(player xG/90
   share × team E[goals]) with LightGBM residual correction; same for assists. Convert
   via scoring matrix (DEF goal = 6, MID = 5, FWD = 4 — READ from bootstrap
   `game_settings`/scoring config, don't hardcode: rules change).
4. **Clean-sheet points:** P(CS) from team model × P(60+ mins) × position CS value.
5. **DEFCON points:** P(hits CBIT/CBIRT threshold) — LightGBM binary per position on
   rolling defensive-action rates vs opponent possession profile. New-ish stat, one
   full season of training data (25/26) — keep the model simple.
6. **Bonus points:** E[bonus] via BPS-share regression (predict BPS, map to E[bonus]
   through historical BPS-rank→bonus distribution per fixture).
7. **Appearance/saves/cards:** appearance pts from minutes model; saves model for GKs
   (rolling saves/90 × opponent shot volume); expected card/OG deductions as small
   flat rates per player history.
8. **Combine:** `E[pts] = Σ components`, plus **q10/q90 quantiles** (train quantile
   LightGBM on total points directly as the uncertainty envelope — used for captaincy
   and chip risk, not for the mean).

**Training protocol:** time-series CV — train on seasons S-4…S-1, validate on season S,
sliding. NO random splits (leakage). Metrics: RMSE on points; Spearman rank corr
within position (ranking is what the optimizer consumes); calibration plots for all
probability outputs.

**Multi-GW horizon:** predict each of next 8 GWs independently (features as-of now,
fixture context per target GW). Apply nothing else — the optimizer handles time value
via decay weights.

- **DoD:** `train` + `project` CLI commands work; validation report generated
  (metrics above, per position); projections beat two baselines on held-out 25/26:
  (a) FPL's own `ep_next`, (b) naive "last-5-GW average". If we can't beat `ep_next`,
  STOP and iterate before building anything downstream.

### Phase 4 — Optimizer (2 days, the muscle)
MILP in `decide/optimizer.py`, full formulation in §7. Solves in one model:
squad-15, budget, ≤3 per club, formation-legal XI, captain+vice, bench order,
transfers with -4 hits, over an 8-GW rolling horizon with per-GW decay (default 0.84^t,
tunable), plus chip decisions injected by `chip_planner.py`.

- Also emit **top-5 alternative moves** with EV deltas (the UI shows suggestions, and
  the human deserves to see the margin — "Palmer IN +8.4" like the mock).
- **Solver budget:** must solve in <60s on a laptop. If the full 8-GW transfer MILP is
  too slow, decompose: solve GW+1 transfers exactly with horizon EVs as terminal
  values (standard approach, plenty good).
- `decide/squad_state.py`: tracks our bank, free transfers (max 5 banked under current
  rules — verify), chips remaining, current 15. Reconciles against
  `entry/{id}/event/{gw}/picks/` after each deadline and SCREAMS if drift detected
  (i.e., the human didn't apply the recommendation).
- **DoD:** golden tests — hand-built 20-player universes where the optimal squad is
  known by hand; hit-taking test (only takes a -4 when EV gain over horizon > 4 +
  margin); formation flexes correctly when a mid outprojects a fwd.

### Phase 5 — Chip planner (1 day)
`decide/chip_planner.py`: for each remaining chip × each feasible future GW, estimate
EV uplift using projections (+ DGW/BGW detection from fixture counts per team per GW):
- **Bench Boost:** EV = projected bench points that GW (maximize by planning a strong
  bench into a DGW).
- **Triple Captain:** EV = best captain's E[pts] that GW (DGWs, or extreme fixture).
- **Free Hit:** EV = (unconstrained best XI for that GW) − (our XI that GW) — best in
  BGWs.
- **Wildcard:** EV = horizon value of re-optimized squad − current squad trajectory.
Output a season chip schedule (soft plan, re-evaluated weekly) + a "chip alert" when
current-week EV exceeds the best projected future EV (fire it now vs save it).
Half-season expiry (GW19) must be modeled — an unused first-half chip is a wasted chip.
- **DoD:** on 25/26 replay, chip planner fires all 8 chips and beats "never chip" by a
  meaningful margin; never recommends a chip outside its valid window.

### Phase 6 — Backtest simulator (1-2 days, the proof)
`backtest/simulator.py`: full-season replay on 2025/26 (train on ≤2024/25 only):
- Start GW1 with optimizer-chosen initial squad under 100.0 budget.
- Each GW: generate projections using only pre-deadline data → optimizer decides
  transfers/captain/bench/chips → score with ACTUAL points → track bank, FTs, hits,
  price changes (use vaastav price data; model price changes simply — actual selling
  price rules: 50% of profit rounds down).
- Report: total points, GW-by-GW, vs benchmarks: 25/26 overall average (~2,200-2,300
  typical for mean manager — pull the real number from `bootstrap-static` of that
  season / vaastav), top-10k cutoff, and "set-and-forget template" baseline.
- **DoD (the go/no-go gate):** simulated season ≥ 2,400 pts on 25/26 replay (roughly
  top-100k-or-better territory; calibrate the exact bar against real 25/26
  distribution once pulled). Also run on 2024/25 as a second sample. If below the bar,
  iterate on Phase 3 — do not proceed to UI polish while the brain underperforms.
  Publish `backtest/report.html` with the evidence.

### Phase 7 — API + build the frontend (2 days)
FastAPI serving the contract in §8. Then build our own frontend from scratch —
Vite + React + Tailwind, original visual identity (not the benchmark's purple theme).
Keep the benchmark's page inventory as a sound feature-list baseline, but exceed it:
- **Page inventory:** Dashboard / Transfers / Fixtures / Signals / Rules / Analytics /
  Settings — same set the benchmark had, own design language throughout.
- **What the benchmark lacked, that we add:**
  - Per-player EV breakdown by component (explainability) — surfaces
    `GET /api/players/{id}/projection` (minutes × attacking × clean-sheet × bonus ×
    DEFCON contributions to E[pts], not just a single number).
  - Predicted-vs-actual accuracy tracker on the Analytics page — shows the model
    being honest about its own error, GW by GW.
  - A dedicated chip-planner view — the season chip schedule + EV-to-fire-now-vs-save
    logic from `chip_planner.py` (Phase 5), visualized, not buried in a report.
- Dashboard: rank/mini-league/GW-points stat cards, AI Strategy Insight card, pitch
  view. Transfers page: OUT/IN compare card, form/xG/fixture bars, predicted net
  points, bank + free transfers + chip strategy cards.
- The **Rules page is functional, not decorative:** each rule persists to
  `config/rules.yaml` and `rules_engine.py` compiles it into optimizer constraints.
  Ship these rule types in v1: min-ownership floor, budget floor after transfers,
  no-captain-first-game-back-from-injury, chip-only-in-DGW, max-hits-per-GW,
  ban/force specific players or teams, formation lock. Free-text rules: parse to a
  structured rule via keyword matching in v1 (LLM parsing is a v2 luxury).
- **DoD:** `fplscout serve` + frontend dev server → every page renders REAL data for
  our actual team ID; toggling a rule changes the next optimize run (test: enable
  "ban player X" → X disappears from recommendations).

### Phase 8 — Automation + weekly ops (½ day)
- GitHub Actions:
  - `nightly_refresh.yml`: 06:00 UTC — refresh data, rebuild projections, commit
    summary stats.
  - `pre_deadline.yml`: dynamic — read next deadline from `gameweeks` table, run T-24h
    and T-2h: full pipeline → optimize → generate report → (optional) email/Telegram
    the "DO THIS" sheet. Telegram bot is free and 20 lines of code; do it.
- `report/weekly.py`: one screen, zero ambiguity:
  ```
  GW7 — deadline Sat 14:30 BST
  TRANSFERS: Salah OUT → Palmer IN (-0 hit) | net +8.4 EV
  CAPTAIN: Haaland (C), Palmer (VC)
  LINEUP: 3-4-3 [names…] | BENCH ORDER: 1. Gusto 2. Garnacho 3. …
  CHIP: none (TC planned GW12 — DGW, EV 14.1)
  CONFIDENCE: 94% | Rules applied: 4 active
  ```
- **DoD:** dry-run both workflows; deadline detection tested against the fixtures
  table; report renders from a real optimize run.

### Phase 9 — Season kickoff (when 26/27 game launches, ~late July)
- Day-0 checklist: re-run schema validation (API reset WILL drift), re-verify chips
  array + scoring rules, load new player list, map promoted teams, retrain with 25/26
  in the training set, run initial-squad optimizer for GW1, register the team.
- Pre-season minutes uncertainty is the big GW1 risk: heavily weight `status`,
  pre-season reports can't be scraped reliably — mitigate by favoring nailed players
  (high last-season start share) in GW1 squad and keeping 2 FTs flexibility.

**Total estimate: ~12 working days of Opus effort. Phases 0-6 are the priority; the
app is worthless with a pretty UI and a dumb brain, and valuable the other way round.**

---

## 5. What Opus should NOT do (scope guards)

- No paid data, no paid solver, no paid hosting. Free tiers only.
- No live in-play features (points update mid-match is a v2 toy; decisions happen
  pre-deadline).
- No multi-user/auth/accounts — single team, single user. The mock's "Manager Name"
  header is cosmetic.
- No Airflow/Kubernetes/microservices. One repo, one DB file, one API process.
- No deep learning. Tabular + Poisson beats transformers here and trains in minutes.
- Don't build Understat/FBref scraping until a backtest proves native FPL xG is the
  bottleneck.
- Mini-league / AI League page: static v1 (just render `entry` league standings from
  the API — it's one endpoint, but no strategy logic against rivals in v1).

---

## 6. Modeling notes & tricks (read before Phase 3)

1. **Minutes is king.** Most projection error is "did he even play". Invest here.
2. **Rank correlation > RMSE.** The optimizer only needs correct ordering + roughly
   calibrated magnitudes for hit decisions.
3. **Decay-weight training samples** (recent seasons matter more; the game's scoring
   changed in 25/26 — DEFCON — so pre-25/26 rows need re-scoring under current rules:
   recompute historical points with today's scoring matrix where fields allow, and
   flag DEFCON as missing-not-zero pre-25/26).
4. **Captaincy = mean + tail.** Pick captain by E[pts] but break near-ties with q90
   (upside) — doubling is a convexity play.
5. **Price changes are second-order.** Model them crudely (transfer-momentum
   heuristic) only to nudge transfer *timing* (early-week vs deadline-day), never to
   override an EV decision. Team value is a means, not the objective.
6. **Promoted teams prior:** for 26/27's promoted sides, initialize Dixon-Coles
   strength at the historical mean of promoted teams' first-season home/away rates
   (compute from vaastav team data), then let the season update it fast (higher
   learning rate for first 6 GWs).
7. **DGW/BGW detection is trivial:** count fixtures per team per GW in `fixtures/`.
   Everything chip-related keys off this.
8. **`ep_next` is the baseline to beat, and also a feature.** FPL's own expected
   points is decent — include it as a model input (it encodes injury news we might
   lag on) while proving we outperform it.

---

## 7. MILP formulation (implement exactly, then extend)

Sets: players `p`, positions (GK/DEF/MID/FWD), clubs `c`, horizon GWs `t ∈ {1..8}`.
Inputs: `ev[p][t]` projected points, `price[p]`, current squad, bank, free transfers.

Variables (binary unless noted):
- `squad[p][t]` in 15-man squad at t; `xi[p][t]` in starting XI; `cap[p][t]`,
  `vice[p][t]`; `bench_k[p][t]` bench slot k∈{1,2,3}; `buy[p][t]`, `sell[p][t]`;
  `hits[t]` integer ≥0; `ft[t]` free transfers available (state).

Objective:
```
max Σ_t decay^t · [ Σ_p ev[p][t]·(xi[p][t] + cap[p][t])          # captain doubles
                   + Σ_p Σ_k bench_weight_k · ev[p][t] · bench_k[p][t]  # autosub EV, weights ≈ [0.25, 0.15, 0.05]
                   − 4·hits[t] ]
```
Constraints per t: squad size 15 (2 GK/5 DEF/5 MID/3 FWD); XI size 11 with formation
bounds (1 GK, 3-5 DEF, 2-5 MID, 1-3 FWD); xi ⊆ squad; one cap, one vice, cap≠vice,
cap ∈ xi; ≤3 per club; budget: Σ price·squad ≤ bank + sale proceeds (model selling
price = purchase + ⌊profit/2⌋·0.1 — track purchase prices in squad_state); transfer
flow conservation squad[t] = squad[t-1] + buy − sell; hits[t] ≥ (buys[t] − ft[t]);
FT banking dynamics ft[t+1] = min(ft[t] − used + 1, 5); rules-engine constraints
appended dynamically. Chip modes: wildcard/free-hit relax transfer costs for one t
(free hit also reverts squad after t); bench boost adds full-EV bench that t; triple
captain sets captain multiplier 3 — implement as solver re-runs with mode flags, NOT
extra binaries (keeps it fast and debuggable).

---

## 8. API contract (matches the screenshots — build exactly these first)

```
GET  /api/dashboard          → { gw, deadline, is_live, avg_points, our_points,
                                 overall_rank, mini_league: {pos, size},
                                 insight: {text, transfer_summary, captain},
                                 bench_order: [{name, team, ev}], pitch: {gk:[], def:[], mid:[], fwd:[]} }
GET  /api/transfers/optimal  → { gw, confidence, bank, free_transfers,
                                 chip_advice: {chip, gw, ev} | null,
                                 moves: [{out: {player card + next_opp + ev3gw},
                                          in:  {player card + next_opp + ev3gw},
                                          compare: {form, xg90, fdr}, net_ev}],
                                 alternatives: [...top5] }
POST /api/transfers/confirm  → marks recommendation as applied (updates squad_state)
GET  /api/fixtures?horizon=8 → per-team fixture ticker with FDR + DGW/BGW flags
GET  /api/signals            → price-change risk, injury news deltas, ownership swings
GET  /api/rules              → [{id, title, body, enabled}]
POST /api/rules  PATCH /api/rules/{id}  DELETE /api/rules/{id}
GET  /api/analytics          → projection accuracy tracker (predicted vs actual by GW),
                                 backtest summary, model version
GET  /api/players/{id}/projection → per-GW EV breakdown by component (explainability)
POST /api/refresh            → trigger pipeline (the "Refresh Stats" button)
```
Notes: "AI Confidence: 94%" in the mock = map from optimizer EV margin + projection
quantile spread to a 0-100 display number; document the formula, don't fake it.

---

## 9. Weekly ops runbook (goes in README)

1. **Tue/Wed:** nightly refresh runs; glance at Signals page for price/injury moves.
   Early transfer ONLY if the app says EV-safe and price-fall risk is real.
2. **T-24h:** pre-deadline workflow fires → report + Telegram. Review, then execute
   moves in the official FPL app/site exactly as written.
3. **T-2h:** final run (late injury news). If recommendation changed, it says why.
4. **Post-deadline:** reconciliation job checks our actual picks match the
   recommendation; mismatch = red banner in dashboard.
5. **Monday:** actuals ingested; Analytics page updates predicted-vs-actual tracker.

---

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| 26/27 API reset breaks schemas | pydantic strict models + Day-0 checklist (Phase 9); CI runs schema validation nightly |
| Scoring rules change again for 26/27 | scoring matrix READ from bootstrap at runtime, never hardcoded |
| Model overfits one season | validate on two held-out seasons (24/25 AND 25/26); prefer simple models |
| Leakage inflates backtest | leakage unit test (Phase 2 DoD); features computed strictly as-of deadline |
| DEFCON has 1 season of data | simple model + shrinkage to position means |
| GW1 squad flops (pre-season fog) | favor nailed players GW1-2; wildcard #1 held as correction tool (valid from GW2) |
| Human forgets to apply moves | Telegram nudges + post-deadline reconciliation alarm |

---

## 11. Inputs needed from the human (collect before Phase 7; Phases 0-6 don't block)

1. **FPL team ID** for 26/27 (exists only after registering when the game launches).
2. Telegram bot token + chat ID if notifications wanted (5-min setup, free).
3. Mini-league ID (for the AI League page).

---

## 12. Definition of "first draft done"

- [ ] Phases 0-8 complete, all DoDs met, tests green
- [ ] Backtest report shows ≥ target on 25/26 replay AND beats `ep_next` picker
- [ ] Dashboard, Transfers, Fixtures, Rules, Analytics pages live on real data
- [ ] One full dry-run of the weekly cycle executed end-to-end
- [ ] README runbook written so the humans can operate it without Claude

Then bring it back for review — the review pass will hit: model error analysis,
chip planner tuning, captaincy tail-risk policy, and rank-aware strategy (when to
chase vs protect in the mini-league).
