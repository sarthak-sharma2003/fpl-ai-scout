import type { FormEvent } from 'react';
import { useMemo, useState } from 'react';
import { combine, useJson } from '../lib/useJson';
import { PROXY_URL } from '../lib/proxy';
import { bestXI, transferSuggestions } from '../lib/suggest';
import type { SquadPlayer } from '../lib/suggest';
import type { Dashboard, PlayerProjection, Position, Projections } from '../types';
import { PageHeader } from '../components/Layout';
import { Card, DataGate, Eyebrow, PosBadge, StatTile } from '../components/ui';
import PitchCard from '../components/PlayerChip';

const STORAGE_KEY = 'myteam_squad';
const BUDGET = 100.0;
const POSITIONS: Position[] = ['GKP', 'DEF', 'MID', 'FWD'];
const SQUAD_SHAPE: Record<Position, number> = { GKP: 2, DEF: 5, MID: 5, FWD: 3 };

interface SavedSquad {
  codes: number[];
  /** £m */
  bank: number;
  source: 'import' | 'manual';
  entryName?: string;
}

function loadSaved(): SavedSquad | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as SavedSquad) : null;
  } catch {
    return null;
  }
}

/** Accepts a bare team ID or a pasted fantasy.premierleague.com/entry/NNNN/... URL. */
function extractTeamId(input: string): number | null {
  const trimmed = input.trim();
  const fromUrl = trimmed.match(/entry\/(\d+)/);
  if (fromUrl) return Number(fromUrl[1]);
  return /^\d+$/.test(trimmed) ? Number(trimmed) : null;
}

function toSquadPlayer(p: PlayerProjection): SquadPlayer {
  return {
    code: p.code, name: p.name, team: p.team, position: p.position,
    price: p.price, ev: p.ev_points, flag: p.flag, pk: p.pk,
  };
}

// Only the fields this page reads from the FPL API responses.
interface FplEntry {
  name?: string;
  last_deadline_bank?: number;
}
interface FplPicksResponse {
  picks?: { element: number }[];
  entry_history?: { bank?: number };
}

