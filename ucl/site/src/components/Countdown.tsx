import { useCountdown } from "../lib/useCountdown";
import { pad2 } from "../lib/format";

/** Compact readout for the sticky topbar — present on every view. */
export function Countdown({ deadlineIso, matchday }: { deadlineIso: string; matchday: number }) {
  const c = useCountdown(deadlineIso);

  if (c.urgency === "expired") {
    return (
      <span className="countdown countdown--expired" role="status">
        <span className="countdown__label">MD{matchday}</span>
        <span className="countdown__digits">LOCKED</span>
      </span>
    );
  }

  return (
    <span className={`countdown countdown--${c.urgency}`} role="status">
      <span className="countdown__label">MD{matchday} in</span>
      <span className="countdown__digits">
        {c.days > 0 ? `${c.days}d ` : ""}
        {pad2(c.hours)}:{pad2(c.minutes)}:{pad2(c.seconds)}
      </span>
    </span>
  );
}
