import type { ReactNode } from 'react';
import type { Loadable } from '../lib/useJson';

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-2xl border border-[var(--pitch-line)] bg-[var(--pitch-850)]/70 backdrop-blur-sm shadow-[0_1px_0_0_rgba(255,255,255,0.03)_inset] ${className}`}
    >
      {children}
    </div>
  );
}

export function SectionTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex items-center justify-between mb-3">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--ink-300)]">
        {children}
      </h2>
      {action}
    </div>
  );
}

export function LiveBadge({ isLive }: { isLive: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
        isLive
          ? 'bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30'
          : 'bg-amber-500/15 text-amber-300 ring-1 ring-amber-500/30'
      }`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${isLive ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`}
      />
      {isLive ? 'Live' : 'Demo data'}
    </span>
  );
}

export function FdrPill({ fdr }: { fdr: number | null | undefined }) {
  if (fdr == null) return <span className="text-[var(--ink-500)]">—</span>;
  const colors: Record<number, string> = {
    1: 'bg-emerald-500/25 text-emerald-200',
    2: 'bg-emerald-500/15 text-emerald-300',
    3: 'bg-[var(--pitch-700)] text-[var(--ink-300)]',
    4: 'bg-rose-500/20 text-rose-300',
    5: 'bg-rose-500/30 text-rose-200',
  };
  return (
    <span
      className={`inline-flex h-6 w-6 items-center justify-center rounded-md text-xs font-semibold ${colors[fdr] ?? colors[3]}`}
    >
      {fdr}
    </span>
  );
}

export function LoadingBlock() {
  return (
    <div className="flex items-center justify-center py-16 text-[var(--ink-500)] text-sm">
      Loading…
    </div>
  );
}

export function ErrorBlock({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
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

export function StatPill({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] uppercase tracking-wide text-[var(--ink-500)]">{label}</span>
      <span className="text-lg font-semibold text-[var(--ink-100)] tabular-nums">{value}</span>
    </div>
  );
}