function ImportForm({
  proj,
  gw,
  onImported,
}: {
  proj: Projections;
  gw: number;
  onImported: (s: SavedSquad) => void;
}) {
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function doImport(e: FormEvent) {
    e.preventDefault();
    const id = extractTeamId(input);
    if (!id) {
      setError('Enter a valid team ID or paste your FPL team URL.');
      return;
    }
    if (!PROXY_URL) {
      setError('Team import is not deployed yet — use the manual picker instead.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const entryRes = await fetch(`${PROXY_URL}/fpl/entry/${id}/`);
      if (!entryRes.ok) {
        throw new Error(entryRes.status === 404 ? 'No team with that ID.' : `FPL API error (${entryRes.status})`);
      }
      const entry = (await entryRes.json()) as FplEntry;

      // Current-GW picks only go public once the deadline passes, so a 404
      // before the deadline is normal — the team as it stands IS last GW's
      // squad, so fall back one gameweek before giving up.
      let picksRes = await fetch(`${PROXY_URL}/fpl/entry/${id}/event/${gw}/picks/`);
      if (picksRes.status === 404 && gw > 1) {
        picksRes = await fetch(`${PROXY_URL}/fpl/entry/${id}/event/${gw - 1}/picks/`);
      }
      if (!picksRes.ok) {
        throw new Error(
          picksRes.status === 404
            ? "Couldn't load this team's picks — the team may be private or it's pre-season. Use the manual picker instead."
            : `FPL API error (${picksRes.status})`,
        );
      }
      const picksData = (await picksRes.json()) as FplPicksResponse;
      const picks = picksData.picks ?? [];
      const codes = picks.map((pk) => proj.elements[pk.element]).filter((c): c is number => c != null);
      if (codes.length !== 15) {
        throw new Error("Couldn't map this squad onto current player data. Use the manual picker instead.");
      }

      // entry_history.bank is the bank as of the fetched gw; last_deadline_bank
      // is the fallback for older API shapes. Neither exists pre-season, in
      // which case bank is just 0 — close enough for a squad with no transfer
      // history yet, and better than blocking the import over it.
      const bankTenths = picksData.entry_history?.bank ?? entry.last_deadline_bank ?? 0;
      onImported({ codes, bank: bankTenths / 10, source: 'import', entryName: entry.name });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="p-4 md:p-6">
      <form onSubmit={doImport} className="flex flex-col gap-3">
        <label className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-500">
          FPL team ID or URL
        </label>
        <div className="flex flex-wrap gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="e.g. 5874404 or your fantasy.premierleague.com/entry/... link"
            className="h-9 min-w-[240px] flex-1 rounded-md border border-line bg-pitch-900/70 px-3 text-sm text-ink-100 placeholder:text-ink-500 focus:border-volt/50"
          />
          <button
            type="submit"
            disabled={busy}
            className="rounded-md bg-volt px-4 py-2 font-mono text-[11px] font-bold uppercase tracking-[0.16em] text-ink-100 disabled:opacity-40"
          >
            {busy ? 'Importing…' : 'Import team'}
          </button>
        </div>
        {error && <p className="text-sm text-danger">{error}</p>}
        {!PROXY_URL && (
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-500">
            Import needs the FPL proxy, which isn't deployed on this build yet.
          </p>
        )}
      </form>
    </Card>
  );
}

function ManualPicker({ proj, onSaved }: { proj: Projections; onSaved: (s: SavedSquad) => void }) {
  const [query, setQuery] = useState('');
  const [pos, setPos] = useState<Position | 'ALL'>('ALL');
  const [codes, setCodes] = useState<number[]>([]);

  const byCode = useMemo(() => new Map(proj.players.map((p) => [p.code, p])), [proj.players]);
  const squad = useMemo(
    () => codes.map((c) => byCode.get(c)).filter((p): p is PlayerProjection => p != null),
    [codes, byCode],
  );

  const counts: Record<Position, number> = { GKP: 0, DEF: 0, MID: 0, FWD: 0 };
  const clubCounts = new Map<string, number>();
  for (const p of squad) {
    counts[p.position]++;
    if (p.team) clubCounts.set(p.team, (clubCounts.get(p.team) ?? 0) + 1);
  }
  const spent = squad.reduce((s, p) => s + p.price, 0);
  const remaining = BUDGET - spent;
  const complete = codes.length === 15 && POSITIONS.every((p) => counts[p] === SQUAD_SHAPE[p]);

  function toggle(p: PlayerProjection) {
    if (codes.includes(p.code)) {
      setCodes(codes.filter((c) => c !== p.code));
      return;
    }
    if (counts[p.position] >= SQUAD_SHAPE[p.position]) return;
    if ((clubCounts.get(p.team ?? '') ?? 0) >= 3) return;
    if (p.price > remaining + 1e-9) return;
    setCodes([...codes, p.code]);
  }

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return proj.players
      .filter((p) => (pos === 'ALL' || p.position === pos) && (q === '' || p.name.toLowerCase().includes(q)))
      .sort((a, b) => b.ev_points - a.ev_points)
      .slice(0, 100);
  }, [proj.players, query, pos]);

  return (
    <div className="flex flex-col gap-4">
      <Card className="p-4 md:p-6">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          <StatTile label="Squad" value={`${codes.length}/15`} />
          {POSITIONS.map((p) => (
            <StatTile key={p} label={p} value={`${counts[p]}/${SQUAD_SHAPE[p]}`} />
          ))}
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
          <StatTile label="Budget remaining" value={`£${remaining.toFixed(1)}m`} />
          <button
            type="button"
            disabled={!complete}
            onClick={() => onSaved({ codes, bank: remaining, source: 'manual' })}
            className="rounded-md bg-volt px-4 py-2 font-mono text-[11px] font-bold uppercase tracking-[0.16em] text-ink-100 disabled:opacity-40"
          >
            Save squad
          </button>
        </div>
        {squad.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-1.5 border-t border-line pt-4">
            {squad.map((p) => (
              <button
                key={p.code}
                type="button"
                onClick={() => toggle(p)}
                className="flex items-center gap-1 rounded-md bg-pitch-900/60 px-2 py-1 text-xs text-ink-100 ring-1 ring-line hover:ring-danger/50"
                title="Remove"
              >
                <PosBadge pos={p.position} />
                {p.name}
                <span aria-hidden className="text-ink-500">
                  ×
                </span>
              </button>
            ))}
          </div>
        )}
      </Card>

      <div className="flex flex-wrap items-center gap-2.5">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search name…"
          className="h-8 w-44 rounded-md border border-line bg-pitch-900/70 px-2.5 text-sm text-ink-100 placeholder:text-ink-500 focus:border-volt/50"
        />
        <div className="flex overflow-hidden rounded-md border border-line">
          {(['ALL', ...POSITIONS] as const).map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setPos(p)}
              className={`px-2.5 py-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.1em] transition-colors ${
                pos === p ? 'bg-volt text-ink-100' : 'bg-pitch-900/60 text-ink-500 hover:text-ink-100'
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      <Card className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-sm">
          <thead>
            <tr className="border-b border-line text-left font-mono text-[9px] uppercase tracking-[0.16em] text-ink-500">
              <th className="py-2.5 pl-3 pr-2 font-bold md:pl-4">Player</th>
              <th className="px-2 py-2.5 text-right font-bold">£</th>
              <th className="px-2 py-2.5 text-right font-bold">EV</th>
              <th className="py-2.5 pl-2 pr-3" />
            </tr>
          </thead>
          <tbody>
            {filtered.map((p) => {
              const owned = codes.includes(p.code);
              const blocked =
                !owned &&
                (counts[p.position] >= SQUAD_SHAPE[p.position] ||
                  (clubCounts.get(p.team ?? '') ?? 0) >= 3 ||
                  p.price > remaining + 1e-9);
              return (
                <tr key={p.code} className="border-b border-line/50">
                  <td className="py-2 pl-3 pr-2 md:pl-4">
                    <div className="flex items-center gap-1.5">
                      <PosBadge pos={p.position} />
                      <span className="font-semibold text-ink-100">{p.name}</span>
                      <span className="font-mono text-[9px] uppercase text-ink-500">{p.team ?? '—'}</span>
                    </div>
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-xs text-ink-300 tabular-nums">
                    {p.price.toFixed(1)}
                  </td>
                  <td className="px-2 py-2 text-right text-[13px] font-bold text-volt tabular-nums">
                    {p.ev_points.toFixed(2)}
                  </td>
                  <td className="py-2 pl-2 pr-3 text-right">
                    <button
                      type="button"
                      onClick={() => toggle(p)}
                      disabled={blocked}
                      className={`rounded-sm px-2 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.1em] ${
                        owned ? 'bg-danger/15 text-danger' : 'bg-volt/15 text-volt disabled:opacity-30'
                      }`}
                    >
                      {owned ? 'Remove' : 'Add'}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function Results({
  saved,
  proj,
  onReset,
}: {
  saved: SavedSquad;
  proj: Projections;
  onReset: () => void;
}) {
  const byCode = useMemo(() => new Map(proj.players.map((p) => [p.code, p])), [proj.players]);
  const squad: SquadPlayer[] = useMemo(
    () =>
      saved.codes
        .map((c) => byCode.get(c))
        .filter((p): p is PlayerProjection => p != null)
        .map(toSquadPlayer),
    [saved.codes, byCode],
  );

  const { xi, bench, formation, captain, vice } = useMemo(() => bestXI(squad), [squad]);
  const transfers = useMemo(
    () => transferSuggestions(squad, saved.bank, proj.players),
    [squad, saved.bank, proj.players],
  );

  const rows: SquadPlayer[][] = POSITIONS.map((p) => xi.filter((x) => x.position === p));
  const badge = (p: SquadPlayer): 'C' | 'V' | undefined =>
    p.code === captain?.code ? 'C' : p.code === vice?.code ? 'V' : undefined;

  return (
    <div className="flex flex-col gap-5">
      <Card className="flex flex-wrap items-center justify-between gap-3 p-4 md:p-6">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-500">
            {saved.source === 'import' ? (saved.entryName ?? 'Imported team') : 'Your manual squad'}
          </p>
          <p className="font-display text-xl font-bold text-ink-100">£{saved.bank.toFixed(1)}m in the bank</p>
        </div>
        <button
          type="button"
          onClick={onReset}
          className="rounded-md border border-line px-3 py-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-ink-300 transition-colors hover:border-volt/40 hover:text-volt"
        >
          Start over
        </button>
      </Card>

      <div>
        <Eyebrow
          action={
            <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-500">
              C captain · V vice
            </span>
          }
        >
          Best XI · {formation}
        </Eyebrow>
        {xi.length === 0 ? (
          <p className="text-sm text-ink-500">Not enough players to field a legal XI yet.</p>
        ) : (
          <div
            className="relative overflow-hidden rounded-lg ring-1 ring-line"
            style={{ background: 'linear-gradient(180deg, #132a1c 0%, #0b1d13 100%)' }}
          >
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
        )}
      </div>

      <div>
        <Eyebrow>Bench · in substitution order</Eyebrow>
        {bench.length === 0 ? (
          <p className="text-sm text-ink-500">No bench data.</p>
        ) : (
          <div className="flex gap-2 overflow-x-auto pb-1 md:grid md:grid-cols-4 md:gap-3">
            {bench.map((b, i) => (
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

      <div>
        <Eyebrow>Transfer suggestions</Eyebrow>
        {transfers.length === 0 ? (
          <p className="text-sm text-ink-500">No single swap beats holding your squad.</p>
        ) : (
          <Card className="flex flex-col gap-1.5 p-3 md:p-4">
            {transfers.map((t, i) => (
              <div
                key={i}
                className="flex items-center justify-between gap-2 rounded-md bg-pitch-900/40 px-3 py-2 ring-1 ring-line"
              >
                <div className="flex min-w-0 items-center gap-2">
                  <PosBadge pos={t.out.position} />
                  <span className="truncate font-mono text-xs text-ink-500 line-through decoration-ink-500/60">
                    {t.out.name}
                  </span>
                  <span aria-hidden className="shrink-0 text-ink-500">
                    →
                  </span>
                  <span className="truncate font-mono text-xs font-bold text-ink-100">{t.in.name}</span>
                </div>
                <span className="shrink-0 font-mono text-[11px] font-bold tabular-nums text-volt">
                  +{t.gain.toFixed(2)}
                </span>
              </div>
            ))}
          </Card>
        )}
      </div>
    </div>
  );
}

export default function MyTeam() {
  const proj = useJson<Projections>('projections.json');
  const dash = useJson<Dashboard>('dashboard.json');
  const [saved, setSaved] = useState<SavedSquad | null>(() => loadSaved());
  const [mode, setMode] = useState<'import' | 'manual'>('import');

  function save(s: SavedSquad) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
    setSaved(s);
  }
  function reset() {
    localStorage.removeItem(STORAGE_KEY);
    setSaved(null);
  }

  return (
    <div>
      <PageHeader
        title="My Team"
        subtitle="Import your FPL team by ID, or build one by hand — see your best XI, captain pick, and ranked transfer suggestions."
      />
      <DataGate state={combine(proj, dash)}>
        {([p, d]) =>
          saved ? (
            <Results saved={saved} proj={p} onReset={reset} />
          ) : (
            <div className="flex flex-col gap-4">
              <div className="flex items-center gap-1.5 self-start rounded-md bg-pitch-800 p-1 ring-1 ring-line">
                {(
                  [
                    { key: 'import' as const, label: 'Import by ID' },
                    { key: 'manual' as const, label: 'Pick manually' },
                  ]
                ).map(({ key, label }) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setMode(key)}
                    className={`rounded px-3 py-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.16em] transition-colors ${
                      mode === key ? 'bg-volt/[0.14] text-volt' : 'text-ink-500 hover:text-ink-300'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {mode === 'import' ? (
                <ImportForm proj={p} gw={d.gw} onImported={save} />
              ) : (
                <ManualPicker proj={p} onSaved={save} />
              )}
            </div>
          )
        }
      </DataGate>
    </div>
  );
}
