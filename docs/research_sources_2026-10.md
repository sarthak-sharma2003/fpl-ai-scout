# Free data sources and open-source techniques for the FPL + UCL Fantasy predictors

Research date: 2026-10-13 (delegated research pass; sources verified from a sandbox that could reach only GitHub-hosted data — see below). Drives the 2026-10 model-v2 work: pre-deadline availability features (Recipe 1), predicted line-ups (Recipe 2, used by the UCL scout), and the backlog in §7.

## How I checked things

- **Reachability from the dev sandbox.** Only `raw.githubusercontent.com` and `git clone` of public repos worked. `api.github.com` refused repos outside the session, `github.com` HTML returned 403, and arxiv, uefa.com, understat and similar sites did not resolve. Everything marked "verified" below was checked by cloning or downloading the GitHub-hosted data myself, including its git history and commit timestamps. Facts about non-GitHub sites come from web search plus other projects' documented live tests. Those are labelled with their source, and anything I could not confirm is marked **UNVERIFIED**.
- **Leak-safe** means a value at gameweek/matchday N could have been known before that deadline.

---

## 0. Headline findings (read these first)

1. **You can train on pre-deadline availability and `ep_next` now, not after 15+ gameweeks of your own archive.** [`Randdalf/fplcache`](https://github.com/Randdalf/fplcache) is a GitHub Actions archive of `bootstrap-static`. It has run 3–4 times a day since **2021-04-18** (7,967 snapshots; last commit 2026-10-13 00:28 UTC) and is Unlicense/public domain. It covers every season you backtest (2021-22 to 2025-26). [`qazybekb/smartplayfpl-dastan`](https://github.com/qazybekb/smartplayfpl-dastan) (MIT) has already turned it into provenance-checked tables:
   - `data/pre_deadline_signals.parquet`: 137,982 rows of status, chance of playing, news flag, set-piece orders, age and days at club, 2021-22 to 2025-26.
   - `data/pre_deadline_ep_next.parquet`: 142,173 rows.
   - Median capture is 4.3 h before the deadline and **0 rows were captured after it**.

   This contradicts ROADMAP §2.3 and §7.1 ("historical snapshots leak, wait for OUR archive"). In Dastan this family was the **largest feature gain (+0.0064 on their objective)**. Most of the gain is in the all-player cohort; it is close to zero among starters.
2. **Your per-gameweek Spearman of 0.22–0.27 is probably not weak.** `train.py` computes it on "plausible starters" (expected minutes ≥ 45 or P(60+) ≥ 0.5). Comparable public figures on starters-only cohorts:

   | Model | Data window | Per-GW Spearman (starters) |
   |---|---|---|
   | Dastan, walk-forward | 2024-25 GW15–38 | 0.288 |
   | Dastan | 2025-26 GW1–24 | 0.127 |
   | OpenFPL | 2025-26 GW1–24 | 0.133 |

   On all players the figure is about 0.70–0.75. Gains will come from minutes and availability, not from ranking starters better. Dastan also measured **seed noise of 0.0106** on its objective, larger than most feature effects. Use multi-seed, paired comparisons.
3. **The ClubElo API appears broken for 2026-27.**
   - Your own `ucl/.../strength.py` (checked 2026-09-08) records that `/Fixtures` returns "Fixtures API deactivated" and the per-club endpoint returns 502.
   - In FPL-Core-Insights the Elo columns are populated through 2025-26 but **blank for every 2026-27 file**.
   - pabloroldan98's ClubElo-derived JSON has been stale since 2026-08-31.

   Working alternatives: EloFootball (HTML) and, better, market odds.
4. **Free player-level odds exist, through prediction markets plus one bookmaker CDN:**
   - **Kalshi `KXEPLGOAL`** (EPL anytime scorer, public API, no key) has hourly price history since **2025-12-02**. It is the only free *historical* player-prop archive I found.
   - **Kambi (`ub` = Unibet international) CDN** serves live "To Score", "To give an assist" and "Player's shots on target" markets with no key.
   - **Pinnacle's guest API has no per-match soccer goalscorer props**, but it does carry team totals for EPL and UCL.
5. **A free GitHub archive of predicted lineups exists for EPL *and* UCL.** [`pabloroldan98/knapsack-football-formations`](https://github.com/pabloroldan98/knapsack-football-formations) commits start probabilities 2–3 times a day, scraped from RotoWire, Sportsgambler and FutbolFantasy, since Sept 2025. That gives roughly one season of point-in-time lineup signal through git history. Its current UCL files cover all 36 clubs.
6. **FBref lost all advanced (Opta) stats on 20 Jan 2026** and returns 403 to datacenter IPs. **FiveThirtyEight SPI** is dead: the GitHub folder is only a README and the CSV links 404. Neither is a viable source.
7. **The UCL calendar is confirmed: MD2 is 13–14 Oct 2026, so the MD2 deadline is today** (first kickoff 18:45 CEST on Tue 13 Oct; check the exact in-app deadline). MD1 was **8–10 Sep** (three days). Details in §4.

---

## 1. Ranked source table

Impact and effort are H (high), M (medium) or L (low). "GitHub?" means hosted on GitHub, so reachable from the dev sandbox.

| # | Source | What it gives | Free / no login? | GitHub? | In-season updated? | Leak-safe for backtest? | Impact | Effort | URL |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **Randdalf/fplcache** (+ Dastan's prebuilt parquet) | Full `bootstrap-static` snapshots 3–4×/day since 2021-04: `status`, `chance_of_playing_next/this_round`, `news`, `news_added`, `ep_next`, `ep_this`, set-piece orders, price, ownership, transfers | Yes (Unlicense; Dastan MIT) | **Yes** | Yes (last commit 2026-10-13) | **Yes**, if you pick the last snapshot before the deadline and require that gameweek to be `is_next` | **H** (FPL minutes and availability) | L–M | https://github.com/Randdalf/fplcache · https://github.com/qazybekb/smartplayfpl-dastan/tree/main/data |
| 2 | **pabloroldan98/knapsack-football-formations** (lineup JSON) | Start probability per player per team for the next match. Sources: RotoWire (EPL, UCL), Sportsgambler (EPL, UCL), FutbolFantasy (UCL; EPL stale). Also Transfermarkt penalty takers/savers, Sofascore season ratings, Elo | Free; **no licence file**; scraped third-party data, so private use only | **Yes** | Yes, 2–3 commits/day | **Yes, through git commit timestamps**, from 2025-09-09 / 2025-09-16 | **H** (FPL and UCL minutes) | M (name matching) | https://github.com/pabloroldan98/knapsack-football-formations/tree/master/json_files |
| 3 | **Kalshi public API** (`KXEPLGOAL`; `KXUCLGOAL` UNVERIFIED) | Anytime-scorer ("Player: 1+ goals") prices with hourly candles; settled result | Market data needs no key (per Matttgic's tests; Kalshi auth docs disagree in places) | No | Yes | **Yes**, use the candle before the deadline, never `last_price`. History starts 2025-12-02 (~7.5k EPL markets) | **H** (FWD/MID attacking returns) | M | https://api.elections.kalshi.com/trade-api/v2 · e.g. https://kalshi.com/markets/kxeplgoal/epl-goalscorer/kxeplgoal-26oct10muntot |
| 4 | **Kambi CDN, operator `ub`** (Unibet) | Live 1X2/totals plus **player "To Score", "To give an assist", "Player's shots on target"** for EPL, UCL and others (odds in thousandths) | Yes, no key | No | Live only | Only if you archive it yourself, starting now | **H** live / M backtest | M | `https://eu-offering-api.kambicdn.com/offering/v2018/ub/...` (tested 2026-10-02 by Matttgic/Overstreamlit) |
| 5 | **olbauday/FPL-Core-Insights** (renamed from FPL-Elo-Insights) | Opta-style per-match player stats (xG, xA, xGOT, touches in box, chances, **recoveries**, tackles, interceptions, clearances, blocks, start/finish minute). Shot-level xG. **All competitions for PL players** (UCL, UEL, UECL, EFL Cup, FA Cup, friendlies). Per-GW FPL snapshots | Yes ("feel free to use… link back") | **Yes** | Yes, ~3×/day (README says 07:30 and 17:30 UTC) | Match stats yes, as lagged features. Per-GW status snapshots: see caveats in Recipe 4 | **M–H** (congestion, DefCon/recoveries, UCL for PL players) | M | https://github.com/olbauday/FPL-Core-Insights |
| 6 | **Pinnacle guest API** | Sharp 1X2, totals, spreads and **team totals** for EPL (league 1980), UCL (2627), La Liga 2196, Serie A 2436, Bundesliga 1842, Ligue 1 2036, UEL 2630. No soccer goalscorer props | No key required (public front-end key optional) | No | Live only | Only if self-archived | **H for UCL team strength**, M for FPL live | L–M | `https://guest.api.arcadia.pinnacle.com/0.1/leagues/{id}/matchups` and `/markets/straight` |
| 7 | **ESPN site/core API** (`soccer/uefa.champions`, `eng.1`, `esp.1`, …) | Scoreboard; **odds with open/close kept after the match** (DraftKings id 100, Bet365 id 2000); `teams/{id}/injuries`; `summary?event=` with rosters and confirmed XIs about 1 h before kickoff | Yes, unofficial | No (docs on GitHub) | Yes | Opening lines yes; closing lines no; confirmed XIs are post-deadline | **M–H** (historical UCL odds for backtests) | M | https://github.com/pseudo-r/Public-ESPN-API/blob/main/docs/sports/soccer.md |
| 8 | **Understat AJAX JSON** (`getLeagueData/{EPL,La_liga,Bundesliga,Serie_A,Ligue_1,RFPL}/{season}`, `getPlayerData/{id}`, `getMatchData/{id}`; needs header `X-Requested-With: XMLHttpRequest`) | Domestic per-match xG, xA, shots, key passes, minutes and position for 6 leagues | Yes | No | Yes | Yes, as lagged features | **M** (UCL domestic form); L for FPL (FPL API already has xG) | M | https://understat.com (site moved to AJAX around Jan 2026; returned 200 from a datacenter IP on 2026-10-02 per Matttgic) |
| 9 | **Dastan FPL↔Understat ID maps** | `fpl_code` ↔ `understat_id`: 1,564 audited historical players; 2026-27 roster 627 of 654 mapped | Yes (MIT) | **Yes** | Updated 2026-09-07 | n/a (identity table) | M (join enabler) | L | https://github.com/qazybekb/smartplayfpl-dastan/tree/main/data/mappings |
| 10 | **AIrsenal data files** | `player_attributes_history_2526.csv` and `_2627.csv`: **daily timestamped** price, selected, transfers, `news`, `chance_of_playing_next_round`. Also `absences_1516…2526.csv`: Transfermarkt injury spells with from/until dates | Yes (licence: check repo) | **Yes** | Daily bot commit (last 2026-10-12) | Attribute history yes (timestamps). Absences **no**: `until` and `days` are known only afterwards, so use them as labels, not features | M (backup to #1; injury-duration labels) | L | https://github.com/alan-turing-institute/AIrsenal/tree/main/src/airsenal/data |
| 11 | **football-data.co.uk `fixtures.csv` and season CSVs** (already used) | Pre-closing average 1X2, O/U 2.5 and Asian handicap for upcoming and past fixtures across 20+ European divisions | Free, but ToS reads "intended for private individuals only, NOT commercial or data training products using automated bots/scrapers/AI" | No (mirrors exist) | Yes (twice a week or more) | Pre-closing `Avg*` mostly yes (collected Fri ≤ 17:00 / Tue ≤ 13:00 UK, "generally"). **`…C…` closing columns are not** | Already in use | — | https://www.football-data.co.uk/fixtures.csv |
| 12 | **RotoWire lineups page** (live) | Predicted XIs with injury tags and confirmed flag; `?league=EPL`, `UCL`, `UEL`, … | Free HTML; ToS unclear | No | Live | Only if self-archived (or use #2) | M (redundant with #2) | M | https://www.rotowire.com/soccer/lineups.php?league=UCL |
| 13 | **EloFootball** | Cross-league club Elo, HTML per country and season | Free HTML | No (pabloroldan98 archives a scaled copy) | Yes (knapsack copy updated 2026-10-01) | Only through dated snapshots | L–M (UCL strength fallback) | M | https://www.elofootball.com/ |
| 14 | **ClubElo** | Daily club Elo (`api.clubelo.com/YYYY-MM-DD`, `/{Club}`, `/Fixtures`) | Free | No | **Degraded**: `/Fixtures` deactivated, per-club 502 (your own check 2026-09-08); FPL-Core-Insights Elo blank for 2026-27 | Historical pre-match Elo for PL matches is in Core-Insights 2024-25 and 2025-26 `matches.csv` | L | http://clubelo.com/API |
| 15 | **open-fpl-solver** (formerly `sertalpbilal/FPL-Optimization-Tools`, now `solioanalytics/open-fpl-solver`) | Multi-period HiGHS MILP with chips, FT-banking values, decay, bench weights, noisy "sensitivity" re-solves | Yes | **Yes** | Active (2026-09-15) | n/a | M (decision layer) | M | https://github.com/solioanalytics/open-fpl-solver |
| 16 | **OpenFPL models** | ~250 XGBoost/random-forest regressors (5 CV folds × top-K × 5 position models incl. Assistant Manager), feature scaler, sample features | Yes (MIT) | **Yes** (models about 720 MB) | Not updated since 2025-08 | Its features are leak-safe rolling windows | M (ensemble member / benchmark) | M | https://github.com/daniegr/OpenFPL |
| 17 | **Polymarket per-match player props** (EPL "player props" events) | Anytime-scorer markets; documented to resolve "No" if the player does not play | Public API (no key) | No | Yes | Through price history; thin liquidity, history patchy | L–M | e.g. https://polyguana.com/events/epl-ful-bou-2026-05-09-player-props (third-party mirror) |
| 18 | **FPL `team/set-piece-notes/`** | Club set-piece notes text | Yes | No | Yes | Live only | L (Dastan: declared set-piece order **+0.0003**, rejected) | L | https://fantasy.premierleague.com/api/team/set-piece-notes/ |
| 19 | **Opta Power Rankings** (`dataviz.theanalyst.com/opta-power-rankings/index.js`) | Global club ratings 0–100 | Free JS blob | No | Daily | **No history**, so not backtestable unless you archive it | L | https://dataviz.theanalyst.com/opta-power-rankings/ |
| 20 | **openfootball/** (football.json, champions-league, …) | Fixtures and results only, public domain | Yes | **Yes** | Yes | Yes | L | https://github.com/openfootball |
| 21 | **vaastav/Fantasy-Premier-League** (already used) | Historical per-GW rows | Yes | **Yes** | **Not reliably**: 2026-27 has GW1 only, last commit 2026-08-28 | `xP` contaminated (README caveat, #222); `players_raw.csv` is end-of-season (leaks `chance_of_playing` and set-piece orders) | — | — | https://github.com/vaastav/Fantasy-Premier-League |
| 22 | The Odds API / odds-api.io / OddsPapi / PropLine | Bookmaker odds incl. some player props | **Free key required**; Odds API history is paid | No | Yes | No free history | L (constraint) | M | https://the-odds-api.com |
| 23 | Premier Injuries / FFS team news / Fantasy Football Hub / BeatFPL / Onside lineups | Injury tables and predicted XIs (HTML) | Free pages; FFS full XIs are member-only; ToS unclear | No | Yes | Live only | L–M | M–H | e.g. https://www.premierinjuries.com |
| 24 | FiveThirtyEight SPI | Club ratings and forecasts | Dead (GitHub has README only; CSV links 404); data ends June 2023 | README only | No | — | L | https://github.com/fivethirtyeight/data/tree/master/soccer-spi |
| 25 | FBref / Sofascore / FotMob | Player stats | FBref advanced stats removed 20 Jan 2026 and 403 for datacenters. Sofascore is Cloudflare-blocked for datacenter IPs. FotMob needs a per-request `x-mas` token | No | — | — | L | — |
| 26 | Betfair exchange read-only / Oddschecker | Odds | Betfair returns 403 to datacenter IPs and needs login plus app key. Oddschecker is Cloudflare-protected and its ToS forbids scraping | No | — | — | **Not viable** | — | — |

**GitHub-hosted (usable in the dev sandbox):** #1, #2, #5, #9, #10, #15, #16, #20, #21, plus documentation-only repos for ESPN, Kambi and Kalshi.

**Not on GitHub (GitHub Actions only):** Kalshi, Kambi, Pinnacle, ESPN, Understat, RotoWire, EloFootball, ClubElo, football-data, UEFA, Polymarket.

---

## 2. Integration recipes for the top 5

### Recipe 1: pre-deadline FPL availability and `ep_next` (fplcache, Dastan shortcut)

**Fastest path (training data for 2021-22 to 2025-26 today):**
- Files: `https://raw.githubusercontent.com/qazybekb/smartplayfpl-dastan/main/data/pre_deadline_signals.parquet` (448 KB) and `.../data/pre_deadline_ep_next.parquet` (362 KB). Both returned HTTP 200 from the sandbox.
- Keys: `(season, gameweek, fpl_code)`; `element_id` is also present.
  - `fpl_code` is FPL `elements[].code` and is stable across seasons. Element ids are renumbered every season: 1,130 of 1,959 players changed id.
- Columns: `sig_status_risk` (ordinal, 0 available to 4 unavailable), `sig_chance_playing`, `sig_has_news`, `sig_pens_order`, `sig_fk_order`, `sig_corners_order`, `sig_age_years`, `sig_days_at_club`, `ep_next`.
- Coverage: signals start in 2021-22. `ep_next` covers 2020-21 partially and about 99.4% afterwards.
- **Missing must be −1, not 0.** 54,430 rows have a genuine `ep_next` of 0.

**Rebuild path (any field, including 2026-27):**
1. Index: `git clone --filter=blob:none --no-checkout https://github.com/Randdalf/fplcache` and list `cache/` (about 8k paths).
2. Path pattern: `https://raw.githubusercontent.com/Randdalf/fplcache/main/cache/{YYYY}/{M}/{D}/{HHMM}.json.xz`. Month and day are not zero-padded; HHMM is UTC; files are LZMA.
3. For each `(season, gw)`, read `deadline_time` from any in-season snapshot. Take the **latest snapshot whose capture time is before the deadline**. Accept it only if `events[gw].is_next` is true **and** its `deadline_time` matches. This is Dastan's acceptance test; reuse `dastan/rebuild/fplcache.py`.
4. Extract `elements[]`: `id`, `code`, `status`, `chance_of_playing_next_round`, `chance_of_playing_this_round`, `news`, `news_added`, `ep_next`, `penalties_order`, `corners_and_indirect_freekicks_order`, `direct_freekicks_order`, `now_cost`, `selected_by_percent`, `transfers_in_event`, `transfers_out_event`, `form`.
5. Gotcha: FPL sends `chance_of_playing_next_round = null` for fully available players. Map `null` with `status == 'a'` to 100. FPL-Core-Insights writes these nulls as 0.0, which is wrong.
- Verified: snapshot `cache/2026/10/13/0028.json.xz` decodes, and its next event is GW7 with deadline `2026-10-17T10:00:00Z`.
- Cadence: about every 6 h (3–4 commits/day), so freshness before a deadline is ≤ ~6 h. Dastan's median age is 4.3 h, maximum 6.2 h.

**Use in your stack:**
- Feed `status_risk`, `chance_playing` and `has_news` into the **minutes model** (P(start), P(60+)). The points model gets them indirectly.
- Join `ep_next` as a feature, which is your ROADMAP §7.1, with no waiting.
- Keep your own `ep_next_archive.csv` as redundancy.

**Expected gain:**
- Dastan: availability family +0.0064 (positive on 3 independent windows: +0.0036, +0.0107, +0.0057). Adding `ep_next` to an already strong model gave only +0.0018 (inside noise).
- HugoReel: perfect minutes knowledge is worth 0.280 MAE against 0.079 for perfect rate knowledge, and its flag-free minutes model captured only 20% of that gap. Availability flags were its "single most valuable addition".
- lagnis337's ablation was inconclusive, but its snapshots averaged about 10 days old. Freshness matters.

### Recipe 2: predicted-lineup signal for FPL and UCL (knapsack archive, live RotoWire)

**Files** (default branch is `master`):
- `https://raw.githubusercontent.com/pabloroldan98/knapsack-football-formations/master/json_files/{src}_{comp}_players_start_probabilities.json`
  - `src` ∈ {`rotowire`, `sportsgambler`, `futbolfantasy`}; `comp` ∈ {`premier`, `champions`}.
  - Also: `transfermarket_{premier,champions}_penalty_takers.json` (last ~6 penalties per taker, scored/missed) and `..._penalty_savers.json`.

**Format:** `{team_name: {player_name: prob}}`, where prob is a coarse level:
- 0.8 = in predicted XI; 0.5 or 0.4 = doubtful; 0.2 = bench; 0.0 = out; 1.0 = confirmed (FutbolFantasy).

Current coverage, verified on HEAD:

| File | Teams | Players |
|---|---|---|
| RotoWire UCL | 36 | 517 |
| FutbolFantasy UCL | 36 | 880 |
| Sportsgambler UCL | 36 | 396 (XI only) |
| RotoWire EPL | 19 | 301 |
| Sportsgambler EPL | 20 | 220 |

**History (point-in-time), verified:**

| File | Versions | Since |
|---|---|---|
| RotoWire EPL | 731 | 2025-09-16 |
| RotoWire UCL | 598 | 2025-09-16 |
| Sportsgambler EPL | 372 | 2025-09-16 |
| FutbolFantasy UCL | 223 | 2025-09-09 |

Recent cadence is about 02:00, 08:30 and 14:00 UTC daily.
- Recipe: `git clone --filter=blob:none https://github.com/pabloroldan98/knapsack-football-formations`, then `git log --format='%H %cI' -- json_files/<file>`, choose the last commit before the deadline, then `git show <sha>:json_files/<file>`.
- Check that the file refers to the target fixture: it holds each team's *next* match, so after midweek games it may already point to the following match.

**Join:**
- FPL: normalised name (unidecode, lowercase, token-set ratio) **within team** against `first_name + second_name` and `web_name`, then to `element`/`code`. Keep an `overrides.csv`.
- UCL: same approach against UEFA feed player names and club. RotoWire uses names like "Sporting Portugal".

**Features:**
- `p_lineup_rw`, `p_lineup_sg`, `p_lineup_ff`, their mean and max, `n_sources_in_xi`, and flags for doubtful and out.
- For UCL, take the Tuesday-morning snapshot for the deadline and the Wednesday snapshot for the captain-switch and sub decisions.

**Live alternative:** scrape RotoWire directly at `https://www.rotowire.com/soccer/lineups.php?league=EPL` (or `UCL`). The HTML has `li.lineup__player`, `div.lineup__pos`, `span.lineup__inj` and confirmed/expected containers. Parsers already exist in `lewis-king/fpl-mcp-server/src/fpl_server/rotowire_scraper.py` and `pabloroldan98/.../rotowire.py`.

**Caveats:**
- The knapsack repo has no licence and scrapes sites whose ToS may forbid it. Keep it private and do not republish.
- Only about one season of history exists, so validate on 2025-26 GW6+ and 2026-27.

**Expected gain:** not publicly measured. Dastan lists "lineup/team-news closer to the deadline" as its #3 untested idea. Given the minutes-attribution numbers above, this is where the remaining minutes error sits.

### Recipe 3: player goal/assist market probabilities (Kalshi for backtests, Kambi live)

**Backtest (EPL, 2025-12-02 onward), Kalshi:**
- List settled markets: `GET https://api.elections.kalshi.com/trade-api/v2/historical/markets?series_ticker=KXEPLGOAL&limit=1000&cursor=…`. Open markets: `/markets?series_ticker=KXEPLGOAL`.
- Fields: `ticker`, `event_ticker` (e.g. `KXEPLGOAL-26OCT10MUNTOT`), `title` (`"<Player>: 1+ goals"`), `result` (yes/no), `open_time`, `close_time`.
- Prices: `GET /historical/markets/{ticker}/candlesticks?start_ts=…&end_ts=…&period_interval=60`. For recent markets use `/series/KXEPLGOAL/markets/{ticker}/candlesticks`.
- Take **mid = (yes_bid_close + yes_ask_close)/2 at the last hourly candle before the FPL deadline**. Never use `last_price`, which is post-match.
- Rate-limited (429): back off.
- Related series named in Matttgic's helper: `KXEPLFIRSTGOAL`, `KXLALIGAGOAL`, `KXSERIEAGOAL`, `KXBUNDESLIGAGOAL`, `KXLIGUE1GOAL` (no archive yet), `KXUCLGOAL` (**UNVERIFIED** whether it exists or has history).
- Settlement when a player does not play: **UNVERIFIED for Kalshi**. Polymarket's equivalent resolves "No", which makes the price an *unconditional* P(play and score).

**Live (EPL and UCL), Kambi:**
- Event list: `https://eu-offering-api.kambicdn.com/offering/v2018/ub/listView/football/england/premier_league/all/matches.json?lang=en_GB&market=GB&useCombined=true`.
  - For the UCL path, discover it via `.../ub/group.json?lang=en_GB&market=GB`. The exact path (for example `football/champions_league`) is **UNVERIFIED**.
- Event detail: `https://eu-offering-api.kambicdn.com/offering/v2018/ub/betoffer/event/{eventId}.json?lang=en_GB&market=GB&includeParticipants=true`. Filter `criterion.englishLabel` for "To Score", "To give an assist", "Player's shots on target" and "To score at least 2 goals".
- Odds are integers ×1000. Player markets usually appear only a few days before kickoff.
- Bookmaker scorer bets are normally **void if the player does not take part** (UNVERIFIED for Kambi), which makes the price conditional on playing.
- **Archive every snapshot from now on** (Parquet in your repo) so 2026-27 becomes backtestable.

**Transform:**
- Remove the margin. Scorer books carry large, longshot-heavy overround, so normalise per team so that Σ_i λ_i ≈ market team λ (from team totals or 1X2+O/U) × (1 − own-goal share). λ_i = −ln(1 − p_i).
- If the market is conditional on playing, multiply by your P(play) and minutes share. If unconditional (Kalshi/Polymarket), you can back out the market's implied P(start).
- Assist probabilities: Kambi "To give an assist" works the same way. Shots-on-target lines feed a bonus/BPS proxy.

**Join:** player name + team + kickoff date, mapped to FPL `element` (season) or `code`, and to UEFA player id for UCL.

**Expected gain:** no public FPL study quantifies it. HugoReel's odds-only baseline lacked scorer odds and named that as its weakest point. Kalshi liquidity is thin on fringe players, so use it as a feature with a flag for when it is missing, not as ground truth.

### Recipe 4: all-competition minutes, Opta-style stats and recoveries (FPL-Core-Insights)

**Paths:**
- 2025-26 and 2026-27: `https://raw.githubusercontent.com/olbauday/FPL-Core-Insights/main/data/{2025-2026|2026-2027}/By%20Gameweek/GW{n}/{playermatchstats,matches,player_gameweek_stats,playerstats,players,teams}.csv`. 2026-27 also has `shots.csv` (shot-level xG/xGOT, situation, body part) and `xg_by_minute.csv`.
- Per competition: `.../By%20Tournament/{Premier%20League|Champions%20League|Europa%20League|Conference%20League|EFL%20Cup|FA%20Cup|Friendlies|Uefa%20Super%20Cup}/GW{n}/…`.
- 2024-25 uses a different layout: `data/2024-2025/{matches,playermatchstats,playerstats}/GW{n}/…`.

**Keys (verified against fplcache):**
- `player_id` = FPL element id for that season; `player_code` = FPL `code`.
- Team ids and codes are FPL's. Non-PL opponents in European games are blank.
- `match_id` is a slug, e.g. `26-27-prem-arsenal-vs-leeds-united`.

**Useful fields:** `minutes_played` (authoritative), `start_min`, `finish_min`, `xg`, `xa`, `xgot`, `touches_opposition_box`, `chances_created`, `big_chances_missed`, `recoveries`, `tackles_won`, `interceptions`, `blocks`, `clearances`, `saves`, `xgot_faced`, `goals_prevented`. Match level: team xG split into open play, set play and non-penalty xG.

**Use it for:**
- (a) **All-competition load**: minutes in the last 3/7/14 days across UCL, UEL, UECL and cups. This is Dastan's #1 open lead; its PL-only congestion feature measured as a coin flip.
- (b) Rolling defensive-workload features: Dastan's **strongest family, +0.0171**.
- (c) UCL scout: PL players' UCL and domestic stats, including recoveries (1 UCL point per 3).

**Leak rules:**
- Use only matches with `kickoff_time < deadline` and roll with a shift.
- The per-GW `playerstats` snapshots get **rewritten later**. The 2025-26 GW2–10 files were re-committed on 2025-11-19, and `news` was back-filled for about 83 extra rows. For status flags use fplcache (Recipe 1), or reconstruct from this repo's git history with `git log --until=<deadline>`.
- 2024-25 GW1–19 snapshots are **back-filled**: the repo started 2024-12-30.
- **Cadence:** about 3 commits/day (1,846 commits since Dec 2024).

### Recipe 5: market team strength for UCL (and a live FPL fallback)

**Live:**
- Pinnacle guest API: `GET https://guest.api.arcadia.pinnacle.com/0.1/leagues/2627/matchups` (UCL; 1980 = EPL) and `GET .../leagues/2627/markets/straight`.
  - Use `type ∈ {moneyline, total, team_total, spread}` with `period == 0`. Prices are American odds.
  - Optional header `X-API-Key` from `https://www.pinnacle.com/config/app.json` (`api.haywire.apiKey`).
  - `team_total` gives each side's goal line directly. Fit Poisson λ_home and λ_away to the totals and 1X2 after Shin de-vigging; `penaltyblog` on PyPI implements this.
  - It returned 200 from a datacenter IP on 2026-10-02 (Matttgic). **Whether GitHub Actions' US Azure IPs are geo-blocked is UNVERIFIED.**
  - football-data.co.uk says Pinnacle's *API feed to them* has been unreliable since 23/07/2025 and dropped Pinnacle columns from its 2026-27 files. That concerns their feed, not the guest endpoint.
- ESPN, robust from US runners:
  - Event ids: `https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/scoreboard?dates=YYYYMMDD`.
  - Odds: `https://sports.core.api.espn.com/v2/sports/soccer/leagues/uefa.champions/events/{id}/competitions/{id}/odds`. These carry open, close and current moneyline and total for DraftKings (100) and Bet365 (2000).

**Historical (UCL 2021-22 to 2025-26 backtests):**
- ESPN core `odds` stays available after the match. Matttgic confirmed this for EPL, NBA, NHL and NFL in 2026. **How far back it goes for UCL is UNVERIFIED.**
- Use the **open** line. Close is after kickoff of Tuesday games and therefore after the UCL deadline.
- For domestic strength, use your existing football-data.co.uk pre-closing odds (E0, SP1, I1, D1, F1, N1, P1, T1, B1, G1, SC0) to rate each UCL club's attack and defence inside its league each week. Link leagues through UCL matches with a league offset.

**Join:** UEFA team id to a name map (Pinnacle and ESPN names), the same way as your `domestic.py` map.

**Expected gain:** HugoReel's EPL results:
- De-vigged market match log loss 0.9417 against Dixon-Coles 0.9824 (−4.3%); clean-sheet Brier better in every season.
- Switching compose to market team strength raised optimised-team points from 54.36 to **57.58 per GW**.

For UCL, where clubs play only 8–17 games a season against cross-league opponents (the reason your domestic Elo hurt), the market gap is likely larger (**UNVERIFIED**).

### Bonus recipe: UCL domestic form (Understat plus Core-Insights)

- Understat:
  - `GET https://understat.com/getLeagueData/{EPL|La_liga|Bundesliga|Serie_A|Ligue_1|RFPL}/{start_year}` with `X-Requested-With: XMLHttpRequest` returns players (xG, xA, npxG, shots, key passes, minutes), dates and teams.
  - `GET /getPlayerData/{understat_id}` returns per-match logs.
  - Join by name + club to UEFA player ids. For PL players use Dastan's `fpl_understat_*` maps plus Core-Insights.
- Coverage gap: Understat lacks Portugal, Netherlands, Turkey, Belgium, Czechia, Norway, Austria, Greece, Azerbaijan and others. For those clubs:
  - fall back to ESPN `summary`/gamelog box scores (goals, assists, shots, minutes);
  - or use the UEFA feed's own `minsPlyd` plus past-season UCL per-MD stats.
- alexg171/ucl-fantasy-predictor found that **domestic season S predicts UCL season S+1 better than same-season domestic form**, which it attributes to timing coincidence. That is OLS on Sofascore data; treat it as weak evidence. https://github.com/alexg171/ucl-fantasy-predictor

---

## 3. Modelling techniques from open-source projects, with reported gains

| Technique | Source | Reported effect |
|---|---|---|
| **Conditional target decomposition**: xPts = p60·Σ_k P(band_k \| 60+)·E[pts \| band_k] + (1−p60)·E[pts \| <60], plus a direct regressor blended per position. Bands split at 1, 3 and 10 points. Positions fitted separately. Band probabilities calibrated per fold and position | Dastan | Against OpenFPL on identical 2025-26 GW1–24 rows: **objective +0.0597** (CI +0.041 to +0.080); Spearman 0.754 vs 0.696; **MAE 0.948 vs 1.167**; RMSE 1.928 vs 2.017. Gain comes from 0-minute and low-return rows. Starters indistinguishable; OpenFPL slightly better on 3–4 point and 5+ point rows |
| **Each head early-stops on the population it models** (for example the "<60 min" head validates on <60 rows), plus other harness fixes | Dastan | Harness fixes worth **+0.0144**, more than all features added afterwards. One family flipped from −0.0084 to +0.0171 |
| **Deadline-anchor double gameweeks**: both fixtures of a DGW get the same pre-deadline history | Dastan | 6,958 DGW player-weeks; **40.6% carried leaky rolling values** before the fix |
| **Score at player-gameweek grain, metric within each GW then averaged; report "all" and "starters" cohorts** | Dastan (matches your harness) | Pooled Spearman ~0.73 vs per-GW figures are different quantities |
| **Seed-noise floor and multi-seed screening**: keep margin = σ_seed/√n_seeds = 0.0106/√3 = 0.0061; separate discovery and confirmation windows | Dastan | A 500-trial Optuna search claiming +0.0161 was worth **−0.0015** after correcting seed luck (+0.0090) and window luck. **Untuned hyperparameters shipped** |
| **Rejected features** (measured): kickoff time −0.0057; rest-day differential −0.0064; travel −0.0041; crowd regime −0.0018; declared set-piece order **+0.0003**; age/days at club +0.0021 | Dastan | Don't spend time here. Your ROADMAP #2 penalty overlay is likely low value |
| **Defensive-workload rolling family** (DefCon, CBIT, recoveries, tackles over 1/3/5/10/38 windows) | Dastan | **+0.0171, the strongest single family** |
| **OpenFPL feature set**: 23 player stats × windows {1,3,5,10,38} (FPL plus Understat xG, xA, xGChain, xGBuildup, key passes, shots); 12 team and 10 opponent stats (goals, xG, xGA, **deep, deep allowed, PPDA**, league rank); availability status | OpenFPL (code in `data/samples.csv`) | Reused as-is by Dastan. Against FPL Review: better on 3–4 point rows (RMSE 1.517 vs 1.594) and 5+ point rows (5.142 vs 5.172; "up to 5%" better on high-return categories); worse on 0-point rows (0.818 vs 0.689) and ≤2-point rows (1.291 vs 1.189) |
| **Large median ensemble**: K-best search over XGBoost and random forest, top-K per CV fold, **median** across 5 folds × K | OpenFPL | See above (accuracy close to FPL Review) |
| **Per-position isotonic calibration of xPts** to undo regression to the mean in MSE-trained models (monotone, so rankings unchanged; stretches the hauler tail) | aadumas-commits/openfpl-extensions | No number reported. Matters for captaincy and the triple-captain choice when comparing across positions |
| **Calibrated minutes heads**: P(start), P(60 \| start), P(sub) as LightGBM with **isotonic calibration on a chronological tail the booster never saw**. E[min \| start] by position (GKP 89.5, DEF 85.8, MID 80.5, FWD 79.9); E[min \| sub] (82.8, 39.4, 28.4, 24.6) | HugoReel/fpl | P(start) log loss −20.5% (2024-25) and −25.4% (2025-26) against a "started last match" baseline; P(sub) −32 to −39% |
| **Error attribution before building**: perfect minutes worth 0.280 MAE against 0.079 for perfect per-90 rates | HugoReel | Directs effort to minutes and availability |
| **Market owns team strength, model owns minutes**: Shin-devig 1X2+O/U, fit Poisson λ's, clean sheets from the market | HugoReel | Optimised XI **+3.22 points/GW** (54.36 to 57.58); market log loss −4.3% against Dixon-Coles |
| **Two-stage hurdle** P(60+)×E[pts \| 60+] against a direct regressor | percivalmahwaya | MAE 0.954 vs 1.024; top-20 picks 4.39 vs 4.29 points (FPL `xP` 3.29) on held-out 2025-26 |
| **Captain = argmax E[X]**, not a ceiling metric | percivalmahwaya | 2025-26 simulation: E[X] 13.63 points/GW vs p(≥10) 11.58 vs 90th percentile 9.21 vs most-owned 9.63 (+152 points/season over template). Doubling is linear, so E[2X] is maximised by E[X]; ceiling only matters for rank chasing |
| **Walk-forward with an optimised-XI metric and paired bootstrap** | lagnis337 | Learned models +5.0 XI points/GW over last-3 mean (CI +1.5 to +8.4). All models reach about **40% of oracle**; GBT, ridge and hurdle are statistically tied, so features, not algorithms, are the limit |
| **Robust transfer choice through noisy re-solves**: noise ∝ Pts·(92 − xMins)/134·N(0,1); count how often each move appears ("sensitivity") | open-fpl-solver `solver.py`, `sensitivity.py` | Qualitative; reduces over-reaction to point estimates |
| **Solver defaults worth copying** (community-tuned, not evidence): horizon 8, decay 0.9, FT value when rolling {2: 2.0, 3: 1.6, 4: 1.3, 5: 1.1}, bench weights {GK 0.03, 1st 0.21, 2nd 0.06, 3rd 0.002}, vice-captain weight 0.1, money-in-bank value 0.08/£0.1m | open-fpl-solver `comprehensive_settings.json` | Untested by me. Compare with your static bench weights (0.25/0.15/0.05) in ROADMAP §7.4 |
| **Source blending with a disagreement signal** (stats feed vs club news vs prior → value plus disagreement; flag ≥ 0.33) | lfrocharod/fantasy-dashboard (`docs/data-sources.md`) | No number. Useful for combining Recipe 1, Recipe 2 and the UEFA `pStatus` field |

UCL-specific techniques (my derivations from the rules; not measured by anyone):
- **Captain-switch option value.** If your Tuesday captain T scores c, switching to the best not-yet-played player W gains E[X_W] − c. Switch when c < E[X_W] (risk-neutral; add a margin for rank context). So **before** the deadline, compare E[max(X_T, μ_W)] (for a Tuesday captain) with μ_W (for a Wednesday captain). A Tuesday captain with slightly lower mean but higher variance is often better because the switch truncates the downside. This needs a predictive distribution, not just a mean: use Poisson components or quantile models (truong-tt/fpl-ml-manager uses quantile regression). Practitioner thresholds of 6 points or 8–10 points (FFS, UEFA tips) are heuristics.
- **Knockout value.** Player value over the remaining horizon = Σ_rounds P(club alive in round r) × E[points_r]. Get P(alive) from two-leg aggregate simulations using market λ's (Recipe 5).
- **Recoveries matter.** One point per 3 recoveries; centre-backs and holding midfielders on high-press sides dominate (UEFA and FFS analyses). Model recoveries per 90 from the UEFA per-MD history plus Core-Insights for PL players.

---

## 4. UCL 2026/27: calendar and Fantasy rules

**Calendar** (UEFA "teams, dates, draws, format, final" page and fixture list; Real Madrid site; Arsenal guide; Paramount+; MD1 results reports):

| Stage | Dates |
|---|---|
| MD1 | **Tue 8 – Thu 10 Sep 2026** (results: e.g. Real Madrid 2-1 Inter on 8 Sep, Napoli 0-1 Arsenal on 9 Sep, Bayern 5-0 Bodø/Glimt on 10 Sep) |
| **MD2** | **Tue 13 – Wed 14 Oct 2026** (confirmed: UEFA list; Real Madrid at Roma on Wed 14 Oct 21:00 CEST; FFS "MD2 projected team goals" article of 11 Oct). Tue games 18:45 and 21:00 CEST |
| MD3 | 20–21 Oct 2026 (Flashscore's "20–21 Nov" is a typo) |
| MD4 | 3–4 Nov 2026 |
| MD5 | 24–25 Nov 2026 |
| MD6 | 8–9 Dec 2026 |
| MD7 | 19–20 Jan 2027 |
| MD8 | **Wed 27 Jan 2027, all 18 games simultaneous** |
| KO play-offs | 16–17 and 23–24 Feb 2027 |
| Round of 16 | 9–10 and 16–17 Mar 2027 |
| Quarter-finals | 6–7 and 13–14 Apr 2027 |
| Semi-finals | 27–28 Apr and 4–5 May 2027 |
| Final | **Sat 5 Jun 2027, Estadio Metropolitano, Madrid** |

Fantasy therefore has **17 matchdays**: 8 league, 2 play-off, 2 R16, 2 QF, 2 SF, 1 final.

**Fantasy rules 2026/27** (UEFA rules page; FFS "How to play UCL Fantasy 2026/27"; TheFantasyTool; secondary sources agree):
- **Squad and budget:** 15 players (2 GK / 5 DEF / 5 MID / 3 FWD); **€100m, rising to €105m after the league phase**.
- **Club limit:** 3 in the league phase, then 4 (play-offs and R16), 5 (QF), 6 (SF), 8 (final).
- **Transfers:**
  - Unlimited before MD1.
  - League phase: **2 free transfers per MD, at most 1 carried over**. Each extra transfer costs **−4 from the overall total**.
  - Unlimited before MD9 (play-offs) and MD11 (R16).
  - Other KO allowances (for example 5 before QF/final and 3 before second legs) are per secondary sources.
  - **No rolling once the knockouts start.**
- **Chips:** Wildcard (permanent, budget kept) and **Limitless** (one MD, no budget or transfer limit, squad reverts), each once. **Not usable on MD1, MD9 or MD11.** One source wrongly calls the Wildcard single-MD; UEFA's page says it is permanent.
- **In-matchday actions:**
  - Once a day's games finish, up to 4 manual substitutions with players yet to play (sent-off players cannot be subbed).
  - **One captain switch** to a player yet to play; it is irreversible.
  - **No manual subs or captain change on MD8 or MD17** (simultaneous kickoffs).
- **Scoring:**

  | Event | Points |
  |---|---|
  | Appearance | +1, plus +1 for 60+ minutes (stoppage time excluded) |
  | Goal | GK/DEF 6, MID 5, FWD 4 |
  | Goal from outside the box | +1 |
  | Assist | 3 (UEFA page undated) |
  | Clean sheet | GK/DEF 4, MID 1 |
  | Every 2 goals conceded (GK/DEF) | −1 |
  | Every 3 saves | +1 |
  | Penalty saved | +5 |
  | Penalty won | +2 |
  | Penalty missed | −2 |
  | Penalty conceded | −1 |
  | **Every 3 ball recoveries** | +1 |
  | **Player of the Match** | **+3, single winner** (no 3-2-1 split) |
  | Yellow card | −1 |
  | Red card | −3 |
  | Own goal | −2 |

**Not found:** an official list of rule changes from 2025/26 to 2026/27. No source flagged any.

---

## 5. FPL 2026/27 rules and evidence on what wins

**Rules** (Premier League "What's happening with FPL chips in 2026/27"; PL "All you need to know about changes"; FFS "5 rule changes"; FFFix):
- **Two chip sets again**: Wildcard, Free Hit, Triple Captain and Bench Boost in each half (8 total). The first set must be used by the **GW19 deadline, Sat 2 Jan 2027 13:30 GMT**; nothing carries over. One chip per GW.
- The Assistant Manager chip is **gone** (BeatFPL; consistent with HugoReel's encoded rules). OpenFPL's "AM" models are for that chip.
- FTs still bank up to 5, and banked FTs survive WC/FH (HugoReel's rules note).
- **No extra free transfers in December** this season, unlike 2025/26's top-up. Make sure your solver does not assume one.
- **BPS rebalanced** to reduce overlap with DefCon:
  - tackled-player deduction removed;
  - defenders earn 1 BPS per **3** clearances/blocks/interceptions (was 2);
  - penalty save 7 BPS (was 8) plus a new big-chance-save +1.
- DefCon unchanged: DEF +2 at 10 CBIT; MID/FWD +2 at 12 CBIRT.
- **Live bonus after 20 minutes and live ranks.** Gameweek lockdown moves to 9am UK the day after the GW's last match.
- Next deadline (from fplcache): **GW7, 2026-10-17 10:00 UTC**.
- FPL login moved to Ping Identity OIDC (`users.premierleague.com` no longer resolves; kru3ish/fpl-auto-manager). This only affects authenticated endpoints. Public `entry/{id}/event/{gw}/picks/` is unaffected.

**Evidence on what wins** (measured where possible; the rest is labelled as heuristic):
- **Captaincy:** maximise E[points]. percivalmahwaya's 2025-26 simulation (above) showed ceiling-based captaincy (90th percentile, P(≥10)) **lost** to the mean. For rank, the field's expected score does not depend on your pick, so the mean still maximises expected rank-relevant gain. A differential captain is only justified when its mean is within about 1–1.5 points of the template and you need variance to climb (fploracle framework; heuristic, not a study). No peer-reviewed or large-sample differential study was found.
- **Hits:** no rigorous multi-season study found. Rules of thumb:
  - a hit buys a move about one week early, so it pays for lasting upgrades or for replacing non-starters (KnightManagers);
  - TransferLab uses a 2.0-point buffer (about 6 projected points per hit) and values a banked FT at 1.5;
  - open-fpl-solver values rolling FTs at {2.0, 1.6, 1.3, 1.1}.
  - Your own ROADMAP (§2.5) already finds ±60 points of backtest total is noise. Judge hit logic on targeted metrics.
- **Chips:**
  - Bench Boost in a double gameweek is the clearest measured edge. 2017/18 DGW34 top-10k: BB 94.9 vs no chip 74.7. 2025/26 GW33 (LiveFPL): BB users about +20 over Free Hit users.
  - FPL Copilot's 2026 solver study (323 squads): the most common optimal late-season sequence was Wildcard GW32 → Bench Boost GW33 (DGW) → Free Hit GW34 (blank) → Triple Captain GW36.
  - 2026/27 blanks and doubles are expected mainly in the second half (FF Hub / Ben Crellin calendar).
  - For the first set, which must be used by GW19, the solver is the right tool: open-fpl-solver supports `use_wc`, `use_bb`, `use_fh`, `use_tc`, `allowed_chip_gws` and `chip_limits`.
- **Effective ownership (EO):** EO = % starting + % captaining + % triple-captaining (FFS). Top-10k EO differs sharply from overall EO (e.g. Palmer 61% overall vs 97% top-10k in one GW, FFFix). Top-10k EO can be estimated from public data: the overall league `leagues-classic/314/standings/?page_standings=N` (50 per page) plus `entry/{id}/event/{gw}/picks/`. A 1,000-manager sample is about 1,020 requests per GW. Your ROADMAP §7.6 deprioritises this; the evidence supports that for points.

---

## 6. Things that contradict the brief's assumptions or need care

1. **"Historical status/chance/`ep_next` leak, so wait for our archive"**: false. Pre-deadline snapshots exist for 2021-04 onward (fplcache) and are already extracted and provenance-checked (Dastan). See Recipe 1.
2. **"Spearman 0.22–0.27 is weak"**: on a plausible-starters cohort it is at or above the public frontier for starters (Dastan 0.29 walk-forward 2024-25; Dastan/OpenFPL about 0.13 on 2025-26 GW1–24). Your cohort also includes predicted starters who end up benched, which is easier to rank. Expect improvements mainly from minutes and availability.
3. **ClubElo "free CSV API"**: degraded for 2026-27. `/Fixtures` deactivated and per-club 502 (your own 2026-09-08 note); FPL-Core-Insights Elo blank since GW1 2026-27; knapsack copy stale since 2026-08-31. Historical pre-match Elo for PL matches survives in Core-Insights 2024-25 and 2025-26 `matches.csv`. For UCL, prefer market odds; EloFootball is the cross-league Elo fallback.
4. **FPL-Elo-Insights has been renamed FPL-Core-Insights**:
   - its per-GW files are rewritten after the fact;
   - 2024-25 GW1–19 are back-filled;
   - `chance_of_playing_next_round` NaN is stored as 0.0 for available players;
   - the Elo columns are empty for 2026-27.
5. **vaastav is not updated in-season for 2026-27** (GW1 only, last commit 2026-08-28). In earlier seasons it was updated irregularly (e.g. 2026-01-02, 02-05, 03-13, 06-17). `xP` is confirmed contaminated by vaastav's README (#222), lagnis337 and lfrocharod: it is 0 for 27 of 38 GWs in 2025-26 and runs about 1.3 points high in matches where the player scored. `players_raw.csv` is end-of-season.
6. **FBref** lost its advanced stats on 20 Jan 2026 (Opta terminated the feed) and blocks datacenter IPs. **FiveThirtyEight SPI** is defunct. **Sofascore and FotMob** are not practical from cloud runners.
7. **football-data.co.uk:**
   - ToS: "private individuals only, NOT … automated bots/scrapers/AI". This is a policy risk for the automated pull in Actions.
   - The 2026-27 files have **no Pinnacle columns**.
   - Pre-closing `Avg*` odds are collected "generally not later than" Fri 17:00 or Tue 13:00 UK. That is safely before most deadlines, but for a Friday-lunchtime or rescheduled GW the odds can post-date the deadline. Add a guard comparing `deadline` with the collection bound, as nsoifer01/shevato does.
   - Your `odds.py` already avoids the closing `…C…` columns. Good.
8. **UEFA gaming feeds may block datacenter IPs**: reported by lfrocharod/fantasy-dashboard ADR-0002 (Oct 2026), **UNVERIFIED** for your case. Your `deploy.yml` fetches them in Actions with `continue-on-error`, so check whether recent runs actually refreshed.
9. **Free player props:** there is no free bookmaker *archive*. Pinnacle has no soccer scorer props. Betfair and Oddschecker are not viable from cloud runners. The only free history is Kalshi (EPL since Dec 2025) and patchy Polymarket. Everything else must be **self-archived from now** (Kambi, Pinnacle team totals, ESPN odds).
10. **The GitHub API is not usable from the dev sandbox** for third-party repos (`api.github.com` refused repos outside the session; `github.com` HTML returns 403). Use `raw.githubusercontent.com` and `git clone --filter=blob:none` (both work), including `git log`/`git show` for point-in-time reconstruction.
11. **OpenFPL** is a 2025 snapshot (last push 2025-08-15). Its positions include the now-removed Assistant Manager chip. It trails Dastan on 0-minute and ≤2-point rows because it uses availability tags rather than modelled minutes.
12. **UCL MD1** ran over three days (8–10 Sep), not the usual two. **MD2 is 13–14 Oct**, so its deadline is today.

---

## 7. Suggested order of work

1. **Recipe 1** (L–M effort, measured gain). Retrain the minutes and points models with pre-deadline status, chance and news, plus `ep_next`, on 2021-22 to 2025-26. Use ≥ 3 seeds and your two-split validation.
2. **Recipe 4** all-competition minutes and rolling DefCon/recoveries features (M effort). Dastan's strongest measured family and #1 open lead.
3. **Recipe 2** lineup archive in the minutes models for FPL and UCL (M effort). Validate on 2025-26 GW6+ and 2026-27.
4. **Recipe 5** market team strength for UCL (M effort). Replaces the Elo your notes say hurt. Start archiving Pinnacle and ESPN odds immediately.
5. **Recipe 3** scorer markets: Kalshi backtest on 2025-26 GW15+ and Kambi live archiving (M effort, potentially high for FWD/MID).
6. Decision layer: per-position isotonic calibration; E[X] captaincy plus the UCL switch option value; solver FT-value and bench-weight defaults.

---

## Sources

- OpenFPL: https://github.com/daniegr/OpenFPL · https://arxiv.org/abs/2508.09992 · https://www.researchgate.net/publication/394488516
- openfpl-extensions: https://github.com/aadumas-commits/openfpl-extensions
- Dastan (SmartPlayFPL): https://github.com/qazybekb/smartplayfpl-dastan (`docs/METHODOLOGY.md`, `docs/FEATURES.md`, `docs/DATA.md`)
- HugoReel/fpl: https://github.com/HugoReel/fpl (`experiments/reports/*.md`)
- percivalmahwaya/fpl-decision-engine: https://github.com/percivalmahwaya/fpl-decision-engine
- lagnis337/fpl-player-models: https://github.com/lagnis337/fpl-player-models
- lfrocharod/fantasy-dashboard: https://github.com/lfrocharod/fantasy-dashboard (`docs/data-sources.md`, `docs/adr/0002-…`)
- open-fpl-solver: https://github.com/solioanalytics/open-fpl-solver
- AIrsenal: https://github.com/alan-turing-institute/AIrsenal
- fplcache: https://github.com/Randdalf/fplcache
- FPL-Core-Insights: https://github.com/olbauday/FPL-Core-Insights
- vaastav: https://github.com/vaastav/Fantasy-Premier-League (README xP caveat) · https://github.com/ADnocap/FPL-RL/blob/main/XP_LOOKAHEAD_ANALYSIS.md
- knapsack-football-formations: https://github.com/pabloroldan98/knapsack-football-formations
- Free odds/props survey (tested from a datacenter IP, 2026-10-02): https://github.com/Matttgic/Overstreamlit/blob/main/docs/14_sources_props_et_cotes_live.md and `scripts/outils_recherche/live_helpers.py`
- Kalshi EPL scorer market example: https://kalshi.com/markets/kxeplgoal/epl-goalscorer/kxeplgoal-26oct10muntot · Kalshi API guide: https://www.predictionhunt.com/blog/kalshi-api-getting-started-guide
- Polymarket EPL props (mirror): https://polyguana.com/events/epl-ful-bou-2026-05-09-player-props
- Pinnacle guest API usage: https://github.com/pretrehr/Sports-betting (`sportsbetting/bookmakers/pinnacle.py`)
- ESPN API docs: https://github.com/pseudo-r/Public-ESPN-API/blob/main/docs/sports/soccer.md
- RotoWire scrapers: https://github.com/lewis-king/fpl-mcp-server (`src/fpl_server/rotowire_scraper.py`)
- football-data notes and ToS analysis: https://github.com/nsoifer01/shevato/blob/master/apps/fpl-planner/scripts/lib/odds-football-data.mjs · notes.txt mirror https://github.com/withqwerty/football-docs/blob/main/specs/football-data/notes.txt · https://www.football-data.co.uk/fixtures.csv
- ClubElo endpoints: http://clubelo.com/API · https://github.com/oseymour/ScraperFC/blob/main/src/ScraperFC/clubelo.py
- Opta Power Rankings JS: https://github.com/sahil-gidwani/football-data-webscraping/blob/main/opta/power_rankings.py
- FiveThirtyEight SPI README: https://github.com/fivethirtyeight/data/tree/master/soccer-spi
- FBref advanced-data removal: https://www.sports-reference.com/blog/ · https://awfulannouncing.com/soccer/sports-reference-pulls-advanced-data-agreement-violation-dispute.html
- FPL 2026/27:
  - https://www.premierleague.com/en/news/4679879/whats-happening-with-fpl-chips-in-202627
  - https://www.premierleague.com/en/news/4679873/all-you-need-to-know-about-changes-to-fpl-for-202627
  - https://www.fantasyfootballscout.co.uk/2026/07/20/fpl-2026-27-5-rule-changes-new-features-announced
  - https://www.fantasyfootballfix.com/blog-index/fpl-2026-27-new-rules/
  - https://www.beatfpl.com/guides/fpl-26-27-rules-changes
  - https://github.com/kru3ish/fpl-auto-manager
- UCL 2026/27 calendar:
  - https://www.uefa.com/uefachampionsleague/news/02a6-20d57cfcd03e-407c22a7f465-1000--2026-27-champions-league-teams-dates-draws-format-final/
  - https://www.uefa.com/uefachampionsleague/news/02a8-2174c9e9019d-f909a77bd77a-1000--2026-27-champions-league-all-the-league-phase-fixtures-a/
  - https://editorial.uefa.com/resources/02a8-21768bda71f7-297d2c258d74-1000/uefa_-_champions_league_-_2026-27_-_league_phase_draw_results_-_calendar_by_matchday.pdf
  - https://www.realmadrid.com/en-US/news/football/first-team/latest-news/calendarios-del-real-madrid-en-la-primera-fase-de-la-champions-2026-27-29-08-2026
  - https://www.arsenal.com/feature/champions-league-2627-format-match-dates-and-more-anWrT4C7i8DT
  - https://www.paramountplus.com/sneak-peak/uefa-champions-league-schedule/
  - https://foot-africa.com/en/news/champions-league-2026-27-all-matchday-1-results-1348181/
  - https://en.wikipedia.org/wiki/2027_UEFA_Champions_League_final
- UCL Fantasy 2026/27 rules:
  - https://www.uefa.com/uefachampionsleague/news/025f-0fd4b42cc0a7-74498b7df63b-1000--uefa-champions-league-fantasy-football-rules-2026-27/
  - https://www.fantasyfootballscout.co.uk/2026/08/31/how-to-play-uefa-champions-league-fantasy-2026-27-rules-scoring-chips
  - https://www.uefa.com/uefachampionsleague/news/02a8-21728a3e14ea-b257b84fec55-1000--champions-league-fantasy-football-when-to-play-the-wildcard/
  - https://thefantasytool.com/guides/ucl-fantasy-rules-and-scoring
  - https://www.uefa.com/uefachampionsleague/news/02a8-2170f6d65d14-238ae7d6bb48-1000--champions-league-fantasy-football-how-to-score-points/
  - https://www.fantasyfootballscout.co.uk/2026/10/11/ucl-fantasy-2026-27-matchday-2-projected-team-goals
- UCL strategy:
  - https://www.fantasyfootballscout.co.uk/2024/10/21/ucl-fantasy-who-are-the-best-players-for-ball-recoveries
  - https://www.fantasyfootballscout.co.uk/2024/09/18/uefa-champions-league-fantasy-a-guide-to-subs-captain-changes
  - https://www.uefa.com/uefachampionsleague/news/0255-0e99ede714ec-6bed535d673d-1000--five-tips-for-success-in-uclfantasy
- FPL strategy:
  - https://www.fantasyfootballscout.co.uk/2021/03/24/what-is-effective-ownership-and-why-is-it-so-widely-talked-about-in-fpl
  - https://fploracle.team/blog/fpl-captaincy-strategy
  - https://www.fantasyfootballfix.com/blog-index/gameweek-13-deadline-report-salah-58-palmer-96-eo/
  - https://www.transferlab.net/
  - https://knightmanagers.com/theplaybook/fpl-free-transfers/
  - https://www.fantasyfootballscout.co.uk/2026/04/24/fpl-double-gameweek-33-chip-usage-template-squads
  - https://www.fantasyfootballscout.co.uk/2018/04/20/metrics-the-bench-boost-and-free-hit/
  - https://fplcopilot.com/blog/dgw-chip-guide
  - https://www.fantasyfootballhub.co.uk/articles/fpl-blank-double-gameweek-guide
- UCL domestic-form model: https://github.com/alexg171/ucl-fantasy-predictor
