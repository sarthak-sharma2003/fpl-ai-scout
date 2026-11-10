import { useEffect, useRef } from 'react';
import type { PlayerDetail, RecentMatch } from '../types';
import { useJson } from '../lib/useJson';

const TH = 'px-1.5 py-1 text-right font-bold';
const TD = 'px-1.5 py-1 text-right tabular-nums';

function RecentTable({ recent }: { recent: RecentMatch[] }) {
  const pts = recent.reduce((s, m) => s + m.points, 0);
  const mins = recent.reduce((s, m) => s + m.minutes, 0);
  return (
    <div>
      <p className="mb-1.5 text-xs text-ink-300">
        <span className="font-semibold text-ink-100">{pts} pts</span> in the last {recent.length}{' '}
        ({(pts / recent.length).toFixed(1)} a match) · {mins} minutes
      </p>
      <table className="w-full text-xs text-ink-300">
        <thead>
          <tr className="border-b border-line font-mono text-[9px] uppercase tracking-[0.12em] text-ink-500">
            <th className="py-1 pr-1.5 text-left font-bold">GW</th>
            <th className="px-1.5 py-1 text-left font-bold">Opp</th>
            <th className={TH}>Min</th>
            <th className={TH}>Pts</th>
            <th className={TH}>G</th>
            <th className={TH}>A</th>
            <th className={TH}>xGI</th>
            <th className={TH}>CS</th>
            <th className="py-1 pl-1.5 text-right font-bold">Bon</th>
          </tr>
        </thead>
        <tbody>
          {recent.map((m) => (
            <tr key={`${m.gw}-${m.opp}`} className="border-b border-line/50">
              <td className="py-1 pr-1.5 font-mono text-ink-500">{m.gw}</td>
              <td className="px-1.5 py-1">
                {m.opp ?? '—'} <span className="text-ink-500">{m.home ? 'H' : 'A'}</span>
              </td>
              <td className={TD}>{m.minutes}</td>
              <td className={`${TD} font-bold ${m.points >= 6 ? 'text-volt-deep' : 'text-ink-100'}`}>
                {m.points}
              </td>
              <td className={TD}>{m.goals || ''}</td>
              <td className={TD}>{m.assists || ''}</td>
              <td className={TD}>{m.xgi != null ? m.xgi.toFixed(2) : '—'}</td>
              <td className={TD}>{m.cs ? '✓' : ''}</td>
              <td className="py-1 pl-1.5 text-right tabular-nums">{m.bonus || ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A player's last few matches, fetched on demand from players/{code}.json. */
export function RecentForm({ code }: { code: number }) {
  const state = useJson<PlayerDetail>(`players/${code}.json`);
  if (state.status === 'loading') return <p className="text-xs text-ink-500">Loading recent matches…</p>;
  if (state.status === 'error' || !state.data.recent?.length)
    return <p className="text-xs text-ink-500">No matches this season.</p>;
  return <RecentTable recent={state.data.recent} />;
}

/** Recent matches in a native modal — what a click on any pitch card opens. */
export function PlayerDialog({
  code,
  name,
  onClose,
}: {
  code: number;
  name: string;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && ref.current?.close()}
      className="m-auto w-[min(92vw,440px)] rounded-lg border border-line bg-pitch-850 p-0 text-ink-100 backdrop:bg-black/40"
    >
      <div className="p-4">
        <div className="mb-3 flex items-baseline justify-between gap-3">
          <h2 className="font-display text-2xl font-bold uppercase leading-none">{name}</h2>
          <button
            type="button"
            onClick={() => ref.current?.close()}
            className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-ink-500 hover:text-ink-100"
          >
            Close
          </button>
        </div>
        <RecentForm code={code} />
      </div>
    </dialog>
  );
}
