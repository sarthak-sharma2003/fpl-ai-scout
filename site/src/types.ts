// Mirrors the shapes produced by publish.py exactly — so a future dynamic
// backend swap-in changes only the fetch transport, not these types.

export type Position = 'GKP' | 'DEF' | 'MID' | 'FWD';

/** Availability warning attached to a player when the API flags them. */
export interface AvailabilityFlag {
  status: string;
  news: string | null;
  chance: number | null;
}

export interface PlayerCard {
  code: number;
  name: string;
  team: string | null;
  position: Position;
  price: number | null;
  ev: number | null;
  flag?: AvailabilityFlag;
  pk?: boolean;
}

export interface Dashboard {
  gw: number;
  season: string;
  is_live: boolean;
  /** 'live' | 'demo' */
  state: string;
  deadline: string | null;
  avg_points: number | null;
  our_points: number | null;
  overall_rank: number | null;
  mini_league: { pos: number; size: number } | null;
  insight: {
    text: string;
    transfer_summary: string | null;
    captain: string | null;
  };
  captain_code: number | null;
  vice_captain_code: number | null;
  bench_order: PlayerCard[];
  pitch: {
    gk: PlayerCard[];
    def: PlayerCard[];
    mid: PlayerCard[];
    fwd: PlayerCard[];
  };
}

export interface TransferMove {
  out: PlayerCard;
  in: PlayerCard;
  compare: { position: string; out_ev: number | null; in_ev: number | null };
  net_ev: number;
}

export interface Transfers {
  gw: number;
  confidence: number;
  bank: number | null;
  free_transfers: number | null;
  chip_advice: { chip: string; gw: number; ev: number | null } | null;
  moves: TransferMove[];
  alternatives: TransferMove[];
}

export interface FixtureTick {
  gw: number;
  opponent?: string;
  was_home?: boolean;
  fdr?: number | null;
  is_dgw?: boolean;
  is_bgw?: boolean;
}

export interface FixturesResponse {
  reference_gw: number;
  horizon: number;
  is_live: boolean;
  state?: string;
  note: string | null;
  teams: {
    code: number;
    name: string;
    short_name: string;
    ticker: FixtureTick[];
  }[];
}

export interface SignalCard {
  code: number;
  name: string;
  price: number | null;
  transfers_balance: number;
  selected_by: number | null;
}

export interface Signals {
  gw: number;
  is_live: boolean;
  price_risers: SignalCard[];
  price_fallers: SignalCard[];
  injury_news: unknown[];
  injury_news_note: string | null;
}

export interface Rule {
  id: string;
  title: string;
  body: string;
  enabled: boolean;
  /** absent = enforced by the pipeline; 'strategy' = playbook, not code */
  kind?: string;
  source?: string;
}

export interface SplitSummary {
  split_label: string;
  version: string;
  train_seasons: string[];
  holdout_season: string;
  beats_naive: boolean;
  model_mean_per_gw_spearman: number;
  naive_mean_per_gw_spearman: number;
  model_rmse: number;
  naive_rmse: number;
}

export interface ChipUse {
  chip: string;
  gw: number;
}

export interface BacktestSeason {
  season: string;
  train_seasons: string[];
  total_points: number;
  total_hits: number;
  chips_used: ChipUse[];
  gw_scores: { gw: number; score: number; hits: number }[];
}

export interface Analytics {
  model_version: string | null;
  validation: {
    beats_naive_decision: boolean;
    primary: SplitSummary;
    secondary: SplitSummary;
  } | null;
  backtest: {
    seasons: BacktestSeason[];
    plan_stated_target: number;
    real_2025_26_average_manager: number;
  } | null;
}

// ——— projections.json ———

export interface PlayerProjection {
  code: number;
  name: string;
  position: Position;
  team: string | null;
  price: number;
  ev_points: number;
  q10_points: number | null;
  q90_points: number | null;
  ev_minutes: number | null;
  p_appearance: number | null;
  p_60_plus: number | null;
  p_clean_sheet: number | null;
  model_version: string;
  flag?: AvailabilityFlag;
  pk?: boolean;
}

export interface Projections {
  season: string;
  gw: number;
  players: PlayerProjection[];
}

// ——— chips.json ———

/** bboost weeks carry bench_ev; 3xc weeks carry name/extra_ev/q90. */
export interface ChipThisWeek {
  bench_ev?: number;
  name?: string;
  extra_ev?: number;
  q90?: number;
}

export interface ChipInfo {
  chip: string;
  chip_id: number;
  start_gw: number;
  stop_gw: number;
  available: boolean;
  used_gw: number | null;
  active_now: boolean;
  this_week: ChipThisWeek | null;
  guidance: string;
}

export interface RadarEntry {
  gw: number;
  dgw_teams: string[];
  bgw_teams: string[];
}

export interface ChipsResponse {
  season: string;
  reference_gw: number;
  configured: boolean;
  note: string | null;
  chips: ChipInfo[];
  dgw_bgw_radar: RadarEntry[];
  radar_note: string | null;
}

// ——— league.json ———

export interface SquadPlayer extends PlayerCard {
  /** 0 = benched, 1 = starts, 2 = captain (3 with triple captain) */
  multiplier: number;
  is_captain: boolean;
}

export interface LeagueEntry {
  entry_id: number;
  entry_name: string;
  player_name: string;
  rank: number;
  last_rank: number;
  total: number;
  event_total: number;
  is_us: boolean;
  chips_used: ChipUse[];
  bank: number | null;
  team_value: number | null;
  projected_next_ev: number | null;
  captain: SquadPlayer | null;
  squad: SquadPlayer[];
}

export interface OwnershipRow {
  code: number;
  name: string;
  team: string | null;
  position: Position;
  price: number | null;
  ev: number | null;
  owned_by: string[];
  n_owned: number;
  n_captained: number;
  we_own: boolean;
}

export interface LeagueResponse {
  configured: boolean;
  note?: string | null;
  league?: { id: number; name: string; fetched_at: string };
  our_entry_id?: number;
  our_squad_source?: string;
  picks_gw?: number;
  projection_gw?: number;
  standings?: LeagueEntry[];
  ownership?: OwnershipRow[];
  differentials?: {
    our_edges: (PlayerCard & { n_owned: number })[];
    threats: OwnershipRow[];
  };
}
