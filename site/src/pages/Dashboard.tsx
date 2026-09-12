import { useEffect, useState } from 'react';
import { useJson } from '../lib/useJson';
import type { Dashboard as DashboardData, PlayerCard, Transfers as TransfersData } from '../types';
import { PageHeader } from '../components/Layout';
import { Card, DataGate, Eyebrow, PosBadge, StateBadge, StatTile } from '../components/ui';
import PitchCard from '../components/PlayerChip';

const CHIP_NAME: Record<string, string> = {
  wildcard: 'Wildcard',
  freehit: 'Free Hit',
  bboost: 'Bench Boost',
  '3xc': 'Triple Captain',
};

/** A player's code can show up in the XI or the bench — search both. */
function findByCode(d: DashboardData, code: number | null): PlayerCard | undefined {
  if (code == null) return undefined;
  return [...d.pitch.gk, ...d.pitch.def, ...d.pitch.mid, ...d.pitch.fwd, ...d.bench_order].find(
    (p) => p.code === code,
  );
}

/** Deadline countdown, re-derived every 30s.
 *
 * Three states, because "the deadline has passed" is NOT the same thing as
 * "this page is stale". Every gameweek spends two or three days with its
 * deadline behind it while the matches are actually being played, and that
 * data is perfectly current — an earlier version of this component called that
 * stale and shouted about it on a completely up-to-date page.
 *
 * Staleness is data age, so that is what we measure: the payload carries the
 * time it was built, and only a `generated_at` older than the nightly deploy's
 * own cadence means something has actually broken. */
const STALE_AFTER_HOURS = 36;

function Countdown({ deadline, generatedAt }: { deadline: string | null; generatedAt?: string | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);
  if (!deadline) return null;
  const ms = new Date(deadline).getTime() - now;
  if (Number.isNaN(ms)) return null;

  if (ms <= 0) {
    const builtMs = generatedAt ? new Date(generatedAt).getTime() : NaN;
    const ageHours = Number.isNaN(builtMs) ? null : (now - builtMs) / 3_600_000;
    const stale = ageHours != null && ageHours > STALE_AFTER_HOURS;
    if (!stale) {
      return (
        <div className="text-right">
          <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-500">
            Deadline passed
          </p>
          <p className="font-display text-lg font-bold leading-tight text-volt">
            Gameweek in progress
          </p>
          {ageHours != null && (
            <p className="mt-1 font-mono text-[9px] uppercase tracking-[0.16em] text-ink-500">
              updated {ageHours < 1 ? 'just now' : `${Math.floor(ageHours)}h ago`}
            </p>
          )}
        </div>
      );
    }
    return (
      <div className="text-right">
        <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.16em] text-danger">
          Data is stale
        </p>
        <p className="font-display text-lg font-bold leading-tight text-danger">
          {Math.floor(ageHours / 24)} days behind
        </p>
        <p className="mt-1 font-mono text-[9px] uppercase tracking-[0.16em] text-ink-500">
          the nightly deploy has not landed
        </p>
      </div>
    );
  }

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

/** The game plan: what to actually DO this gameweek, at a glance — so the
 * pitch below (which may already reflect recommended transfers) never reads
 * as an unexplained swap to a different team. Skipped entirely when
 * transfers.json isn't available; the dashboard still works without it. */
