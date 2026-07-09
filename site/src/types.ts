// Mirrors the plan's §8 API contract shapes exactly, as produced by
// publish.py — so a future dynamic backend swap-in changes only the fetch
// transport, not these types.

export interface PlayerCard {
  code: number;
  name: string;
  team: string | null;
  position: 'GKP' | 'DEF' | 'MID' | 'FWD';
  price: number | null;
  ev: number | null;
}

export interface Dashboard {
  gw: number;
  season: string;
  is_live: boolean;
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
  bench_order: { name: string; team: string | null; ev: number }[];
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

export interface BacktestSeason {
  season: string;
  train_seasons: string[];
  total_points: number;
  total_hits: number;
  chips_used: { gw: number; chip: string }[];
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

export interface PlayerProjection {
  code: number;
  name: string;
  position: string;
  team: string | null;
  ev_points: number;
  q10_points: number | null;
  q90_points: number | null;
  ev_minutes: number | null;
  p_appearance: number | null;
  p_60_plus: number | null;
  p_clean_sheet: number | null;
  model_version: string;
}
