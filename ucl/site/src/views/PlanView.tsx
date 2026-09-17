import type { PlanAction, PlanData } from "../types";

export function PlanView({ plan }: { plan: PlanData }) {
  return (
    <div>
      <div className="now-hero">
        <div>
          <h1 className="now-hero__title">Season plan</h1>
          <p className="now-hero__sub">
            MD{plan.matchday}–{plan.lastMatchday} · {plan.totalProjected.toFixed(1)} pts projected
          </p>
        </div>
      </div>

      {plan.limitless && (
        <div className="limitless-banner">
          <div>
            <div className="limitless-banner__title">Play Limitless at MD{plan.limitless.matchday}</div>
            <div className="limitless-banner__detail">
              Projected gain: +{plan.limitless.gain.toFixed(1)} pts over holding the current squad
            </div>
          </div>
        </div>
      )}

      <h2 className="section-label">Matchday-by-matchday</h2>
      <div className="plan-list">
        {plan.actions.map((a) => (
          <PlanRow key={a.matchday} action={a} />
        ))}
      </div>
    </div>
  );
}

function PlanRow({ action }: { action: PlanAction }) {
  const isTransfer = action.sells.length > 0 || action.buys.length > 0;
  const rowClass = isTransfer ? "plan-row plan-row--transfer" : "plan-row plan-row--bank";

  return (
    <div className={rowClass}>
      <span className="plan-row__md">{String(action.matchday).padStart(2, "0")}</span>
      <div className="plan-row__body">
        <div className="plan-row__headline">
          <span className="plan-row__action">{isTransfer ? "Transfer" : "Bank"}</span>
          {action.chip && <span className="badge badge--recoveries">{action.chip}</span>}
          {action.hits > 0 && <span className="badge badge--warning">−{action.hits * 4} hit</span>}
        </div>
        {isTransfer ? (
          <div className="plan-row__transfer-detail">
            {action.sells.map((s) => (
              <span key={s.id}>{s.name}</span>
            ))}
            <span className="transfer-arrow">&rarr;</span>
            {action.buys.map((b) => (
              <span key={b.id}>{b.name}</span>
            ))}
          </div>
        ) : (
          <div className="plan-row__meta">
            {action.freeAvailable} free transfer{action.freeAvailable === 1 ? "" : "s"} available
          </div>
        )}
        <div className="plan-row__meta">Captain: {action.captain?.name ?? "—"}</div>
      </div>
      <div className="plan-row__stats">
        <div className="plan-row__projected">{action.projected.toFixed(1)}</div>
        {action.hits > 0 && <div className="plan-row__hits">{action.hits} hit{action.hits === 1 ? "" : "s"}</div>}
      </div>
    </div>
  );
}
