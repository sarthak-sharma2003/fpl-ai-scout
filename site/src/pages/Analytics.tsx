import { useJson } from '../lib/useJson';
import type { Analytics as AnalyticsData, BacktestSeason, SplitSummary } from '../types';
import { PageHeader } from '../components/Layout';
import { Card, DataGate, Eyebrow } from '../components/ui';

const CHIP_ABBR: Record<string, string> = {
  triple_captain: 'TC',
  bench_boost: 'BB',
  wildcard: 'WC',
  freehit: 'FH',
  '3xc': 'TC',
  bboost: 'BB',
};

/** Model-vs-naive paired bars on a shared scale. */
function PairBars({
  label,
  model,
  naive,
  digits,
  note,
}: {
  label: string;
  model: number;
  naive: number;
  digits: number;
  note?: string;
}) {
  const max = Math.max(model, naive) * 1.12;
  const row = (name: string, v: number, tone: 'volt' | 'dim') => (
    <div className="flex items-center gap-2">
      <span className="w-12 shrink-0 font-mono text-[9px] uppercase tracking-[0.12em] text-ink-500">
        {name}
      </span>
      <div className="h-[7px] flex-1 rounded-full bg-white/[0.05]">
        <div
          className={`h-full rounded-full ${tone === 'volt' ? 'bg-volt' : 'bg-ink-500/50'}`}
          style={{ width: `${(v / max) * 100}%` }}
        />
      </div>
      <span
        className={`w-14 shrink-0 text-right font-mono text-xs font-bold tabular-nums ${
          tone === 'volt' ? 'text-volt' : 'text-ink-300'
        }`}
      >
        {v.toFixed(digits)}
      </span>
    </div>
  );
  return (
    <div>
      <p className="mb-1.5 flex items-baseline justify-between font-mono text-[9px] uppercase tracking-[0.16em] text-ink-500">
        <span className="font-bold text-ink-300">{label}</span>
        {note && <span>{note}</span>}
      </p>
      <div className="flex flex-col gap-1.5">
        {row('model', model, 'volt')}
        {row('naive', naive, 'dim')}
      </div>
    </div>
  );
}

function SplitCard({ split }: { split: SplitSummary }) {
  return (
    <Card className="p-4">
      <div className="mb-1 flex items-center justify-between gap-3">
        <span className="font-display text-lg font-semibold uppercase leading-none text-ink-100">
          {split.split_label} split
        </span>
        <span
          className={`rounded-sm px-2 py-0.5 font-mono text-[9px] font-bold uppercase tracking-[0.14em] ring-1 ${
            split.beats_naive
              ? 'bg-volt/10 text-volt ring-volt/40'
              : 'bg-danger/10 text-danger ring-danger/40'
          }`}
        >
          {split.beats_naive ? 'Beats naive' : 'Below naive'}
        </span>
      </div>
      <p className="mb-4 font-mono text-[9px] uppercase tracking-[0.12em] text-ink-500">
        train {split.train_seasons.join(' ')} → holdout {split.holdout_season}
      </p>
      <div className="flex flex-col gap-4">
        <PairBars
          label="Mean per-GW Spearman"
          note="higher is better"
          model={split.model_mean_per_gw_spearman}
          naive={split.naive_mean_per_gw_spearman}
          digits={3}
        />
        <PairBars
          label="RMSE"
          note="lower is better"
          model={split.model_rmse}
          naive={split.naive_rmse}
          digits={2}
        />
      </div>
    </Card>
  );
}

/** 38 tiny bars, one per GW; amber underline marks chip weeks. */
function SeasonSparkline({ season, max }: { season: BacktestSeason; max: number }) {
  const chipGws = new Set(season.chips_used.map((c) => c.gw));
  return (
    <div className="flex h-12 items-end gap-[2px]">
      {season.gw_scores.map((g) => (
        <div
          key={g.gw}
          className="flex flex-1 flex-col justify-end gap-[2px]"
          title={`GW${g.gw} · ${g.score} pts${g.hits ? ` · ${g.hits} hit${g.hits > 1 ? 's' : ''}` : ''}${
            chipGws.has(g.gw) ? ' · chip' : ''
          }`}
        >
          <div
            className="w-full rounded-[1px] bg-volt/45"
            style={{ height: `${Math.max(4, (g.score / max) * 40)}px` }}
          />
          <div className={`h-[3px] w-full rounded-[1px] ${chipGws.has(g.gw) ? 'bg-armband' : 'bg-transparent'}`} />
        </div>
      ))}
    </div>
  );
}

