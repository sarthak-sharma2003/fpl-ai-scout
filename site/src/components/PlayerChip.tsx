import type { PlayerCard } from '../types';

const POSITION_COLOR: Record<string, string> = {
  GKP: 'text-amber-300',
  DEF: 'text-sky-300',
  MID: 'text-emerald-300',
  FWD: 'text-rose-300',
};

export default function PlayerChip({ player, captain = false }: { player: PlayerCard; captain?: boolean }) {
  return (
    <div className="flex flex-col items-center gap-1 rounded-xl bg-[var(--pitch-900)]/80 ring-1 ring-[var(--pitch-line)] px-2.5 py-2 min-w-[84px]">
      <div className="flex items-center gap-1">
        <span className={`text-[10px] font-bold uppercase ${POSITION_COLOR[player.position] ?? 'text-[var(--ink-300)]'}`}>
          {player.position}
        </span>
        {captain && (
          <span className="grid h-3.5 w-3.5 place-items-center rounded-full bg-amber-400 text-[8px] font-bold text-[var(--pitch-950)]">
            C
          </span>
        )}
      </div>
      <span className="text-xs font-semibold text-[var(--ink-100)] text-center leading-tight">
        {player.name}
      </span>
      <span className="text-[10px] text-[var(--ink-500)]">{player.team ?? '—'}</span>
      <span className="text-xs font-semibold text-emerald-300 tabular-nums">
        {player.ev != null ? player.ev.toFixed(1) : '—'}
      </span>
    </div>
  );
}
