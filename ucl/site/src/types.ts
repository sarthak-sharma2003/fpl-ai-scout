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

export interface SquadData extends Meta {
  squad: {
    cost: number;
    xiPoints: number;
    totalPoints: number;
    captainId: number;
    byDay: Record<string, number>;
    picks: SquadPick[];
  };
}

export interface PlanAction {
  matchday: number;
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
