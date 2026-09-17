import { useCountdown } from "../lib/useCountdown";
import { pad2 } from "../lib/format";

const URGENCY_COPY: Record<string, string> = {
  calm: "Deadline",
  soon: "Deadline closing in",
  urgent: "Deadline closing in",
};

/** The Now view's hero — this is the number the whole product exists to get
 * in front of someone in time. Semantics ride the colour: calm while there
 * are hours to spare, amber under 6h, red and urgent under 1h, and a loud
 * permanent LOCKED state once it's gone — never a blank space. */
export function CountdownHero({ deadlineIso, matchday }: { deadlineIso: string; matchday: number }) {
  const c = useCountdown(deadlineIso);

  if (c.urgency === "expired") {
    return (
      <div className="countdown-hero countdown-hero--expired" role="status">
        <p className="countdown-hero__label">MD{matchday} deadline</p>
        <p className="countdown-hero__digits">LOCKED</p>
        <p className="countdown-hero__sub">
          Passed {c.days > 0 ? `${c.days}d ` : ""}
          {pad2(c.hours)}h {pad2(c.minutes)}m ago — this is the squad UCL Fantasy locked in.
        </p>
      </div>
    );
  }

  return (
    <div className={`countdown-hero countdown-hero--${c.urgency}`} role="status">
      <p className="countdown-hero__label">
        {URGENCY_COPY[c.urgency]} · MD{matchday}
      </p>
      <p className="countdown-hero__digits">
        {c.days > 0 && <span className="countdown-hero__days">{c.days}d </span>}
        {pad2(c.hours)}
        <span className="countdown-hero__sep">:</span>
        {pad2(c.minutes)}
        <span className="countdown-hero__sep">:</span>
        {pad2(c.seconds)}
      </p>
    </div>
  );
}
