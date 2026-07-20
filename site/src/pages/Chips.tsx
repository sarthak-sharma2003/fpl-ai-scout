import { useJson } from '../lib/useJson';
import type { ChipInfo, ChipsResponse } from '../types';
import { PageHeader } from '../components/Layout';
import { Card, ChalkState, DataGate, Eyebrow } from '../components/ui';

const CHIP_NAME: Record<string, string> = {
  wildcard: 'Wildcard',
  freehit: 'Free Hit',
  bboost: 'Bench Boost',
  '3xc': 'Triple Captain',
};

const BB_BAR = 15; // strategy rule: ~15+ realistic bench points to fire BB

/** GW1→38 strip: the valid window lit, used GW burned, current GW ticked. */
function WindowStrip({ chip, currentGw }: { chip: ChipInfo; currentGw: number }) {
  return (
    <div>
      <div className="flex h-2 gap-px">
        {Array.from({ length: 38 }, (_, i) => {
          const gw = i + 1;
          const inWindow = gw >= chip.start_gw && gw <= chip.stop_gw;
          let cls = 'bg-white/[0.06]';
          if (inWindow) cls = chip.used_gw != null ? 'bg-white/10' : 'bg-volt/40';
          if (gw === chip.used_gw) cls = 'bg-danger';
          if (gw === currentGw) cls += ' outline outline-1 outline-ink-100/70';
          return <span key={gw} title={`GW${gw}`} className={`flex-1 rounded-[1px] ${cls}`} />;
        })}
      </div>
      <div className="mt-1 flex justify-between font-mono text-[8px] uppercase tracking-[0.14em] text-ink-500">
        <span>GW1</span>
        <span>
          window {chip.start_gw}–{chip.stop_gw}
        </span>
        <span>GW38</span>
      </div>
    </div>
  );
}

function StatusBadge({ chip }: { chip: ChipInfo }) {
  if (chip.used_gw != null) {
    return (
      <span className="rounded-sm bg-danger/10 px-2 py-0.5 font-mono text-[9px] font-bold uppercase tracking-[0.14em] text-danger ring-1 ring-danger/40 line-through decoration-2">
        Used GW{chip.used_gw}
      </span>
    );
  }
  if (chip.available && chip.active_now) {
    return (
      <span className="rounded-sm bg-volt/10 px-2 py-0.5 font-mono text-[9px] font-bold uppercase tracking-[0.14em] text-volt ring-1 ring-volt/40">
        Playable now
      </span>
    );
  }
  if (chip.available) {
    return (
      <span className="rounded-sm bg-white/5 px-2 py-0.5 font-mono text-[9px] font-bold uppercase tracking-[0.14em] text-ink-300 ring-1 ring-line">
        Opens GW{chip.start_gw}
      </span>
    );
  }
  return (
    <span className="rounded-sm bg-white/5 px-2 py-0.5 font-mono text-[9px] font-bold uppercase tracking-[0.14em] text-ink-500 ring-1 ring-line">
      Unavailable
    </span>
  );
}

/** The this-week observable: bench EV vs the 15-pt bar, or the 3xc captain. */
function ThisWeek({ chip }: { chip: ChipInfo }) {
  const tw = chip.this_week;
  if (!tw) return null;
  if (tw.bench_ev != null) {
    const scale = Math.max(20, tw.bench_ev * 1.15);
    const ok = tw.bench_ev >= BB_BAR;
    return (
      <div className="mt-3">
        <div className="mb-1 flex items-baseline justify-between">
          <span className="font-mono text-[9px] uppercase tracking-[0.16em] text-ink-500">
            Bench EV this week
          </span>
          <span
            className={`font-display text-xl font-bold leading-none tabular-nums ${ok ? 'text-volt' : 'text-armband'}`}
          >
            {tw.bench_ev.toFixed(1)}
          </span>
        </div>
        <div className="relative h-2 rounded-full bg-white/[0.06]">
          <div
            className={`h-full rounded-full ${ok ? 'bg-volt/70' : 'bg-armband/70'}`}
            style={{ width: `${Math.min(100, (tw.bench_ev / scale) * 100)}%` }}
          />
          <div
            className="absolute -top-1 h-4 w-[2px] bg-ink-100/80"
            style={{ left: `${(BB_BAR / scale) * 100}%` }}
            title={`${BB_BAR}-pt worth-it bar`}
          />
        </div>
        <p className="mt-1 text-right font-mono text-[8px] uppercase tracking-[0.14em] text-ink-500">
          {BB_BAR}-pt worth-it bar
        </p>
      </div>
    );
  }
  if (tw.name != null) {
    return (
      <div className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-display text-xl font-bold uppercase leading-none text-ink-100">
          {tw.name}
        </span>
        {tw.extra_ev != null && (
          <span className="font-mono text-[11px] font-bold text-volt tabular-nums">
            +{tw.extra_ev.toFixed(2)} extra EV
          </span>
        )}
        {tw.q90 != null && (
          <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-ink-500 tabular-nums">
            ceiling {tw.q90.toFixed(1)} (q90)
          </span>
        )}
      </div>
    );
  }
  return null;
}

