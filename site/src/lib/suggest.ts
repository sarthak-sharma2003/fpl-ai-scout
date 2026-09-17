import type { PlayerCard, PlayerProjection, Position } from '../types';

/** A squad member is just a PlayerCard — code/name/team/position/price/ev,
 * the same shape the pitch already renders (PlayerChip.tsx). */
export type SquadPlayer = PlayerCard;

export interface XIResult {
  xi: SquadPlayer[];
  bench: SquadPlayer[];
  /** "4-4-2", or '—' when the squad can't field a legal XI (e.g. a partial
   * manual squad missing a position). */
  formation: string;
  captain: SquadPlayer | null;
  vice: SquadPlayer | null;
}

const ev = (p: SquadPlayer) => p.ev ?? 0;

function byPos(squad: SquadPlayer[], pos: Position): SquadPlayer[] {
  return squad.filter((p) => p.position === pos).sort((a, b) => ev(b) - ev(a));
}

const sumEv = (players: SquadPlayer[]) => players.reduce((s, p) => s + ev(p), 0);

/** Picks the highest-EV legal formation (1 GK, 3-5 DEF, 2-5 MID, 1-3 FWD, 11
 * total) out of a squad. Captain/vice are the top-2 EV players in that XI;
 * bench is the rest, outfielders by EV desc then the backup GK last (FPL's
 * bench-GK-last convention).
 *
 * ponytail: brute-forces every (def, mid, fwd) shape instead of a real
 * knapsack — a squad has at most 5 outfielders per position, so this is ~12
 * combinations, not worth a smarter algorithm. */
export function bestXI(squad: SquadPlayer[]): XIResult {
  const gk = byPos(squad, 'GKP');
  const def = byPos(squad, 'DEF');
  const mid = byPos(squad, 'MID');
  const fwd = byPos(squad, 'FWD');

  let best: { def: number; mid: number; fwd: number; value: number } | null = null;
  for (let d = 3; d <= Math.min(5, def.length); d++) {
    for (let m = 2; m <= Math.min(5, mid.length); m++) {
      const f = 10 - d - m;
      if (f < 1 || f > 3 || f > fwd.length) continue;
      const value = sumEv(def.slice(0, d)) + sumEv(mid.slice(0, m)) + sumEv(fwd.slice(0, f));
      if (!best || value > best.value) best = { def: d, mid: m, fwd: f, value };
    }
  }

  if (!best || gk.length === 0) {
    return {
      xi: [],
      bench: [...squad].sort((a, b) => ev(b) - ev(a)),
      formation: '—',
      captain: null,
      vice: null,
    };
  }

  const xi = [gk[0], ...def.slice(0, best.def), ...mid.slice(0, best.mid), ...fwd.slice(0, best.fwd)];
  const benchOutfield = [...def.slice(best.def), ...mid.slice(best.mid), ...fwd.slice(best.fwd)].sort(
    (a, b) => ev(b) - ev(a),
  );
  const bench = [...benchOutfield, ...gk.slice(1)];

  const [captain = null, vice = null] = [...xi].sort((a, b) => ev(b) - ev(a));

  return { xi, bench, formation: `${best.def}-${best.mid}-${best.fwd}`, captain, vice };
}

export interface TransferSuggestion {
  out: SquadPlayer;
  in: SquadPlayer;
  gain: number;
}

function toSquadPlayer(p: PlayerProjection): SquadPlayer {
  return {
    code: p.code, name: p.name, team: p.team, position: p.position,
    price: p.price, ev: p.ev_points, flag: p.flag, pk: p.pk,
  };
}

/** Ranks single-player swaps by EV gain: for each owned player, the best
 * replacement is the highest-EV affordable same-position player not already
 * owned that keeps every club at ≤3 players. Returns the top `limit` swaps
 * with gain > 0, best first.
 *
 * Selling price is approximated as the player's current listed price — real
 * FPL selling price can be lower after a price drop, but this app has no
 * purchase-history to compute that from (same simplification the rest of the
 * site makes for a squad it didn't track from day one).
 *
 * O(squad × players): squads are 15 and the player pool is ~600, so this is
 * a few thousand comparisons — no index needed. */
export function transferSuggestions(
  squad: SquadPlayer[],
  bank: number,
  allPlayers: PlayerProjection[],
  limit = 8,
): TransferSuggestion[] {
  const ownedCodes = new Set(squad.map((p) => p.code));
  const clubCounts = new Map<string, number>();
  for (const p of squad) {
    if (p.team) clubCounts.set(p.team, (clubCounts.get(p.team) ?? 0) + 1);
  }

  const suggestions: TransferSuggestion[] = [];
  for (const out of squad) {
    const budget = (out.price ?? 0) + bank;
    const clubCountWithoutOut = (out.team ? (clubCounts.get(out.team) ?? 0) : 0) - 1;

    let bestIn: PlayerProjection | null = null;
    for (const candidate of allPlayers) {
      if (candidate.position !== out.position) continue;
      if (ownedCodes.has(candidate.code)) continue;
      if (candidate.price > budget + 1e-9) continue;
      const clubAfter =
        candidate.team === out.team
          ? clubCountWithoutOut + 1
          : (candidate.team ? (clubCounts.get(candidate.team) ?? 0) : 0) + 1;
      if (clubAfter > 3) continue;
      if (!bestIn || candidate.ev_points > bestIn.ev_points) bestIn = candidate;
    }

    if (bestIn && bestIn.ev_points > ev(out)) {
      suggestions.push({ out, in: toSquadPlayer(bestIn), gain: bestIn.ev_points - ev(out) });
    }
  }

  return suggestions.sort((a, b) => b.gain - a.gain).slice(0, limit);
}
