import type { Meta } from '../types';
import { useJson } from '../lib/useJson';
import { PageHeader } from '../components/Layout';
import { Card, DataGate } from '../components/ui';

/** What is actually live: the commit this build came from and the last real
 * changes, straight from git at deploy time. If a change is not listed here,
 * it has not shipped. */
export default function Changelog() {
  const state = useJson<Meta>('meta.json');
  return (
    <div>
      <PageHeader
        title="What's live"
        subtitle="Every change that has shipped to this site, newest first. If it isn't on this list, it isn't running."
      />
      <DataGate state={state}>
        {(meta) => (
          <Card className="divide-y divide-line/60">
            <p className="px-4 py-3 font-mono text-[11px] uppercase tracking-[0.14em] text-ink-500">
              Data built {new Date(meta.generated_at).toLocaleString()} · build {meta.commit ?? '—'}
            </p>
            {meta.changelog.map((c) => (
              <div key={c.sha} className="flex gap-4 px-4 py-2.5">
                <span className="w-20 shrink-0 font-mono text-[11px] text-ink-500">
                  {new Date(c.date).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}
                </span>
                <span className="text-sm text-ink-100">{c.subject}</span>
                <span className="ml-auto shrink-0 font-mono text-[10px] text-ink-500">{c.sha}</span>
              </div>
            ))}
          </Card>
        )}
      </DataGate>
    </div>
  );
}
