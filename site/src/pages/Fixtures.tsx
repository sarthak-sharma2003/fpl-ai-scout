import { useJson } from '../lib/useJson';
import type { FixturesResponse } from '../types';
import { PageHeader } from '../components/Layout';
import { Card, DataGate, FdrPill } from '../components/ui';

export default function Fixtures() {
  const state = useJson<FixturesResponse>('fixtures.json');

  return (
    <div>
      <PageHeader title="Fixtures" subtitle="Per-team fixture ticker with difficulty ratings." />
      <DataGate state={state}>
        {(f) => (
          <div className="flex flex-col gap-4">
            {f.note && (
              <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
                {f.note}
              </div>
            )}
            <Card className="overflow-x-auto">
              <table className="w-full text-sm min-w-[600px]">
                <thead>
                  <tr className="border-b border-[var(--pitch-line)] text-left text-[var(--ink-500)] text-xs uppercase tracking-wide">
                    <th className="px-3 py-3 sticky left-0 bg-[var(--pitch-850)]">Team</th>
                    {f.teams[0]?.ticker.map((tk, i) => (
                      <th key={i} className="px-2 py-3 text-center">
                        GW{tk.gw}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {f.teams.map((team) => (
                    <tr key={team.code} className="border-b border-[var(--pitch-line)]/60">
                      <td className="px-3 py-2.5 font-medium text-[var(--ink-100)] sticky left-0 bg-[var(--pitch-850)]">
                        {team.short_name}
                      </td>
                      {team.ticker.map((tk, i) => (
                        <td key={i} className="px-2 py-2.5 text-center">
                          {tk.is_bgw ? (
                            <span className="text-[var(--ink-500)] text-xs">BGW</span>
                          ) : (
                            <div className="flex flex-col items-center gap-1">
                              <FdrPill fdr={tk.fdr} />
                              <span className="text-[10px] text-[var(--ink-500)]">
                                {tk.was_home ? '' : '@'}
                                {tk.opponent}
                                {tk.is_dgw ? ' (DGW)' : ''}
                              </span>
                            </div>
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </div>
        )}
      </DataGate>
    </div>
  );
}
