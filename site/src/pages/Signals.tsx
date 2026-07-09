import { useJson } from '../lib/useJson';
import type { Signals as SignalsData, SignalCard } from '../types';
import { PageHeader } from '../components/Layout';
import { Card, DataGate, SectionTitle } from '../components/ui';

function SignalRow({ s, positive }: { s: SignalCard; positive: boolean }) {
  return (
    <div className="flex items-center justify-between rounded-lg bg-[var(--pitch-900)]/60 px-3 py-2.5 text-sm">
      <div className="flex flex-col">
        <span className="font-medium text-[var(--ink-100)]">{s.name}</span>
        <span className="text-xs text-[var(--ink-500)]">
          {s.price != null ? `£${s.price.toFixed(1)}m` : ''}
          {s.selected_by != null ? ` · ${(s.selected_by / 1000).toFixed(0)}k owned` : ''}
        </span>
      </div>
      <span className={`font-semibold tabular-nums ${positive ? 'text-emerald-300' : 'text-rose-300'}`}>
        {positive ? '+' : ''}
        {(s.transfers_balance / 1000).toFixed(0)}k
      </span>
    </div>
  );
}

export default function Signals() {
  const state = useJson<SignalsData>('signals.json');

  return (
    <div>
      <PageHeader title="Signals" subtitle="Price-change risk and ownership swings." />
      <DataGate state={state}>
        {(s) => (
          <div className="flex flex-col gap-5">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <Card className="p-4 md:p-5">
                <SectionTitle>Price risers</SectionTitle>
                <div className="flex flex-col gap-2">
                  {s.price_risers.map((r) => (
                    <SignalRow key={r.code} s={r} positive />
                  ))}
                </div>
              </Card>
              <Card className="p-4 md:p-5">
                <SectionTitle>Price fallers</SectionTitle>
                <div className="flex flex-col gap-2">
                  {s.price_fallers.map((r) => (
                    <SignalRow key={r.code} s={r} positive={false} />
                  ))}
                </div>
              </Card>
            </div>
            <Card className="p-4 md:p-5">
              <SectionTitle>Injury news</SectionTitle>
              <p className="text-sm text-[var(--ink-500)]">{s.injury_news_note ?? 'No news.'}</p>
            </Card>
          </div>
        )}
      </DataGate>
    </div>
  );
}
