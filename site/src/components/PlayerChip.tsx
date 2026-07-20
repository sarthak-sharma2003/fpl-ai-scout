import type { PlayerCard } from '../types';
import { FlagMark, PkMark, Roundel } from './ui';

const POS_STRIPE: Record<string, string> = {
  GKP: 'bg-gkp',
  DEF: 'bg-def',
  MID: 'bg-mid',
  FWD: 'bg-fwd',
};

/** A player card on the pitch: position stripe (the shirt), name, club·price,
 * EV in the model's voice, armband roundel and flag/PK marks when present.
 * Width comes from the wrapper so pitch rows and the bench can size it. */
export default function PitchCard({
  player,
  badge,
}: {
  player: PlayerCard;
  badge?: 'C' | 'V';
}) {
  return (
    <div className="relative w-full">
      {badge && (
        <div className="absolute -right-1.5 -top-1.5 z-10">
          <Roundel kind={badge} />
        </div>
      )}
      <div className="flex flex-col items-center overflow-hidden rounded-md bg-pitch-950/85 ring-1 ring-white/10">
        <div className={`h-[3px] w-full ${POS_STRIPE[player.position] ?? 'bg-ink-500'}`} />
        <div className="w-full px-1 pb-1.5 pt-1 text-center">
          <p className="truncate text-[11px] font-semibold leading-tight text-ink-100 md:text-xs">
            {player.name}
          </p>
          <p className="font-mono text-[8px] uppercase tracking-wide text-ink-500 md:text-[9px]">
            {player.team ?? '—'} · {player.price != null ? player.price.toFixed(1) : '—'}
          </p>
          <div className="mt-0.5 flex items-center justify-center gap-1">
            <span className="font-display text-[17px] font-semibold leading-none text-volt">
              {player.ev != null ? player.ev.toFixed(1) : '—'}
            </span>
            {player.flag && <FlagMark flag={player.flag} />}
            {player.pk && <PkMark />}
          </div>
        </div>
      </div>
    </div>
  );
}
