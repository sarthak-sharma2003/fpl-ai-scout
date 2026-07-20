import { useMemo, useState } from 'react';
import { useJson } from '../lib/useJson';
import type { PlayerProjection, Position, Projections } from '../types';
import { PageHeader } from '../components/Layout';
import { Card, DataGate, FlagMark, PkMark, PosBadge } from '../components/ui';

const PAGE = 50;

const SORTS = [
  { key: 'ev', label: 'EV' },
  { key: 'price', label: 'Price' },
  { key: 'value', label: 'Value' },
  { key: 'ceiling', label: 'Ceiling' },
  { key: 'floor', label: 'Floor' },
  { key: 'minutes', label: 'Minutes' },
] as const;
type SortKey = (typeof SORTS)[number]['key'];

function sortVal(p: PlayerProjection, k: SortKey): number {
  switch (k) {
    case 'ev':
      return p.ev_points;
    case 'price':
      return p.price;
    case 'value':
      return p.price > 0 ? p.ev_points / p.price : 0;
    case 'ceiling':
      return p.q90_points ?? -1;
    case 'floor':
      return p.q10_points ?? -1;
    case 'minutes':
      return p.ev_minutes ?? -1;
  }
}

const POSITIONS: (Position | 'ALL')[] = ['ALL', 'GKP', 'DEF', 'MID', 'FWD'];

const pct = (v: number | null | undefined) => (v != null ? `${Math.round(v * 100)}%` : '—');

/** q10→q90 band on a scale shared by every visible row; volt tick = EV. */
function QuantileBand({
  q10,
  q90,
  ev,
  max,
}: {
  q10: number | null;
  q90: number | null;
  ev: number;
  max: number;
}) {
  const at = (v: number) => `${Math.max(0, Math.min(100, (v / max) * 100))}%`;
  return (
    <div className="relative h-5 w-full min-w-[96px]">
      <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-white/10" />
      {q10 != null && q90 != null && (
        <div
          className="absolute top-1/2 h-[5px] -translate-y-1/2 rounded-full bg-ink-500/40"
          style={{ left: at(q10), width: `calc(${at(q90)} - ${at(q10)})` }}
        />
      )}
      <div
        className="absolute top-1/2 h-3 w-[3px] -translate-y-1/2 -translate-x-1/2 rounded-sm bg-volt"
        style={{ left: at(ev) }}
      />
    </div>
  );
}

function DetailRow({ p }: { p: PlayerProjection }) {
  const stats: [string, string][] = [
    ['EV points', p.ev_points.toFixed(2)],
    ['Floor · q10', p.q10_points != null ? p.q10_points.toFixed(2) : '—'],
    ['Ceiling · q90', p.q90_points != null ? p.q90_points.toFixed(2) : '—'],
    ['Value · EV/£', p.price > 0 ? (p.ev_points / p.price).toFixed(2) : '—'],
    ['EV minutes', p.ev_minutes != null ? p.ev_minutes.toFixed(0) : '—'],
    ['P(appearance)', pct(p.p_appearance)],
    ['P(60+ min)', pct(p.p_60_plus)],
    ['P(clean sheet)', pct(p.p_clean_sheet)],
  ];
  return (
    <div className="px-3 py-3 md:px-4">
      <div className="grid grid-cols-2 gap-x-6 gap-y-2.5 sm:grid-cols-4">
        {stats.map(([label, value]) => (
          <div key={label}>
            <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-ink-500">{label}</p>
            <p className="text-sm font-semibold tabular-nums text-ink-100">{value}</p>
          </div>
        ))}
      </div>
      {p.flag?.news && (
        <p className="mt-2.5 text-xs text-armband/90">
          <span className="mr-1.5 font-mono text-[9px] font-bold uppercase tracking-[0.16em]">
            flag
          </span>
          {p.flag.news}
        </p>
      )}
      <p className="mt-2.5 font-mono text-[9px] uppercase tracking-[0.16em] text-ink-500">
        model {p.model_version}
      </p>
    </div>
  );
}

