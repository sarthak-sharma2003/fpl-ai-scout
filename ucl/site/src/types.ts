// Mirrors the data contract emitted by src/uclscout (see site/public/data/*.json).
// The frontend only reads this shape — it never re-derives projections.

export type Position = "GK" | "DEF" | "MID" | "FWD";

export interface Meta {
  schema: number;
  generatedAt: string;
  season: string;
  tour: number;
  matchday: number;
  lastMatchday: number;
  deadline: string;
  budget: number;
  maxPerClub: number;
  gamedays: string[];
  subsAllowed: number;
  checklist: string[];
  /** Set while an earlier matchday is under way: transfers are locked, but
   * players who have not kicked off yet can still be subbed / armbanded until
   * `deadline` (the next gameday's first kick-off). */
  subs?: { matchday: number; deadline: string; gameday: string; manualSubs: number } | null;
}

export interface Player {
  id: number;
  name: string;
  pos: Position;
  team: string;
  teamId: number;
  value: number;
  points: number;
  minutes: number;
  minutesSource: string;
  minutesTrusted: boolean;
  minutesNote: string;
  canCaptain: boolean;
  opponentAdj: number;
  owned: number;
  recoveriesPer90: number | null;
  status: string;
  day: string;
  priorPoints: number;
}

export interface SquadPick extends Player {
  starting: boolean;
  captain: boolean;
}

/** One chip's verdict for this run; "hold" is an answer too. */
export interface ChipCheck {
  advice: "play" | "planned" | "hold" | "used";
  matchday?: number | null;
  /** projected points gained at `matchday` (vs free transfers / your XI) */
  gain?: number | null;
  /** what playing it must beat */
  bar: number;
  reason?: string;
}

export interface SquadData extends Meta {
  /** every chip, checked on every run (pipeline.build_plan) */
  chips?: Record<string, ChipCheck>;
  squad: {
    cost: number;
    xiPoints: number;
    totalPoints: number;
    captainId: number;
    byDay: Record<string, number>;
    picks: SquadPick[];
    /** Present when the squad is YOUR held squad (config/squad.txt), not a
     * from-scratch build: the moves to make before this deadline. */
    held?: boolean;
    budget?: number;
    bankAfter?: number;
    trackedSince?: string;
    moves?: {
      buys: Player[];
      sells: Player[];
      hits: number;
      chip: string | null;
      freeAvailable: number;
      summary: string;
    };
  } | null;
}

export interface PlanAction {
  matchday: number;
  /** league | playoff | r16 | qf | sf | final */
  round?: string;
  /** projected before the knockout draw: P(club still in) x a generic opponent */
  generic?: boolean;
  /** a free rebuild (MD9, MD11): every transfer is free */
  unlimited?: boolean;
  maxPerClub?: number;
  chip: string | null;
  banking: boolean;
  freeAvailable: number;
  hits: number;
  projected: number;
  captain: Player | null;
  buys: Player[];
  sells: Player[];
  summary: string;
}

export interface PlanData extends Meta {
  totalProjected: number;
  limitless: { matchday: number; gain: number } | null;
  chips?: Record<string, ChipCheck>;
  actions: PlanAction[];
}

export interface PlayersData extends Meta {
  players: Player[];
}

export interface Team {
  id: number;
  short: string;
  name: string;
  pot: number;
  eliminated: boolean;
  elo: number;
}

export interface TeamsData extends Meta {
  teams: Team[];
}

export interface FixtureMatch {
  home: number;
  away: number;
  kickoff: string;
  lineupAnnounced: boolean;
}

export interface FixtureMatchday {
  matchday: number;
  deadline: string;
  subsAllowed: number;
  gamedays: string[];
  matches: FixtureMatch[];
}

export interface FixturesData extends Meta {
  matchdays: FixtureMatchday[];
}

export interface AllData {
  meta: Meta;
  squad: SquadData;
  plan: PlanData;
  players: PlayersData;
  teams: TeamsData;
  fixtures: FixturesData;
}
