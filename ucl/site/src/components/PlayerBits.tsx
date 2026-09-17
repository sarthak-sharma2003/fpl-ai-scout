import { WarningIcon, ChevronIcon } from "./Icons";

const ELITE_RECOVERIES = 6;

/** recoveriesPer90 is the model's most under-priced signal (brief: a
 * defender at 6+/90 has a ~4pt floor before a single clean sheet or goal),
 * so it gets a dedicated, weighted treatment rather than a plain number —
 * tabular digits, a 4-tick meter, and bold accent ink once it clears the
 * "elite floor" threshold the product is actually built to surface. */
export function RecoveriesCell({ value }: { value: number | null }) {
  if (value == null) {
    return <span className="recov-cell num" aria-label="No recoveries data">—</span>;
  }
  const ticks = Math.max(0, Math.min(4, Math.ceil(value / 2)));
  const elite = value >= ELITE_RECOVERIES;
  return (
    <span className="recov-cell" title={elite ? `${value.toFixed(2)}/90 — elite recoveries floor` : `${value.toFixed(2)} recoveries/90`}>
      <span className={elite ? "recov-value recov-value--elite" : "recov-value"}>{value.toFixed(1)}</span>
      <span className="recov-ticks" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => (
          <span key={i} className={i < ticks ? "recov-tick recov-tick--on" : "recov-tick"} />
        ))}
      </span>
    </span>
  );
}

/** minutesTrusted === false means the projection rests on a guessed minutes
 * figure and the player can't captain. Expand-on-click (not hover-only, so
 * it works on the phones this app is actually used on) reveals why.
 * Controlled by the parent so a table row can render the expanded note as
 * a sibling <tr> rather than nested inside the cell. */
export function TrustChip({ note, open, onToggle }: { note: string; open: boolean; onToggle: () => void }) {
  return (
    <button type="button" className="trust-chip" aria-expanded={open} onClick={onToggle} title={note}>
      <WarningIcon />
      Guessed mins
      <ChevronIcon open={open} />
    </button>
  );
}

export function TrustNote({ note }: { note: string }) {
  return <p className="note-row__text">{note}</p>;
}
