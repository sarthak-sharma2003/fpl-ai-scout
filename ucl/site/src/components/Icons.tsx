// Small authored icon set — no icon library needed for six glyphs.

export function WarningIcon({ className }: { className?: string }) {
  return (
    <svg className={className ?? "icon"} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M8 1.6 15 13.6a1 1 0 0 1-.87 1.5H1.87A1 1 0 0 1 1 13.6L8 1.6Z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
      <path d="M8 6v3.4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="8" cy="11.9" r="0.9" fill="currentColor" />
    </svg>
  );
}

export function SunIcon({ className }: { className?: string }) {
  return (
    <svg className={className ?? "icon"} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="3.1" stroke="currentColor" strokeWidth="1.3" />
      <g stroke="currentColor" strokeWidth="1.3" strokeLinecap="round">
        <path d="M8 1v1.6M8 13.4V15M15 8h-1.6M2.6 8H1M12.7 3.3l-1.1 1.1M4.4 11.6l-1.1 1.1M12.7 12.7l-1.1-1.1M4.4 4.4 3.3 3.3" />
      </g>
    </svg>
  );
}

export function MoonIcon({ className }: { className?: string }) {
  return (
    <svg className={className ?? "icon"} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M13.6 9.9A5.6 5.6 0 0 1 6.1 2.4a5.8 5.8 0 1 0 7.5 7.5Z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function SortIcon({ dir }: { dir: "asc" | "desc" | "none" }) {
  return (
    <svg className="icon" viewBox="0 0 10 12" fill="none" aria-hidden="true" style={{ width: "0.6em", height: "0.75em" }}>
      <path
        d="M5 0 9 5H1Z"
        fill={dir === "asc" ? "currentColor" : "var(--rule-strong)"}
      />
      <path
        d="M5 12 1 7h8Z"
        fill={dir === "desc" ? "currentColor" : "var(--rule-strong)"}
      />
    </svg>
  );
}

export function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      className="icon"
      viewBox="0 0 12 12"
      fill="none"
      aria-hidden="true"
      style={{ transform: open ? "rotate(180deg)" : undefined, transition: "transform 120ms ease-out" }}
    >
      <path d="M2.5 4.5 6 8l3.5-3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
