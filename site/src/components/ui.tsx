import type { ReactNode } from 'react';
import type { Loadable } from '../lib/useJson';
import type { AvailabilityFlag, Position } from '../types';

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-lg border border-line bg-pitch-850/70 shadow-[0_1px_0_0_rgba(255,255,255,0.03)_inset] ${className}`}
    >
      {children}
    </div>
  );
}

/** Section label: mono, tracked-out, with the volt tick. */
export function Eyebrow({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <h2 className="flex items-center gap-2 font-mono text-[11px] font-bold uppercase tracking-[0.22em] text-ink-300">
        <span aria-hidden className="h-2 w-2 shrink-0 bg-volt" />
        {children}
      </h2>
      {action}
    </div>
  );
}

/** Data-state badge: 'live' or 'demo'. */
export function StateBadge({ state }: { state: string }) {
  if (state === 'live') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-sm bg-volt/10 px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-volt ring-1 ring-volt/40">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-volt" />
        Live
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-sm bg-white/5 px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-ink-500 ring-1 ring-line">
      <span className="h-1.5 w-1.5 rounded-full bg-ink-500" />
      Demo data
    </span>
  );
}

const POS_CHIP: Record<Position, string> = {
  GKP: 'text-gkp bg-gkp/12 ring-gkp/30',
  DEF: 'text-def bg-def/12 ring-def/30',
  MID: 'text-mid bg-mid/12 ring-mid/30',
  FWD: 'text-fwd bg-fwd/12 ring-fwd/30',
};

export function PosBadge({ pos }: { pos: Position }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-[3px] px-1 py-px font-mono text-[9px] font-bold tracking-[0.08em] ring-1 ${POS_CHIP[pos]}`}
    >
      {pos}
    </span>
  );
}

/** Availability warning: amber triangle + chance%, news in the tooltip. */
export function FlagMark({ flag }: { flag: AvailabilityFlag }) {
  return (
    <span
      title={flag.news ?? `Status: ${flag.status}`}
      className="inline-flex items-center gap-0.5 text-armband"
    >
      <svg viewBox="0 0 24 24" className="h-3 w-3" aria-label="Availability flag">
        <path fill="currentColor" d="M12 3.2 22.8 21H1.2L12 3.2Z" />
        <path d="M12 9.6v5" stroke="var(--color-pitch-950)" strokeWidth="2" strokeLinecap="round" />
        <circle cx="12" cy="17.6" r="1.15" fill="var(--color-pitch-950)" />
      </svg>
      {flag.chance != null && (
        <span className="font-mono text-[9px] font-bold">{flag.chance}%</span>
      )}
    </span>
  );
}

/** Penalty-taker mark: the little ball. */
export function PkMark() {
  return (
    <span title="Penalty taker" className="inline-flex text-ink-300">
      <svg viewBox="0 0 24 24" className="h-3 w-3" aria-label="Penalty taker">
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
        <path fill="currentColor" d="M12 7.6 16.2 10.7 14.6 15.6H9.4L7.8 10.7Z" />
      </svg>
    </span>
  );
}

/** Armband roundel — C solid white (the captain), V outlined (the deputy). */
export function Roundel({ kind }: { kind: 'C' | 'V' }) {
  return kind === 'C' ? (
    <span className="grid h-4 w-4 place-items-center rounded-full bg-ink-100 font-mono text-[9px] font-bold text-pitch-950 shadow-[0_0_0_2px_rgba(7,20,16,0.8)]">
      C
    </span>
  ) : (
    <span className="grid h-4 w-4 place-items-center rounded-full bg-pitch-950/90 font-mono text-[9px] font-bold text-ink-100 ring-1 ring-ink-100/60">
      V
    </span>
  );
}

export function StatTile({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="flex flex-col gap-0.5" title={hint}>
      <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-500">{label}</span>
      <span className="font-display text-2xl font-semibold leading-none text-ink-100 md:text-[28px]">
        {value}
      </span>
    </div>
  );
}

/** Empty state as a chalkboard drill: dashed outline, plain talk about what
 * will appear here and when. */
export function ChalkState({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line bg-pitch-900/40 px-5 py-6">
      <p className="mb-2 font-mono text-[11px] font-bold uppercase tracking-[0.22em] text-ink-300">
        {title}
      </p>
      <div className="max-w-prose text-sm leading-relaxed text-ink-500">{children}</div>
    </div>
  );
}

export function LoadingBlock() {
  return (
    <div className="flex items-center justify-center gap-2 py-16 font-mono text-xs uppercase tracking-[0.22em] text-ink-500">
      <span className="h-2 w-2 animate-pulse bg-volt" />
      Syncing
    </div>
  );
}

export function ErrorBlock({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
      Couldn't load data: {message}
    </div>
  );
}

/** Wraps a useJson() result, handling loading/error so pages only write the
 * ready-state view. */
export function DataGate<T>({
  state,
  children,
}: {
  state: Loadable<T>;
  children: (data: T) => ReactNode;
}) {
  if (state.status === 'loading') return <LoadingBlock />;
  if (state.status === 'error') return <ErrorBlock message={state.error} />;
  return <>{children(state.data)}</>;
}