function GamePlan({ d, t }: { d: DashboardData; t: TransfersData }) {
  const captainName = d.insight.captain ?? findByCode(d, d.captain_code)?.name;
  const viceName = findByCode(d, d.vice_captain_code)?.name;
  const moves = t.moves ?? [];
  const chip = t.chip_advice;
  const forCaptain = captainName ? `, captain ${captainName}` : '';

  const headline =
    moves.length > 0
      ? `Make ${moves.length} transfer${moves.length > 1 ? 's' : ''}${forCaptain}`
      : chip
        ? `${CHIP_NAME[chip.chip] ?? chip.chip} suggested${
            chip.ev != null ? `: +${chip.ev.toFixed(1)} EV over your current squad` : ''
          }`
        : `Hold your transfers — bank the free transfer${t.free_transfers === 1 ? '' : 's'}${forCaptain}`;

  return (
    <div>
      <Eyebrow>Game plan</Eyebrow>
      <Card className="flex flex-col gap-4 p-4 md:p-6">
        <p className="font-display text-xl font-bold leading-tight text-ink-100 md:text-2xl">{headline}</p>

        {moves.length > 0 ? (
          <div className="flex flex-col gap-1.5">
            {moves.map((m, i) => (
              <div
                key={i}
                className="flex items-center justify-between gap-2 rounded-md bg-pitch-900/40 px-3 py-2 ring-1 ring-line"
              >
                <div className="flex min-w-0 items-center gap-2">
                  <PosBadge pos={m.out.position} />
                  <span className="truncate font-mono text-xs text-ink-500 line-through decoration-ink-500/60">
                    {m.out.name}
                  </span>
                  <span aria-hidden className="shrink-0 text-ink-500">
                    →
                  </span>
                  <span className="truncate font-mono text-xs font-bold text-ink-100">{m.in.name}</span>
                </div>
                <span
                  className={`shrink-0 font-mono text-[11px] font-bold tabular-nums ${
                    m.net_ev >= 0 ? 'text-volt' : 'text-danger'
                  }`}
                >
                  {m.net_ev >= 0 ? '+' : ''}
                  {m.net_ev.toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-ink-500">No transfer beats holding — bank the free transfer this week.</p>
        )}

        <div className="grid grid-cols-2 gap-x-6 gap-y-4 border-t border-line pt-4 sm:grid-cols-4">
          <StatTile label="Captain" value={captainName ?? '—'} />
          <StatTile label="Vice" value={viceName ?? '—'} />
          <StatTile label="Bank" value={t.bank != null ? `£${(t.bank / 10).toFixed(1)}m` : '—'} />
          <StatTile label="Free transfers" value={t.free_transfers ?? '—'} />
        </div>
        {d.insight.transfer_summary && (
          <p className="-mt-2 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-500">
            {d.insight.transfer_summary}
          </p>
        )}

        {chip && (
          <p className="rounded-r-md border-l-2 border-armband bg-armband/[0.06] px-4 py-3 text-sm leading-relaxed text-ink-300">
            <span className="mr-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.22em] text-armband">
              Chip advice
            </span>
            {CHIP_NAME[chip.chip] ?? chip.chip} would gain {chip.ev != null ? `+${chip.ev.toFixed(1)} EV` : 'value'}.
            Not applied —{' '}
            {t.squad_source === 'synced'
              ? 'the squad below is your own team plus the moves above'
              : 'the squad below is a recommended draft, not applied to any real team'}
            .
          </p>
        )}

        {t.squad_source && (
          <p className="border-t border-line pt-3 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-500">
            {t.squad_source === 'synced'
              ? 'Pitch below · your real squad with the recommended moves applied'
              : 'Pitch below · a suggested draft, not your synced squad'}
          </p>
        )}
      </Card>
    </div>
  );
}

export default function Dashboard() {
  const dashMain = useJson<DashboardData>('dashboard.json');
  const dashAlt = useJson<DashboardData>('dashboard_alt.json');
  const transfers = useJson<TransfersData>('transfers.json');
  const [variant, setVariant] = useState<'main' | 'alt'>('main');
  const altReady = dashAlt.status === 'ready' ? dashAlt.data : null;
  // transfers.json is a nice-to-have enhancement, not a hard dependency — a
  // failed or slow fetch here must not blank the whole dashboard, so it's
  // read separately rather than folded into the page's DataGate.
  const t = transfers.status === 'ready' ? transfers.data : null;

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="The model's recommended squad for the gameweek — EV from the ML projections, formation and bench from the MILP optimizer."
      />
      <DataGate state={dashMain}>
        {(dMain) => {
          const d = variant === 'alt' && altReady ? altReady : dMain;
          const formation = `${d.pitch.def.length}-${d.pitch.mid.length}-${d.pitch.fwd.length}`;
          return (
            <div className="flex flex-col gap-5">
              {/* Squad variant toggle — only when an alternative build exists */}
              {altReady && (
                <div className="flex items-center gap-1.5 self-start rounded-md bg-pitch-800 p-1 ring-1 ring-line">
                  {[
                    { key: 'main' as const, label: 'Recommended' },
                    { key: 'alt' as const, label: altReady.alt_label || 'Alternative' },
                  ].map(({ key, label }) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setVariant(key)}
                      className={`rounded px-3 py-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.16em] transition-colors ${
                        variant === key
                          ? 'bg-volt/[0.14] text-volt'
                          : 'text-ink-500 hover:text-ink-300'
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}

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
                  </div>
                  <Countdown deadline={d.deadline} generatedAt={d.generated_at} />
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
                    value={t ? `${t.confidence.toFixed(0)}%` : '—'}
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

              {/* Game plan — skipped gracefully if transfers.json didn't load */}
              {t && <GamePlan d={d} t={t} />}

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