function Explorer({ proj }: { proj: Projections }) {
  const [query, setQuery] = useState('');
  const [pos, setPos] = useState<Position | 'ALL'>('ALL');
  const [team, setTeam] = useState('ALL');
  const [sort, setSort] = useState<SortKey>('ev');
  const [shown, setShown] = useState(PAGE);
  const [open, setOpen] = useState<number | null>(null);

  const teams = useMemo(
    () =>
      Array.from(new Set(proj.players.map((p) => p.team).filter((t): t is string => t != null))).sort(),
    [proj.players],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return proj.players
      .filter(
        (p) =>
          (pos === 'ALL' || p.position === pos) &&
          (team === 'ALL' || p.team === team) &&
          (q === '' || p.name.toLowerCase().includes(q)),
      )
      .sort((a, b) => sortVal(b, sort) - sortVal(a, sort));
  }, [proj.players, query, pos, team, sort]);

  // shared band scale across the whole visible (filtered) set
  const bandMax = useMemo(
    () => Math.max(1, ...filtered.map((p) => p.q90_points ?? p.ev_points)),
    [filtered],
  );

  const reset = () => {
    setShown(PAGE);
    setOpen(null);
  };
  const visible = filtered.slice(0, shown);

  return (
    <div className="flex flex-col gap-4">
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-2.5">
        <input
          type="search"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            reset();
          }}
          placeholder="Search name…"
          className="h-8 w-44 rounded-md border border-line bg-pitch-900/70 px-2.5 text-sm text-ink-100 placeholder:text-ink-500 focus:border-volt/50"
        />
        <div className="flex overflow-hidden rounded-md border border-line">
          {POSITIONS.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => {
                setPos(p);
                reset();
              }}
              className={`px-2.5 py-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.1em] transition-colors ${
                pos === p
                  ? 'bg-volt text-pitch-950'
                  : 'bg-pitch-900/60 text-ink-500 hover:text-ink-100'
              }`}
            >
              {p}
            </button>
          ))}
        </div>
        <select
          value={team}
          onChange={(e) => {
            setTeam(e.target.value);
            reset();
          }}
          aria-label="Filter by team"
          className="h-8 rounded-md border border-line bg-pitch-900/70 px-2 font-mono text-[11px] uppercase text-ink-300"
        >
          <option value="ALL">All teams</option>
          {teams.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[9px] uppercase tracking-[0.16em] text-ink-500">Sort</span>
          <div className="flex overflow-hidden rounded-md border border-line">
            {SORTS.map(({ key, label }) => (
              <button
                key={key}
                type="button"
                onClick={() => {
                  setSort(key);
                  reset();
                }}
                className={`px-2 py-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.08em] transition-colors ${
                  sort === key
                    ? 'bg-pitch-700 text-volt'
                    : 'bg-pitch-900/60 text-ink-500 hover:text-ink-100'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <span className="ml-auto font-mono text-[10px] uppercase tracking-[0.14em] text-ink-500">
          {filtered.length} / {proj.players.length} players
        </span>
      </div>

      {/* Table */}
      <Card className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead>
            <tr className="border-b border-line text-left font-mono text-[9px] uppercase tracking-[0.16em] text-ink-500">
              <th className="py-2.5 pl-3 pr-2 font-bold md:pl-4">#</th>
              <th className="px-2 py-2.5 font-bold">Player</th>
              <th className="px-2 py-2.5 text-right font-bold">£</th>
              <th className="px-2 py-2.5 text-right font-bold">EV</th>
              <th className="w-[30%] px-3 py-2.5 font-bold">
                Floor → ceiling <span className="normal-case">(q10–q90, shared scale)</span>
              </th>
              <th className="hidden px-2 py-2.5 text-right font-bold md:table-cell">60+</th>
              <th className="hidden px-2 py-2.5 text-right font-bold md:table-cell">CS</th>
              <th className="py-2.5 pl-2 pr-3" />
            </tr>
          </thead>
          <tbody>
            {visible.map((p, i) => (
              <PlayerRows
                key={p.code}
                p={p}
                rank={i + 1}
                bandMax={bandMax}
                open={open === p.code}
                onToggle={() => setOpen(open === p.code ? null : p.code)}
              />
            ))}
          </tbody>
        </table>
      </Card>

      {shown < filtered.length && (
        <button
          type="button"
          onClick={() => setShown(shown + PAGE)}
          className="mx-auto rounded-md border border-line bg-pitch-900/60 px-4 py-2 font-mono text-[11px] font-bold uppercase tracking-[0.14em] text-ink-300 transition-colors hover:border-volt/40 hover:text-volt"
        >
          Show {Math.min(PAGE, filtered.length - shown)} more · {filtered.length - shown} left
        </button>
      )}
    </div>
  );
}

function PlayerRows({
  p,
  rank,
  bandMax,
  open,
  onToggle,
}: {
  p: PlayerProjection;
  rank: number;
  bandMax: number;
  open: boolean;
  onToggle: () => void;
}) {
  const defensive = p.position === 'GKP' || p.position === 'DEF';
  return (
    <>
      <tr
        onClick={onToggle}
        className={`cursor-pointer border-b border-line/50 transition-colors hover:bg-white/[0.025] ${
          open ? 'bg-white/[0.03]' : ''
        }`}
      >
        <td className="py-2 pl-3 pr-2 font-mono text-[10px] text-ink-500 tabular-nums md:pl-4">
          {rank}
        </td>
        <td className="px-2 py-2">
          <div className="flex items-center gap-1.5">
            <span className="font-semibold text-ink-100">{p.name}</span>
            {p.flag && <FlagMark flag={p.flag} />}
            {p.pk && <PkMark />}
          </div>
          <div className="mt-0.5 flex items-center gap-1.5">
            <PosBadge pos={p.position} />
            <span className="font-mono text-[9px] uppercase text-ink-500">{p.team ?? '—'}</span>
          </div>
        </td>
        <td className="px-2 py-2 text-right font-mono text-xs text-ink-300 tabular-nums">
          {p.price.toFixed(1)}
        </td>
        <td className="px-2 py-2 text-right text-[15px] font-bold text-volt tabular-nums">
          {p.ev_points.toFixed(2)}
        </td>
        <td className="px-3 py-2">
          <QuantileBand q10={p.q10_points} q90={p.q90_points} ev={p.ev_points} max={bandMax} />
        </td>
        <td className="hidden px-2 py-2 text-right font-mono text-xs text-ink-300 tabular-nums md:table-cell">
          {pct(p.p_60_plus)}
        </td>
        <td className="hidden px-2 py-2 text-right font-mono text-xs tabular-nums md:table-cell">
          {defensive ? (
            <span className="text-ink-300">{pct(p.p_clean_sheet)}</span>
          ) : (
            <span className="text-ink-500/50">—</span>
          )}
        </td>
        <td className="py-2 pl-2 pr-3 text-right">
          <span
            aria-hidden
            className={`inline-block text-[10px] text-ink-500 transition-transform ${open ? 'rotate-90' : ''}`}
          >
            ▸
          </span>
        </td>
      </tr>
      {open && (
        <tr className="border-b border-line/50 bg-pitch-900/50">
          <td colSpan={8}>
            <DetailRow p={p} />
          </td>
        </tr>
      )}
    </>
  );
}

export default function Players() {
  const state = useJson<Projections>('projections.json');
  return (
    <div>
      <PageHeader
        title="Players"
        subtitle="Every projection the model published for the gameweek — expected points with q10–q90 uncertainty bands, minutes, and per-outcome probabilities."
        right={
          state.status === 'ready' ? (
            <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-500">
              {state.data.season} · GW{state.data.gw}
            </span>
          ) : undefined
        }
      />
      <DataGate state={state}>{(proj) => <Explorer proj={proj} />}</DataGate>
    </div>
  );
}
