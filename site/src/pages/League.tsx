import { useState } from 'react';
import { useJson } from '../lib/useJson';
import type { LeagueEntry, LeagueResponse, OwnershipRow, Position, SquadPlayer } from '../types';
import { PageHeader } from '../components/Layout';
import { Card, ChalkState, DataGate, Eyebrow, PosBadge, Roundel } from '../components/ui';

const POS_TEXT: Record<Position, string> = {
  GKP: 'text-gkp',
  DEF: 'text-def',
  MID: 'text-mid',
  FWD: 'text-fwd',
};

const CHIP_ABBR: Record<string, string> = {
  wildcard: 'WC',
  freehit: 'FH',
  bboost: 'BB',
  '3xc': 'TC',
  triple_captain: 'TC',
  bench_boost: 'BB',
};

const N_RIVALS = 7;

function Movement({ rank, last }: { rank: number; last: number }) {
  if (rank < last) return <span className="font-mono text-[10px] text-volt">▲{last - rank}</span>;
  if (rank > last) return <span className="font-mono text-[10px] text-danger">▼{rank - last}</span>;
  return <span className="font-mono text-[10px] text-ink-500">—</span>;
}

function SquadGroup({ pos, players }: { pos: Position; players: SquadPlayer[] }) {
  if (players.length === 0) return null;
  const sorted = [...players].sort((a, b) => b.multiplier - a.multiplier || (b.ev ?? 0) - (a.ev ?? 0));
  return (
    <div>
      <p className={`mb-1.5 font-mono text-[9px] font-bold uppercase tracking-[0.2em] ${POS_TEXT[pos]}`}>
        {pos}
      </p>
      <ul className="flex flex-col gap-1">
        {sorted.map((p) => (
          <li
            key={p.code}
            className={`flex items-center justify-between gap-2 text-xs ${
              p.multiplier === 0 ? 'opacity-50' : ''
            }`}
          >
            <span className="flex min-w-0 items-center gap-1.5">
              <span className="truncate font-medium text-ink-100">{p.name}</span>
              {p.is_captain && <Roundel kind="C" />}
              {p.multiplier === 0 && (
                <span className="font-mono text-[8px] uppercase text-ink-500">bench</span>
              )}
            </span>
            <span className="font-mono text-[11px] text-ink-300 tabular-nums">
              {p.ev != null ? p.ev.toFixed(1) : '—'}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function StandingsTable({ data }: { data: LeagueResponse }) {
  const [open, setOpen] = useState<number | null>(null);
  const standings = data.standings ?? [];
  const evMax = Math.max(1, ...standings.map((e) => e.projected_next_ev ?? 0));

  const row = (e: LeagueEntry) => {
    const isOpen = open === e.entry_id;
    return (
      <tbody key={e.entry_id} className="group">
        <tr
          onClick={() => setOpen(isOpen ? null : e.entry_id)}
          className={`cursor-pointer border-b border-line/50 transition-colors hover:bg-white/[0.02] ${
            e.is_us ? 'bg-volt/[0.05]' : ''
          }`}
        >
          <td className={`py-2.5 pl-3 pr-2 md:pl-4 ${e.is_us ? 'border-l-2 border-volt' : 'border-l-2 border-transparent'}`}>
            <div className="flex items-center gap-1.5">
              <span className="font-display text-lg font-bold leading-none text-ink-100 tabular-nums">
                {e.rank}
              </span>
              <Movement rank={e.rank} last={e.last_rank} />
            </div>
          </td>
          <td className="min-w-[150px] px-2 py-2.5">
            <p className="flex items-center gap-1.5 font-semibold leading-tight text-ink-100">
              {e.entry_name}
              {e.is_us && (
                <span className="rounded-sm bg-volt/15 px-1 py-px font-mono text-[8px] font-bold uppercase tracking-[0.12em] text-volt">
                  us
                </span>
              )}
            </p>
            <p className="font-mono text-[9px] uppercase tracking-wide text-ink-500">
              {e.player_name}
            </p>
          </td>
          <td className="px-2 py-2.5 text-right font-display text-lg font-bold text-ink-100 tabular-nums">
            {e.total}
          </td>
          <td className="px-2 py-2.5 text-right font-mono text-xs text-ink-300 tabular-nums">
            {e.event_total}
          </td>
          <td className="hidden px-2 py-2.5 text-right font-mono text-xs text-ink-300 tabular-nums sm:table-cell">
            {e.team_value != null ? e.team_value.toFixed(1) : '—'}
            <span className="text-ink-500"> / {e.bank != null ? e.bank.toFixed(1) : '—'}</span>
          </td>
          <td className="hidden px-2 py-2.5 md:table-cell">
            <div className="flex flex-wrap gap-1">
              {e.chips_used.length === 0 ? (
                <span className="font-mono text-[10px] text-ink-500">—</span>
              ) : (
                e.chips_used.map((c, i) => (
                  <span
                    key={i}
                    className="rounded-sm bg-white/5 px-1.5 py-px font-mono text-[9px] font-bold uppercase text-ink-300 ring-1 ring-line"
                  >
                    {CHIP_ABBR[c.chip] ?? c.chip}
                    {c.gw}
                  </span>
                ))
              )}
            </div>
          </td>
          <td className="px-2 py-2.5">
            <div className="flex items-center gap-2">
              <div className="h-[5px] w-20 rounded-full bg-white/[0.06] md:w-28">
                <div
                  className={`h-full rounded-full ${e.is_us ? 'bg-volt' : 'bg-ink-500/60'}`}
                  style={{ width: `${((e.projected_next_ev ?? 0) / evMax) * 100}%` }}
                />
              </div>
              <span className="font-mono text-[11px] font-bold text-ink-100 tabular-nums">
                {e.projected_next_ev != null ? e.projected_next_ev.toFixed(1) : '—'}
              </span>
            </div>
          </td>
          <td className="py-2.5 pl-1 pr-3 text-right">
            <span
              aria-hidden
              className={`inline-block text-[10px] text-ink-500 transition-transform ${isOpen ? 'rotate-90' : ''}`}
            >
              ▸
            </span>
          </td>
        </tr>
        {isOpen && (
          <tr className="border-b border-line/50 bg-pitch-900/50">
            <td colSpan={8} className="px-4 py-4">
              <div className="grid grid-cols-2 gap-x-6 gap-y-4 lg:grid-cols-4">
                {(['GKP', 'DEF', 'MID', 'FWD'] as Position[]).map((pos) => (
                  <SquadGroup
                    key={pos}
                    pos={pos}
                    players={e.squad.filter((p) => p.position === pos)}
                  />
                ))}
              </div>
              {e.captain && (
                <p className="mt-3 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-500">
                  Armband on {e.captain.name} · squad EV {e.projected_next_ev?.toFixed(1) ?? '—'}
                </p>
              )}
            </td>
          </tr>
        )}
      </tbody>
    );
  };

  return (
    <div>
      <Card className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead>
            <tr className="border-b border-line text-left font-mono text-[9px] uppercase tracking-[0.16em] text-ink-500">
              <th className="py-2.5 pl-3 pr-2 font-bold md:pl-4">Rk</th>
              <th className="px-2 py-2.5 font-bold">Entry</th>
              <th className="px-2 py-2.5 text-right font-bold">Total</th>
              <th className="px-2 py-2.5 text-right font-bold">GW</th>
              <th className="hidden px-2 py-2.5 text-right font-bold sm:table-cell">TV / bank</th>
              <th className="hidden px-2 py-2.5 font-bold md:table-cell">Chips</th>
              <th className="px-2 py-2.5 font-bold">Next-GW EV *</th>
              <th className="py-2.5 pl-1 pr-3" />
            </tr>
          </thead>
          {standings.map(row)}
        </table>
      </Card>
      <p className="mt-2 font-mono text-[9px] uppercase tracking-[0.14em] text-ink-500">
        * approximation: their last synced squad (GW{data.picks_gw ?? '?'}) scored by our model's
        GW{data.projection_gw ?? '?'} EV
      </p>
    </div>
  );
}

function Dots({ n, max }: { n: number; max: number }) {
  return (
    <span className="flex items-center gap-[3px]">
      {Array.from({ length: max }, (_, i) => (
        <span
          key={i}
          className={`h-1.5 w-1.5 rounded-full ${i < n ? 'bg-volt' : 'bg-white/10'}`}
        />
      ))}
    </span>
  );
}

function Coverage({ rows }: { rows: OwnershipRow[] }) {
  const sorted = [...rows].sort((a, b) => b.n_owned - a.n_owned || (b.ev ?? 0) - (a.ev ?? 0));
  return (
    <Card className="flex min-h-0 flex-col p-4">
      <Eyebrow>Coverage — who the rivals hold</Eyebrow>
      <p className="mb-3 text-xs leading-relaxed text-ink-500">
        Every player in a rival squad, by how many of the {N_RIVALS} rivals own them. The armband
        count says who needs covering.
      </p>
      <ul className="flex max-h-[430px] flex-col gap-1 overflow-y-auto pr-1">
        {sorted.map((r) => (
          <li key={r.code} className="flex items-center justify-between gap-2 rounded-sm px-1 py-1 text-xs">
            <span className="flex min-w-0 items-center gap-1.5">
              <PosBadge pos={r.position} />
              <span className="truncate font-medium text-ink-100">{r.name}</span>
              <span className="font-mono text-[9px] uppercase text-ink-500">{r.team}</span>
              {r.we_own && (
                <span title="In our squad" className="font-mono text-[9px] font-bold text-volt">
                  ✓ ours
                </span>
              )}
            </span>
            <span className="flex shrink-0 items-center gap-2">
              {r.n_captained > 0 && (
                <span
                  title={`Captained by ${r.n_captained} rival${r.n_captained > 1 ? 's' : ''}`}
                  className="rounded-sm bg-armband/10 px-1 py-px font-mono text-[9px] font-bold text-armband"
                >
                  C×{r.n_captained}
                </span>
              )}
              <Dots n={r.n_owned} max={N_RIVALS} />
              <span className="w-6 text-right font-mono text-[10px] text-ink-300 tabular-nums">
                {r.n_owned}/{N_RIVALS}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Differentials({ data }: { data: NonNullable<LeagueResponse['differentials']> }) {
  const edges = [...data.our_edges].sort((a, b) => (b.ev ?? 0) - (a.ev ?? 0));
  const threats = [...data.threats].sort((a, b) => (b.ev ?? 0) - (a.ev ?? 0));
  return (
    <Card className="p-4">
      <Eyebrow>Differentials</Eyebrow>
      <div className="grid gap-5 sm:grid-cols-2">
        <div>
          <p className="mb-2 font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-volt">
            Our edges · ≤1 rival owns
          </p>
          <ul className="flex flex-col gap-1.5">
            {edges.map((p) => (
              <li
                key={p.code}
                className="flex items-center justify-between gap-2 rounded-md bg-volt/[0.04] px-2.5 py-1.5 ring-1 ring-volt/15"
              >
                <span className="flex min-w-0 items-center gap-1.5 text-xs">
                  <PosBadge pos={p.position} />
                  <span className="truncate font-medium text-ink-100">{p.name}</span>
                  <span className="font-mono text-[9px] uppercase text-ink-500">{p.team}</span>
                </span>
                <span className="flex shrink-0 items-baseline gap-2">
                  <span className="font-display text-base font-bold leading-none text-volt tabular-nums">
                    {p.ev != null ? p.ev.toFixed(2) : '—'}
                  </span>
                  <span className="font-mono text-[9px] text-ink-500">×{p.n_owned}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="mb-2 font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-danger">
            Threats · ≥2 rivals own, we don't
          </p>
          <ul className="flex flex-col gap-1.5">
            {threats.map((p) => (
              <li
                key={p.code}
                className="flex items-center justify-between gap-2 rounded-md bg-danger/[0.04] px-2.5 py-1.5 ring-1 ring-danger/15"
              >
                <span className="flex min-w-0 items-center gap-1.5 text-xs">
                  <PosBadge pos={p.position} />
                  <span className="truncate font-medium text-ink-100">{p.name}</span>
                  <span className="font-mono text-[9px] uppercase text-ink-500">{p.team}</span>
                </span>
                <span className="flex shrink-0 items-baseline gap-2">
                  <span className="font-display text-base font-bold leading-none text-ink-100 tabular-nums">
                    {p.ev != null ? p.ev.toFixed(2) : '—'}
                  </span>
                  <span className="font-mono text-[9px] text-danger">×{p.n_owned} rivals</span>
                  {p.n_captained > 0 && (
                    <span className="font-mono text-[9px] font-bold text-armband">C×{p.n_captained}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <p className="mt-3 border-t border-line/60 pt-2.5 text-xs text-ink-500">
        Playbook: in an 8-manager league raw points beat differentials — this intel is for captaincy
        cover and blocking buys, not for avoiding good players.
      </p>
    </Card>
  );
}

export default function League() {
  const state = useJson<LeagueResponse>('league.json');

  return (
    <div>
      <PageHeader
        title="League"
        subtitle="The 8-manager battleground — synced standings, every rival squad scored by our model, and where the coverage gaps are."
      />
      <DataGate state={state}>
        {(l) =>
          l.configured ? (
            <div className="flex flex-col gap-5">
              {l.league && (
                <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                  <span className="font-display text-2xl font-bold uppercase leading-none text-ink-100">
                    {l.league.name}
                  </span>
                  <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-500">
                    synced {new Date(l.league.fetched_at).toLocaleString([], {
                      day: 'numeric',
                      month: 'short',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </span>
                </div>
              )}
              <StandingsTable data={l} />
              <div className="grid gap-5 lg:grid-cols-2">
                {l.ownership && <Coverage rows={l.ownership} />}
                {l.differentials && <Differentials data={l.differentials} />}
              </div>
            </div>
          ) : (
            <ChalkState title="No mini-league synced">
              <p>{l.note}</p>
              <p className="mt-3">Once synced, this page becomes the war room:</p>
              <ul className="mt-1.5 list-inside list-disc space-y-1">
                <li>standings with movement, chips burned, and every rival squad expandable</li>
                <li>each rival's next-GW EV under our model — the honest gap to the field</li>
                <li>coverage: who the 7 rivals hold and captain, player by player</li>
                <li>differentials: our edges vs the threats we don't own</li>
              </ul>
            </ChalkState>
          )
        }
      </DataGate>
    </div>
  );
}
