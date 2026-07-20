import { useJson } from '../lib/useJson';
import type { FixtureTick, FixturesResponse } from '../types';
import { PageHeader } from '../components/Layout';
import { Card, DataGate, StateBadge } from '../components/ui';

/** 5-step heat ramp tuned for the dark theme: green = comfortable, neutral =
 * par, orange/rose = hostile. */
const FDR_CLS: Record<number, string> = {
  1: 'bg-[#1c5735] text-[#a9f5cb]',
  2: 'bg-[#14402a] text-[#7fd9a7]',
  3: 'bg-white/[0.05] text-ink-300',
  4: 'bg-[#4d2c12] text-[#f9bd7f]',
  5: 'bg-[#571523] text-[#fdaebc]',
};
const FDR_LABEL: Record<number, string> = {
  1: 'Comfortable',
  2: 'Favourable',
  3: 'Par',
  4: 'Hard',
  5: 'Brutal',
};

function OppCell({ ticks }: { ticks: FixtureTick[] }) {
  if (ticks.length === 0 || ticks.every((t) => t.is_bgw)) {
    return (
      <div className="grid h-11 place-items-center rounded-[4px] bg-pitch-950/70 ring-1 ring-line/40">
        <span className="font-mono text-[8px] uppercase tracking-[0.18em] text-ink-500/70">
          Blank
        </span>
      </div>
    );
  }
  const isDgw = ticks.length > 1 || ticks.some((t) => t.is_dgw);
  return (
    <div className="relative flex h-11 flex-col gap-px">
      {isDgw && (
        <span className="absolute -right-1 -top-1 z-10 grid h-3.5 w-3.5 place-items-center rounded-sm bg-volt font-mono text-[8px] font-bold text-pitch-950">
          2
        </span>
      )}
      {ticks.map((t, i) => (
        <div
          key={i}
          className={`flex flex-1 items-center justify-center gap-1 rounded-[4px] ${FDR_CLS[t.fdr ?? 3] ?? FDR_CLS[3]}`}
          title={`${t.opponent ?? '?'} (${t.was_home ? 'home' : 'away'}) · FDR ${t.fdr ?? '—'}`}
        >
          <span className="font-mono text-[10px] font-bold tracking-wide">{t.opponent ?? '?'}</span>
          <span className="font-mono text-[8px] opacity-60">{t.was_home ? 'H' : 'A'}</span>
        </div>
      ))}
    </div>
  );
}

export default function Fixtures() {
  const state = useJson<FixturesResponse>('fixtures.json');

  return (
    <div>
      <PageHeader
        title="Fixtures"
        subtitle="Fixture difficulty for every team across the ticker horizon, straight from the released calendar — the raw material behind the 8-GW decayed EV."
      />
      <DataGate state={state}>
        {(f) => {
          // group each team's ticker by GW so DGWs (two ticks, one GW) stack
          const gws = Array.from(new Set(f.teams.flatMap((t) => t.ticker.map((tk) => tk.gw)))).sort(
            (a, b) => a - b,
          );
          const byGw = (ticker: FixtureTick[]) => {
            const m = new Map<number, FixtureTick[]>();
            for (const tk of ticker) {
              const arr = m.get(tk.gw) ?? [];
              arr.push(tk);
              m.set(tk.gw, arr);
            }
            return m;
          };
          return (
            <div className="flex flex-col gap-4">
              {f.note && (
                <div className="flex flex-wrap items-center gap-3 rounded-lg border border-armband/25 bg-armband/[0.07] px-4 py-3">
                  {f.state && <StateBadge state={f.state} />}
                  <p className="text-sm text-armband/90">{f.note}</p>
                </div>
              )}

              {/* Legend */}
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                {[1, 2, 3, 4, 5].map((n) => (
                  <span key={n} className="flex items-center gap-1.5">
                    <span
                      className={`grid h-5 w-5 place-items-center rounded-[4px] font-mono text-[10px] font-bold ${FDR_CLS[n]}`}
                    >
                      {n}
                    </span>
                    <span className="font-mono text-[9px] uppercase tracking-[0.12em] text-ink-500">
                      {FDR_LABEL[n]}
                    </span>
                  </span>
                ))}
                <span className="font-mono text-[9px] uppercase tracking-[0.12em] text-ink-500">
                  H home · A away ·{' '}
                  <span className="text-volt">2</span> double GW
                </span>
              </div>

              <Card className="overflow-x-auto">
                <table className="w-full min-w-[720px] border-separate border-spacing-0 text-sm">
                  <thead>
                    <tr className="text-left font-mono text-[9px] uppercase tracking-[0.16em] text-ink-500">
                      <th className="sticky left-0 z-10 border-b border-line bg-[#0e2017] py-2.5 pl-3 pr-2 font-bold md:pl-4">
                        Team
                      </th>
                      {gws.map((gw) => (
                        <th key={gw} className="border-b border-line px-1 py-2.5 text-center font-bold">
                          GW{gw}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {f.teams.map((team) => {
                      const m = byGw(team.ticker);
                      return (
                        <tr key={team.code}>
                          <td className="sticky left-0 z-10 border-b border-line/50 bg-[#0e2017] py-1.5 pl-3 pr-3 md:pl-4">
                            <span className="font-mono text-xs font-bold tracking-wide text-ink-100">
                              {team.short_name}
                            </span>
                          </td>
                          {gws.map((gw) => (
                            <td key={gw} className="min-w-[64px] border-b border-line/50 p-1">
                              <OppCell ticks={m.get(gw) ?? []} />
                            </td>
                          ))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </Card>
            </div>
          );
        }}
      </DataGate>
    </div>
  );
}
