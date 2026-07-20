import { useJson } from '../lib/useJson';
import type { TransferMove, Transfers as TransfersData } from '../types';
import { PageHeader } from '../components/Layout';
import { Card, DataGate, Eyebrow, PosBadge, StatTile } from '../components/ui';

const CHIP_NAME: Record<string, string> = {
  wildcard: 'Wildcard',
  freehit: 'Free Hit',
  bboost: 'Bench Boost',
  '3xc': 'Triple Captain',
};

/** Half-circle gauge. Arc length for r=44 over 180° is π·44 ≈ 138.2. */
function ConfidenceDial({ value }: { value: number }) {
  const ARC = Math.PI * 44;
  const frac = Math.max(0, Math.min(1, value / 100));
  const tone = value >= 70 ? 'var(--color-volt)' : value >= 45 ? 'var(--color-ink-300)' : 'var(--color-armband)';
  return (
    <div className="relative w-44 shrink-0">
      <svg viewBox="0 0 100 54" className="w-full">
        <path
          d="M6 50 A44 44 0 0 1 94 50"
          fill="none"
          stroke="rgba(255,255,255,0.07)"
          strokeWidth="7"
          strokeLinecap="round"
        />
        <path
          d="M6 50 A44 44 0 0 1 94 50"
          fill="none"
          stroke={tone}
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray={`${frac * ARC} ${ARC}`}
        />
      </svg>
      <div className="absolute inset-x-0 bottom-0 text-center">
        <span className="font-display text-4xl font-bold leading-none text-ink-100 tabular-nums">
          {value.toFixed(0)}
          <span className="text-lg text-ink-500">%</span>
        </span>
        <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-ink-500">confidence</p>
      </div>
    </div>
  );
}

function SwapCard({ move }: { move: TransferMove }) {
  const positive = move.net_ev >= 0;
  return (
    <Card className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <PosBadge pos={move.out.position} />
        <span
          className={`rounded-sm px-2 py-0.5 font-mono text-[11px] font-bold tabular-nums ${
            positive ? 'bg-volt/10 text-volt ring-1 ring-volt/30' : 'bg-danger/10 text-danger ring-1 ring-danger/30'
          }`}
        >
          {positive ? '+' : ''}
          {move.net_ev.toFixed(2)} EV
        </span>
      </div>
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
        <div className="min-w-0">
          <p className="font-mono text-[9px] font-bold uppercase tracking-[0.2em] text-danger/80">Out</p>
          <p className="truncate font-semibold text-ink-100">{move.out.name}</p>
          <p className="font-mono text-[10px] uppercase text-ink-500">
            {move.out.team ?? '—'} · £{move.out.price != null ? move.out.price.toFixed(1) : '—'}m
          </p>
          <p className="mt-1 font-mono text-xs text-ink-300 tabular-nums">
            EV {move.compare.out_ev != null ? move.compare.out_ev.toFixed(2) : '—'}
          </p>
        </div>
        <span aria-hidden className="px-1 text-lg text-ink-500">
          →
        </span>
        <div className="min-w-0 text-right">
          <p className="font-mono text-[9px] font-bold uppercase tracking-[0.2em] text-volt/90">In</p>
          <p className="truncate font-semibold text-ink-100">{move.in.name}</p>
          <p className="font-mono text-[10px] uppercase text-ink-500">
            {move.in.team ?? '—'} · £{move.in.price != null ? move.in.price.toFixed(1) : '—'}m
          </p>
          <p className="mt-1 font-mono text-xs font-bold text-volt tabular-nums">
            EV {move.compare.in_ev != null ? move.compare.in_ev.toFixed(2) : '—'}
          </p>
        </div>
      </div>
    </Card>
  );
}

export default function Transfers() {
  const state = useJson<TransfersData>('transfers.json');

  return (
    <div>
      <PageHeader
        title="Transfers"
        subtitle="The optimizer's swap candidates, ranked by net EV over the 8-gameweek decayed horizon — with how sure the model is about this week's plan."
      />
      <DataGate state={state}>
        {(t) => (
          <div className="flex flex-col gap-5">
            <Card className="p-4 md:p-5">
              <div className="flex flex-wrap items-center gap-x-8 gap-y-4">
                <ConfidenceDial value={t.confidence} />
                <div className="grid grow grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
                  <StatTile
                    label="Bank"
                    value={t.bank != null ? `£${(t.bank / 10).toFixed(1)}m` : '—'}
                  />
                  <StatTile label="Free transfers" value={t.free_transfers ?? '—'} />
                  <StatTile label="Gameweek" value={`GW${t.gw}`} />
                </div>
              </div>
              <p className="mt-4 border-t border-line pt-3 text-xs leading-relaxed text-ink-500">
                Confidence is quantile-spread-based: how tightly the model's q10–q90 bands bracket
                the recommended picks. Narrow bands mean the plan rests on predictable players;
                wide ones mean this week could go many ways.
              </p>
            </Card>

            {t.chip_advice && (
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-r-md border-l-2 border-armband bg-armband/[0.06] px-4 py-3">
                <span className="font-mono text-[10px] font-bold uppercase tracking-[0.22em] text-armband">
                  Chip advice
                </span>
                <span className="font-display text-xl font-bold uppercase leading-none text-ink-100">
                  {CHIP_NAME[t.chip_advice.chip] ?? t.chip_advice.chip} · GW{t.chip_advice.gw}
                </span>
                {t.chip_advice.ev != null && (
                  <span className="font-mono text-xs text-ink-300 tabular-nums">
                    +{t.chip_advice.ev.toFixed(1)} EV
                  </span>
                )}
              </div>
            )}

            {t.moves.length > 0 && (
              <div>
                <Eyebrow>Recommended moves</Eyebrow>
                <div className="grid gap-3 md:grid-cols-2">
                  {t.moves.map((m, i) => (
                    <SwapCard key={i} move={m} />
                  ))}
                </div>
              </div>
            )}

            <div>
              <Eyebrow>Single-swap upgrades</Eyebrow>
              {t.moves.length === 0 && (
                <p className="-mt-1 mb-3 text-xs text-ink-500">
                  Pre-season there's no owned squad to transfer from — these are upgrades on the
                  recommended draft itself.
                </p>
              )}
              {t.alternatives.length === 0 ? (
                <p className="text-sm text-ink-500">No alternatives available.</p>
              ) : (
                <div className="grid gap-3 md:grid-cols-2">
                  {t.alternatives.map((m, i) => (
                    <SwapCard key={i} move={m} />
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </DataGate>
    </div>
  );
}