function BacktestBlock({ backtest }: { backtest: NonNullable<AnalyticsData['backtest']> }) {
  const bench = backtest.real_2025_26_average_manager;
  const target = backtest.plan_stated_target;
  const scaleMax = Math.max(target, bench, ...backtest.seasons.map((s) => s.total_points)) * 1.06;
  return (
    <Card className="p-4 md:p-5">
      <Eyebrow>Full-season replay backtest</Eyebrow>
      <p className="mb-4 text-xs leading-relaxed text-ink-500">
        The whole pipeline replayed over past seasons on frozen data. Benchmark: the real 2025-26
        average manager finished on {bench.toLocaleString('en-GB')}; the plan's stated go/no-go bar
        is {target.toLocaleString('en-GB')}.
      </p>
      <div className="flex flex-col gap-6">
        {backtest.seasons.map((s) => (
          <div key={s.season}>
            <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="font-display text-xl font-bold uppercase leading-none text-ink-100">
                {s.season}
              </span>
              <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-ink-500">
                {s.total_hits} hits taken
              </span>
              {s.chips_used.map((c, i) => (
                <span
                  key={i}
                  className="rounded-sm bg-white/5 px-1.5 py-px font-mono text-[9px] font-bold uppercase text-ink-300 ring-1 ring-line"
                >
                  {CHIP_ABBR[c.chip] ?? c.chip}
                  {c.gw}
                </span>
              ))}
            </div>
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2">
                <span className="w-24 shrink-0 font-mono text-[9px] uppercase tracking-[0.1em] text-ink-500">
                  model
                </span>
                <div className="relative h-[9px] flex-1 rounded-full bg-white/[0.05]">
                  <div
                    className="h-full rounded-full bg-volt"
                    style={{ width: `${(s.total_points / scaleMax) * 100}%` }}
                  />
                  <div
                    title={`Plan target ${target}`}
                    className="absolute -top-0.5 h-3 w-[2px] bg-armband/80"
                    style={{ left: `${(target / scaleMax) * 100}%` }}
                  />
                </div>
                <span className="w-12 shrink-0 text-right font-mono text-sm font-bold text-volt tabular-nums">
                  {s.total_points}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-24 shrink-0 font-mono text-[9px] uppercase tracking-[0.1em] text-ink-500">
                  avg manager
                </span>
                <div className="h-[9px] flex-1 rounded-full bg-white/[0.05]">
                  <div
                    className="h-full rounded-full bg-ink-500/50"
                    style={{ width: `${(bench / scaleMax) * 100}%` }}
                  />
                </div>
                <span className="w-12 shrink-0 text-right font-mono text-sm font-bold text-ink-300 tabular-nums">
                  {bench}
                </span>
              </div>
            </div>
            <div className="mt-3">
              <SeasonSparkline
                season={s}
                max={Math.max(...backtest.seasons.flatMap((x) => x.gw_scores.map((g) => g.score)))}
              />
              <p className="mt-1 font-mono text-[8px] uppercase tracking-[0.16em] text-ink-500">
                GW1 → GW38 · amber tick = chip week
              </p>
            </div>
          </div>
        ))}
      </div>
      <p className="mt-4 border-t border-line/60 pt-3 text-[11px] leading-relaxed text-ink-500">
        The avg-manager line is the real 25/26 field; the replay can't capture in-season price
        knowledge, so treat the gap as directional, not a promise.
      </p>
    </Card>
  );
}

export default function Analytics() {
  const state = useJson<AnalyticsData>('analytics.json');

  return (
    <div>
      <PageHeader
        title="Analytics"
        subtitle="How honest the model is — holdout validation against a naive baseline, and the full pipeline replayed over past seasons."
      />
      <DataGate state={state}>
        {(a) => (
          <div className="flex flex-col gap-5">
            {a.validation && (
              <div>
                <Eyebrow
                  action={
                    <span
                      className={`rounded-sm px-2 py-0.5 font-mono text-[9px] font-bold uppercase tracking-[0.14em] ring-1 ${
                        a.validation.beats_naive_decision
                          ? 'bg-volt/10 text-volt ring-volt/40'
                          : 'bg-danger/10 text-danger ring-danger/40'
                      }`}
                    >
                      {a.validation.beats_naive_decision ? 'Ship decision: beats naive' : 'Below naive'}
                    </span>
                  }
                >
                  Validation · holdout, honest
                </Eyebrow>
                <p className="-mt-1 mb-3 text-xs text-ink-500">
                  Scores on seasons the model never trained on — the naive baseline is
                  last-season points-per-game carried forward.
                </p>
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <SplitCard split={a.validation.primary} />
                  <SplitCard split={a.validation.secondary} />
                </div>
              </div>
            )}

            {a.backtest && <BacktestBlock backtest={a.backtest} />}

            <p className="flex flex-wrap gap-x-2 gap-y-1 font-mono text-[9px] uppercase tracking-[0.14em] text-ink-500">
              <span>model {a.model_version ?? '—'}</span>
              {a.validation && (
                <>
                  <span aria-hidden>·</span>
                  <span>validation runs {a.validation.primary.version} / {a.validation.secondary.version}</span>
                </>
              )}
            </p>
          </div>
        )}
      </DataGate>
    </div>
  );
}