function ChipCard({ chip, currentGw }: { chip: ChipInfo; currentGw: number }) {
  const half = chip.start_gw <= 19 ? 'First half' : 'Second half';
  return (
    <Card className={`p-4 ${chip.used_gw != null ? 'opacity-70' : ''}`}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-xl font-semibold uppercase leading-none text-ink-100">
            {CHIP_NAME[chip.chip] ?? chip.chip}
          </h3>
          <p className="mt-1 font-mono text-[9px] uppercase tracking-[0.16em] text-ink-500">{half}</p>
        </div>
        <StatusBadge chip={chip} />
      </div>
      <WindowStrip chip={chip} currentGw={currentGw} />
      <ThisWeek chip={chip} />
      <p className="mt-3 border-t border-line/60 pt-3 text-[13px] leading-relaxed text-ink-300">
        {chip.guidance}
      </p>
    </Card>
  );
}

function Radar({ data }: { data: ChipsResponse }) {
  return (
    <div>
      <Eyebrow>DGW / BGW radar</Eyebrow>
      {data.dgw_bgw_radar.length === 0 ? (
        <ChalkState title="Radar clear">
          <p>{data.radar_note ?? 'No doubles or blanks on the horizon.'}</p>
        </ChalkState>
      ) : (
        <Card className="divide-y divide-line/60">
          {data.dgw_bgw_radar.map((r) => (
            <div key={r.gw} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
              <span className="w-14 font-display text-2xl font-bold uppercase leading-none text-ink-100">
                GW{r.gw}
              </span>
              {r.dgw_teams.map((t) => (
                <span
                  key={`d${t}`}
                  className="rounded-sm bg-volt/10 px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wide text-volt ring-1 ring-volt/30"
                >
                  {t} ×2
                </span>
              ))}
              {r.bgw_teams.map((t) => (
                <span
                  key={`b${t}`}
                  className="rounded-sm bg-danger/10 px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wide text-danger ring-1 ring-danger/30"
                >
                  {t} blank
                </span>
              ))}
            </div>
          ))}
        </Card>
      )}
      <p className="mt-2 text-xs text-ink-500">
        Chip weeks are usually decided by this table — doubles and blanks emerge from postponements
        as the season runs.
      </p>
    </div>
  );
}

export default function Chips() {
  const state = useJson<ChipsResponse>('chips.json');

  return (
    <div>
      <PageHeader
        title="Chips"
        subtitle="Chip windows synced live from the FPL API — never hardcoded — with the optimizer's this-week observables against the playbook's firing bars."
      />
      <DataGate state={state}>
        {(c) =>
          c.configured ? (
            <div className="flex flex-col gap-6">
              <div>
                <Eyebrow
                  action={
                    <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-500">
                      {c.season} · ref GW{c.reference_gw}
                    </span>
                  }
                >
                  Chip inventory
                </Eyebrow>
                <div className="grid gap-3 md:grid-cols-2">
                  {c.chips.map((chip) => (
                    <ChipCard key={chip.chip_id} chip={chip} currentGw={c.reference_gw} />
                  ))}
                </div>
              </div>
              <Radar data={c} />
            </div>
          ) : (
            <div className="flex flex-col gap-6">
              <ChalkState title="Waiting for kickoff">
                <p>{c.note}</p>
                <p className="mt-2">
                  This board populates the day the 26/27 API goes live. Each chip card will show its
                  valid GW window as a strip, availability, and the week's observable — bench EV
                  against the 15-pt bar for Bench Boost, the captain's extra EV and ceiling for
                  Triple Captain.
                </p>
              </ChalkState>
              <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
                {Object.entries(CHIP_NAME).map(([key, name]) => (
                  <div
                    key={key}
                    className="rounded-lg border border-dashed border-line bg-pitch-900/30 px-4 py-4"
                  >
                    <p className="font-display text-lg font-semibold uppercase leading-none text-ink-300">
                      {name}
                    </p>
                    <p className="mt-1.5 font-mono text-[9px] uppercase tracking-[0.16em] text-ink-500">
                      ×2 · one per half
                    </p>
                    <div className="mt-3 flex h-2 gap-px opacity-40">
                      {Array.from({ length: 38 }, (_, i) => (
                        <span key={i} className="flex-1 rounded-[1px] bg-white/[0.07]" />
                      ))}
                    </div>
                    <p className="mt-1.5 font-mono text-[8px] uppercase tracking-[0.14em] text-ink-500">
                      window syncs at launch
                    </p>
                  </div>
                ))}
              </div>
              <Radar data={c} />
            </div>
          )
        }
      </DataGate>
    </div>
  );
}
