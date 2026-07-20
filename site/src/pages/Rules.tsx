import { useJson } from '../lib/useJson';
import type { Rule } from '../types';
import { PageHeader } from '../components/Layout';
import { Card, DataGate, Eyebrow } from '../components/ui';

function RuleCard({ r }: { r: Rule }) {
  return (
    <Card className={`flex flex-col p-4 ${r.enabled ? '' : 'opacity-55'}`}>
      <div className="mb-1.5 flex items-start justify-between gap-3">
        <h3 className="text-sm font-semibold leading-snug text-ink-100">{r.title}</h3>
        {!r.enabled && (
          <span className="shrink-0 rounded-sm bg-white/5 px-1.5 py-px font-mono text-[9px] font-bold uppercase tracking-[0.14em] text-ink-500 ring-1 ring-line">
            Disabled
          </span>
        )}
      </div>
      <p className="grow text-[13px] leading-relaxed text-ink-300">{r.body}</p>
      {r.source && (
        <p
          title={r.source}
          className="mt-3 truncate border-t border-line/60 pt-2 font-mono text-[9px] text-ink-500"
        >
          {r.source}
        </p>
      )}
    </Card>
  );
}

export default function Rules() {
  const state = useJson<Rule[]>('rules.json');

  return (
    <div>
      <PageHeader
        title="Rules"
        subtitle="What the pipeline enforces in code and the playbook it plays by — read-only here, edited by committing config/rules.yaml."
      />
      <DataGate state={state}>
        {(rules) => {
          const enforced = rules.filter((r) => r.kind !== 'strategy');
          const strategy = rules.filter((r) => r.kind === 'strategy');
          return (
            <div className="flex flex-col gap-7">
              {rules.length === 0 && <p className="text-sm text-ink-500">No rules configured.</p>}

              {enforced.length > 0 && (
                <div>
                  <Eyebrow>Enforced by the pipeline</Eyebrow>
                  <p className="-mt-1 mb-3 text-xs text-ink-500">
                    Hard constraints wired into the optimizer and simulator — the model cannot
                    break these even when the EV says otherwise.
                  </p>
                  <div className="grid gap-3 md:grid-cols-2">
                    {enforced.map((r) => (
                      <RuleCard key={r.id} r={r} />
                    ))}
                  </div>
                </div>
              )}

              {strategy.length > 0 && (
                <div>
                  <Eyebrow>Strategy playbook</Eyebrow>
                  <p className="-mt-1 mb-3 text-xs text-ink-500">
                    Judgment calls the model informs but a human fires — chip timing, captaincy by
                    league state, deadline hygiene.
                  </p>
                  <div className="grid gap-3 md:grid-cols-2">
                    {strategy.map((r) => (
                      <RuleCard key={r.id} r={r} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          );
        }}
      </DataGate>
    </div>
  );
}
