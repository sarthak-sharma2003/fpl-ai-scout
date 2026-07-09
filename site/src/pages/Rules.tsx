import { useJson } from '../lib/useJson';
import type { Rule } from '../types';
import { PageHeader } from '../components/Layout';
import { Card, DataGate } from '../components/ui';

export default function Rules() {
  const state = useJson<Rule[]>('rules.json');

  return (
    <div>
      <PageHeader
        title="Rules"
        subtitle="The decision rules the pipeline enforces. Read-only in v1 — edited by committing config/rules.yaml."
      />
      <DataGate state={state}>
        {(rules) => (
          <div className="flex flex-col gap-3">
            {rules.length === 0 && (
              <p className="text-sm text-[var(--ink-500)]">No rules configured.</p>
            )}
            {rules.map((r) => (
              <Card key={r.id} className="p-4 md:p-5">
                <div className="flex items-start justify-between gap-3 mb-2">
                  <h3 className="text-sm font-semibold text-[var(--ink-100)]">{r.title}</h3>
                  <span
                    className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${
                      r.enabled
                        ? 'bg-emerald-500/15 text-emerald-300'
                        : 'bg-white/5 text-[var(--ink-500)]'
                    }`}
                  >
                    {r.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                </div>
                <p className="text-sm text-[var(--ink-300)] leading-relaxed">{r.body}</p>
              </Card>
            ))}
          </div>
        )}
      </DataGate>
    </div>
  );
}
