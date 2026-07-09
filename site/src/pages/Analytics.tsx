import { useJson } from '../lib/useJson';
import type { Analytics as AnalyticsData, SplitSummary } from '../types';
import { PageHeader } from '../components/Layout';
import { Card, DataGate, SectionTitle, StatPill } from '../components/ui';

function SplitCard({ split }: { split: SplitSummary }) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-semibold text-[var(--ink-100)] capitalize">
          {split.split_label} split
        </span>
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-medium ${
            split.beats_naive
              ? 'bg-emerald-500/15 text-emerald-300'
              : 'bg-rose-500/15 text-rose-300'
          }`}
        >
          {split.beats_naive ? 'Beats naive' : 'Below naive'}
        </span>
      </div>
      <p className="text-xs text-[var(--ink-500)] mb-3">
        Train {split.train_seasons.join(', ')} · Holdout {split.holdout_season}
      </p>
      <div className="grid grid-cols-2 gap-4">
        <StatPill label="Model per-GW Spearman" value={split.model_mean_per_gw_spearman.toFixed(3)} />
        <StatPill label="Naive per-GW Spearman" value={split.naive_mean_per_gw_spearman.toFixed(3)} />
        <StatPill label="Model RMSE" value={split.model_rmse.toFixed(2)} />
        <StatPill label="Naive RMSE" value={split.naive_rmse.toFixed(2)} />
      </div>
    </Card>
  );
}

export default function Analytics() {
  const state = useJson<AnalyticsData>('analytics.json');

  return (
    <div>
      <PageHeader title="Analytics" subtitle="Model validation and full-season backtest results." />
      <DataGate state={state}>
        {(a) => (
          <div className="flex flex-col gap-5">
            <Card className="p-4 md:p-5">
              <SectionTitle>Model version</SectionTitle>
              <p className="text-sm text-[var(--ink-100)] font-mono">{a.model_version ?? '—'}</p>
            </Card>

            {a.validation && (
              <div>
                <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--ink-300)] mb-3">
                  Validation (holdout accuracy)
                </h2>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <SplitCard split={a.validation.primary} />
                  <SplitCard split={a.validation.secondary} />
                </div>
              </div>
            )}

            {a.backtest && (
              <Card className="p-4 md:p-5">
                <SectionTitle>Full-season backtest</SectionTitle>
                <p className="text-xs text-[var(--ink-500)] mb-3">
                  Plan's stated go/no-go bar: {a.backtest.plan_stated_target} pts. Real 2025-26
                  average-manager benchmark: {a.backtest.real_2025_26_average_manager} pts.
                </p>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm min-w-[420px]">
                    <thead>
                      <tr className="border-b border-[var(--pitch-line)] text-left text-[var(--ink-500)] text-xs uppercase tracking-wide">
                        <th className="py-2 pr-3">Season</th>
                        <th className="py-2 pr-3">Total points</th>
                        <th className="py-2 pr-3">Hits</th>
                        <th className="py-2">Chips used</th>
                      </tr>
                    </thead>
                    <tbody>
                      {a.backtest.seasons.map((s) => (
                        <tr key={s.season} className="border-b border-[var(--pitch-line)]/60">
                          <td className="py-2.5 pr-3 font-medium text-[var(--ink-100)]">{s.season}</td>
                          <td className="py-2.5 pr-3 tabular-nums text-emerald-300 font-semibold">
                            {s.total_points}
                          </td>
                          <td className="py-2.5 pr-3 tabular-nums">{s.total_hits}</td>
                          <td className="py-2.5 text-[var(--ink-500)] text-xs">
                            {s.chips_used.length > 0
                              ? s.chips_used.map((c) => `GW${c.gw}:${c.chip}`).join(', ')
                              : 'none'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </div>
        )}
      </DataGate>
    </div>
  );
}
