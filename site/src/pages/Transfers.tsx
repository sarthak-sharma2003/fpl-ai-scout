import { useJson } from '../lib/useJson';
import type { Transfers as TransfersData, TransferMove } from '../types';
import { PageHeader } from '../components/Layout';
import { Card, DataGate, SectionTitle, StatPill } from '../components/ui';

function MoveRow({ move }: { move: TransferMove }) {
  const positive = move.net_ev >= 0;
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl bg-[var(--pitch-900)]/60 ring-1 ring-[var(--pitch-line)] px-3 py-3">
      <div className="flex items-center gap-3 min-w-0">
        <div className="flex flex-col items-start min-w-0">
          <span className="text-[10px] uppercase tracking-wide text-rose-300/80">Out</span>
          <span className="text-sm font-medium text-[var(--ink-100)] truncate">{move.out.name}</span>
          <span className="text-xs text-[var(--ink-500)] tabular-nums">
            EV {move.compare.out_ev?.toFixed(1) ?? '—'}
          </span>
        </div>
        <span className="text-[var(--ink-500)]">→</span>
        <div className="flex flex-col items-start min-w-0">
          <span className="text-[10px] uppercase tracking-wide text-emerald-300/80">In</span>
          <span className="text-sm font-medium text-[var(--ink-100)] truncate">{move.in.name}</span>
          <span className="text-xs text-[var(--ink-500)] tabular-nums">
            EV {move.compare.in_ev?.toFixed(1) ?? '—'}
          </span>
        </div>
      </div>
      <span
        className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold tabular-nums ${
          positive ? 'bg-emerald-500/15 text-emerald-300' : 'bg-rose-500/15 text-rose-300'
        }`}
      >
        {positive ? '+' : ''}
        {move.net_ev.toFixed(1)}
      </span>
    </div>
  );
}

export default function Transfers() {
  const state = useJson<TransfersData>('transfers.json');

  return (
    <div>
      <PageHeader title="Transfers" subtitle="This gameweek's optimal moves and alternatives." />
      <DataGate state={state}>
        {(t) => (
          <div className="flex flex-col gap-5">
            <Card className="p-4 md:p-5">
              <div className="flex flex-wrap gap-6">
                <StatPill label="AI Confidence" value={`${t.confidence}%`} />
                <StatPill label="Bank" value={t.bank != null ? `£${(t.bank / 10).toFixed(1)}m` : '—'} />
                <StatPill label="Free transfers" value={t.free_transfers ?? '—'} />
                {t.chip_advice && (
                  <StatPill label="Chip advice" value={`${t.chip_advice.chip} · GW${t.chip_advice.gw}`} />
                )}
              </div>
            </Card>

            {t.moves.length > 0 && (
              <Card className="p-4 md:p-5">
                <SectionTitle>Recommended moves</SectionTitle>
                <div className="flex flex-col gap-2">
                  {t.moves.map((m, i) => (
                    <MoveRow key={i} move={m} />
                  ))}
                </div>
              </Card>
            )}

            <Card className="p-4 md:p-5">
              <SectionTitle>Top single-swap upgrades</SectionTitle>
              {t.alternatives.length === 0 ? (
                <p className="text-sm text-[var(--ink-500)]">No alternatives available.</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {t.alternatives.map((m, i) => (
                    <MoveRow key={i} move={m} />
                  ))}
                </div>
              )}
            </Card>
          </div>
        )}
      </DataGate>
    </div>
  );
}
