import { useEffect, useState } from 'react';
import { combine, useJson } from '../lib/useJson';
import type { Dashboard as DashboardData, PlayerCard, Transfers as TransfersData } from '../types';
import { PageHeader } from '../components/Layout';
import { Card, DataGate, Eyebrow, StateBadge, StatTile } from '../components/ui';
import PitchCard from '../components/PlayerChip';

/** Live deadline countdown, re-derived every 30s. Hidden when the deadline
 * is null, unparseable, or already past. */
function Countdown({ deadline }: { deadline: string | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);
  if (!deadline) return null;
  const ms = new Date(deadline).getTime() - now;
  if (Number.isNaN(ms) || ms <= 0) return null;
  const d = Math.floor(ms / 86_400_000);
  const h = Math.floor((ms % 86_400_000) / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const abs = new Date(deadline).toLocaleString([], {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
  return (
    <div className="text-right">
      <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-500">
        Deadline · {abs}
      </p>
      <div className="flex items-end justify-end gap-2.5">
        {[
          { v: d, u: 'days' },
          { v: h, u: 'hrs' },
          { v: m, u: 'min' },
        ].map(({ v, u }) => (
          <div key={u} className="text-center">
            <span className="block font-display text-3xl font-bold leading-none text-ink-100 tabular-nums md:text-4xl">
              {String(v).padStart(2, '0')}
            </span>
            <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-ink-500">{u}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** The pitch: mowing stripes, chalk markings, goal at the top, formation
 * rows flowing down toward the halfway line. */
function Pitch({ d }: { d: DashboardData }) {
  const rows: PlayerCard[][] = [d.pitch.gk, d.pitch.def, d.pitch.mid, d.pitch.fwd];
  const badge = (p: PlayerCard): 'C' | 'V' | undefined =>
    p.code === d.captain_code ? 'C' : p.code === d.vice_captain_code ? 'V' : undefined;
  return (
    <div
      className="relative overflow-hidden rounded-lg ring-1 ring-line"
      style={{
        background:
          'repeating-linear-gradient(0deg, rgba(255,255,255,0.022) 0 44px, rgba(255,255,255,0) 44px 88px), linear-gradient(180deg, #132a1c 0%, #0b1d13 100%)',
      }}
    >
      {/* chalk */}
      <div aria-hidden className="pointer-events-none absolute inset-0 opacity-50">
        <div className="absolute inset-x-3 bottom-0 top-4 border border-b-0 border-white/25">
          <div className="absolute left-1/2 top-0 h-[18%] w-[58%] -translate-x-1/2 border border-t-0 border-white/25" />
          <div className="absolute left-1/2 top-0 h-[7.5%] w-[27%] -translate-x-1/2 border border-t-0 border-white/25" />
          <div className="absolute left-1/2 top-[18%] h-9 w-24 -translate-x-1/2 rounded-b-full border border-t-0 border-white/25" />
          <div className="absolute left-1/2 top-[13%] h-1 w-1 -translate-x-1/2 rounded-full bg-white/40" />
          <div className="absolute bottom-0 left-1/2 h-40 w-40 -translate-x-1/2 translate-y-1/2 rounded-full border border-white/25" />
        </div>
        {/* goal mouth */}
        <div className="absolute left-1/2 top-1.5 h-2.5 w-24 -translate-x-1/2 border-x-2 border-t-2 border-white/40" />
      </div>
      <div className="relative z-10 flex flex-col gap-3.5 px-2 pb-8 pt-6 md:gap-6 md:px-8 md:pb-12 md:pt-8">
        {rows.map((row, i) => (
          <div key={i} className="flex justify-center gap-1.5 md:gap-4">
            {row.map((p) => (
              <div key={p.code} className="w-[19%] min-w-[62px] max-w-[104px]">
                <PitchCard player={p} badge={badge(p)} />
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Dashboard() {
  const dash = useJson<DashboardData>('dashboard.json');
  const transfers = useJson<TransfersData>('transfers.json');
  const state = combine(dash, transfers);

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="The model's recommended squad for the gameweek — EV from the ML projections, formation and bench from the MILP optimizer."
      />
      <DataGate state={state}>
        {([d, t]) => {
          const formation = `${d.pitch.def.length}-${d.pitch.mid.length}-${d.pitch.fwd.length}`;
          return (
            <div className="flex flex-col gap-5">
              {/* Header band */}
              <Card className="p-4 md:p-6">
                <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
                  <div>
                    <div className="flex items-center gap-3">
                      <span className="font-display text-5xl font-bold uppercase leading-none text-ink-100 md:text-6xl">
                        GW{d.gw}
                      </span>
                      <div className="flex flex-col items-start gap-1.5">
                        <StateBadge state={d.state} />
                        <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-ink-500">
                          {d.season}
                        </span>
                      </div>
                    </div>
                    {d.state === 'provisional' && (
                      <p className="mt-2 max-w-sm text-xs leading-relaxed text-armband/90">
                        Provisional = the pre-launch preview: real released fixtures, stand-in
                        end-of-25/26 prices. Regenerates the day FPL 26/27 goes live.
                      </p>
                    )}
                  </div>
                  <Countdown deadline={d.deadline} />
                </div>
                <div className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4 border-t border-line pt-4 sm:grid-cols-4">
                  <StatTile
                    label="Projected XI pts"
                    value={d.our_points != null ? d.our_points.toFixed(1) : '—'}
                    hint="Sum of the starting XI's expected points, captain doubled"
                  />
                  <StatTile
                    label="Avg manager"
                    value={d.avg_points != null ? d.avg_points : '—'}
                    hint="The live average manager score — the benchmark to beat"
                  />
                  <StatTile
                    label="Confidence"
                    value={`${t.confidence.toFixed(0)}%`}
                    hint="Quantile-spread confidence in this week's plan — details on Transfers"
                  />
                  <StatTile
                    label="Mini-league"
                    value={d.mini_league ? `${d.mini_league.pos}/${d.mini_league.size}` : '—'}
                    hint="Our rank in the 8-manager league once it syncs"
                  />
                </div>
                <div className="mt-5 rounded-r-md border-l-2 border-volt bg-volt/[0.05] px-4 py-3">
                  <p className="mb-1 font-mono text-[10px] font-bold uppercase tracking-[0.22em] text-volt">
                    Model briefing
                  </p>
                  <p className="text-sm leading-relaxed text-ink-300">{d.insight.text}</p>
                  <p className="mt-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-500">
                    {d.insight.captain && <span>Captain {d.insight.captain}</span>}
                    {d.insight.captain && d.insight.transfer_summary && <span> · </span>}
                    {d.insight.transfer_summary && <span>{d.insight.transfer_summary}</span>}
                  </p>
                </div>
              </Card>

              {/* Pitch */}
              <div>
                <Eyebrow
                  action={
                    <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-500">
                      C captain · V vice
                    </span>
                  }
                >
                  Starting XI · {formation}
                </Eyebrow>
                <Pitch d={d} />
              </div>

              {/* Bench */}
              <div>
                <Eyebrow>Bench · in substitution order</Eyebrow>
                {d.bench_order.length === 0 ? (
                  <p className="text-sm text-ink-500">No bench data.</p>
                ) : (
                  <div className="flex gap-2 overflow-x-auto pb-1 md:grid md:grid-cols-4 md:gap-3">
                    {d.bench_order.map((b, i) => (
                      <div key={b.code} className="relative w-[112px] shrink-0 pt-1.5 md:w-auto">
                        <span className="absolute -top-0 left-1.5 z-10 grid h-4 w-4 place-items-center rounded-sm bg-pitch-700 font-mono text-[9px] font-bold text-ink-300 ring-1 ring-line">
                          {i + 1}
                        </span>
                        <PitchCard player={b} />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        }}
      </DataGate>
    </div>
  );
}
