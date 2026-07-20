import { useJson } from '../lib/useJson';
import type { SignalCard, Signals as SignalsData } from '../types';
import { PageHeader } from '../components/Layout';
import { Card, ChalkState, DataGate, Eyebrow } from '../components/ui';

const fmtBalance = (n: number) => `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n).toLocaleString('en-GB')}`;

function SignalList({ rows, positive }: { rows: SignalCard[]; positive: boolean }) {
  return (
    <ol className="flex flex-col gap-1">
      {rows.map((s, i) => (
        <li
          key={s.code}
          className="flex items-center justify-between gap-3 rounded-md px-2 py-2 odd:bg-white/[0.02]"
        >
          <span className="flex min-w-0 items-center gap-2.5">
            <span className="w-4 shrink-0 text-right font-mono text-[10px] text-ink-500 tabular-nums">
              {i + 1}
            </span>
            <span className="min-w-0">
              <span className="block truncate text-sm font-semibold text-ink-100">{s.name}</span>
              <span className="font-mono text-[9px] uppercase tracking-wide text-ink-500">
                {s.price != null ? `£${s.price.toFixed(1)}m` : ''}
                {s.selected_by != null ? ` · ${s.selected_by.toLocaleString('en-GB')} selected` : ''}
              </span>
            </span>
          </span>
          <span
            className={`shrink-0 font-mono text-sm font-bold tabular-nums ${
              positive ? 'text-volt' : 'text-danger'
            }`}
          >
            {fmtBalance(s.transfers_balance)}
          </span>
        </li>
      ))}
    </ol>
  );
}

export default function Signals() {
  const state = useJson<SignalsData>('signals.json');

  return (
    <div>
      <PageHeader
        title="Signals"
        subtitle="Overnight market intelligence from the live API — net transfer momentum behind price moves, and availability news as it lands."
      />
      <DataGate state={state}>
        {(s) => (
          <div className="flex flex-col gap-5">
            <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
              <Card className="p-4 md:p-5">
                <Eyebrow>Price risers</Eyebrow>
                {s.price_risers.length === 0 ? (
                  <ChalkState title="No momentum yet">
                    <p>
                      Risers rank by net transfers in — the queue that triggers a price rise.
                      Populates once the 26/27 transfer market opens and refreshes run.
                    </p>
                  </ChalkState>
                ) : (
                  <SignalList rows={s.price_risers} positive />
                )}
              </Card>
              <Card className="p-4 md:p-5">
                <Eyebrow>Price fallers</Eyebrow>
                {s.price_fallers.length === 0 ? (
                  <ChalkState title="No momentum yet">
                    <p>
                      Fallers rank by net transfers out — sell-offs that threaten a price drop and
                      our team value. Populates with the live market.
                    </p>
                  </ChalkState>
                ) : (
                  <SignalList rows={s.price_fallers} positive={false} />
                )}
              </Card>
            </div>

            <div>
              <Eyebrow>Injury &amp; availability news</Eyebrow>
              {s.injury_news.length === 0 ? (
                <ChalkState title="No availability signals yet">
                  <p>{s.injury_news_note ?? 'No news.'}</p>
                </ChalkState>
              ) : (
                <Card className="p-4">
                  <p className="text-sm text-ink-300">
                    {s.injury_news.length} item{s.injury_news.length > 1 ? 's' : ''} — full
                    rendering lands with the first live refresh of the season.
                  </p>
                </Card>
              )}
            </div>
          </div>
        )}
      </DataGate>
    </div>
  );
}
