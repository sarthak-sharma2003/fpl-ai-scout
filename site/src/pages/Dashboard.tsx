import { useJson } from '../lib/useJson';
import type { Dashboard as DashboardData } from '../types';
import { PageHeader } from '../components/Layout';
import { Card, DataGate, LiveBadge, StatPill } from '../components/ui';
import PlayerChip from '../components/PlayerChip';

const ROWS: { key: 'gk' | 'def' | 'mid' | 'fwd'; label: string }[] = [
  { key: 'gk', label: 'GK' },
  { key: 'def', label: 'DEF' },
  { key: 'mid', label: 'MID' },
  { key: 'fwd', label: 'FWD' },
];

export default function Dashboard() {
  const state = useJson<DashboardData>('dashboard.json');

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="This gameweek's recommended squad and headline numbers."
      />
      <DataGate state={state}>
        {(d) => (
          <div className="flex flex-col gap-5">
            <Card className="p-4 md:p-5">
              <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
                <div className="flex items-center gap-3">
                  <span className="text-sm font-medium text-[var(--ink-300)]">
                    {d.season} · GW{d.gw}
                  </span>
                  <LiveBadge isLive={d.is_live} />
                </div>
                {d.deadline && (
                  <span className="text-xs text-[var(--ink-500)]">
                    Deadline: {new Date(d.deadline).toLocaleString()}
                  </span>
                )}
              </div>
              <div className="flex flex-wrap gap-6">
                <StatPill label="Projected points" value={d.our_points ?? '—'} />
                <StatPill label="Avg. manager (season)" value={d.avg_points ?? '—'} />
                <StatPill label="Overall rank" value={d.overall_rank ?? '—'} />
                {d.mini_league && (
                  <StatPill
                    label="Mini-league"
                    value={`${d.mini_league.pos} / ${d.mini_league.size}`}
                  />
                )}
              </div>
              <p className="mt-4 text-sm text-[var(--ink-300)] leading-relaxed">{d.insight.text}</p>
              <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-xs text-[var(--ink-500)]">
                {d.insight.transfer_summary && <span>{d.insight.transfer_summary}</span>}
                {d.insight.captain && <span>Captain: {d.insight.captain}</span>}
              </div>
            </Card>

            <Card className="p-4 md:p-5">
              <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--ink-300)] mb-4">
                Starting XI
              </h2>
              <div className="rounded-2xl bg-gradient-to-b from-[var(--pitch-800)] to-[var(--pitch-900)] ring-1 ring-[var(--pitch-line)] p-3 md:p-5 flex flex-col gap-4">
                {ROWS.map(({ key, label }) => (
                  <div key={key} className="flex flex-wrap justify-center gap-2 md:gap-3">
                    {d.pitch[key].length === 0 ? (
                      <span className="text-xs text-[var(--ink-500)]">{label}: —</span>
                    ) : (
                      d.pitch[key].map((p) => (
                        <PlayerChip
                          key={p.code}
                          player={p}
                          captain={d.insight.captain === p.name}
                        />
                      ))
                    )}
                  </div>
                ))}
              </div>
            </Card>

            <Card className="p-4 md:p-5">
              <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--ink-300)] mb-3">
                Bench
              </h2>
              {d.bench_order.length === 0 ? (
                <p className="text-sm text-[var(--ink-500)]">No bench data.</p>
              ) : (
                <ol className="flex flex-col gap-2">
                  {d.bench_order.map((b, i) => (
                    <li
                      key={i}
                      className="flex items-center justify-between rounded-lg bg-[var(--pitch-900)]/60 px-3 py-2 text-sm"
                    >
                      <span className="flex items-center gap-2">
                        <span className="text-[var(--ink-500)] tabular-nums">{i + 1}.</span>
                        <span className="text-[var(--ink-100)]">{b.name}</span>
                        <span className="text-[var(--ink-500)] text-xs">{b.team}</span>
                      </span>
                      <span className="text-emerald-300 font-medium tabular-nums">
                        {b.ev.toFixed(1)}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </Card>
          </div>
        )}
      </DataGate>
    </div>
  );
}
