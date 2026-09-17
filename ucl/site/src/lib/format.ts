// Date/number formatting helpers. No date library — Intl + Date cover everything here.

export interface CountdownParts {
  expired: boolean;
  totalMs: number;
  days: number;
  hours: number;
  minutes: number;
  seconds: number;
}

export function countdown(deadlineIso: string, now: Date): CountdownParts {
  const totalMs = new Date(deadlineIso).getTime() - now.getTime();
  const expired = totalMs <= 0;
  const abs = Math.abs(totalMs);
  const days = Math.floor(abs / 86_400_000);
  const hours = Math.floor((abs % 86_400_000) / 3_600_000);
  const minutes = Math.floor((abs % 3_600_000) / 60_000);
  const seconds = Math.floor((abs % 60_000) / 1000);
  return { expired, totalMs, days, hours, minutes, seconds };
}

export function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

const DATE_FMT = new Intl.DateTimeFormat(undefined, {
  weekday: "short",
  day: "numeric",
  month: "short",
});

const DATETIME_FMT = new Intl.DateTimeFormat(undefined, {
  weekday: "short",
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

const TIME_FMT = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
});

export function formatDay(iso: string): string {
  return DATE_FMT.format(new Date(iso + "T00:00:00"));
}

export function formatDateTime(iso: string): string {
  return DATETIME_FMT.format(new Date(iso));
}

export function formatTime(iso: string): string {
  return TIME_FMT.format(new Date(iso));
}

/** "3h ago" / "in 2d" — used for the generatedAt staleness readout. */
export function relativeTo(iso: string, now: Date): string {
  const ms = now.getTime() - new Date(iso).getTime();
  const future = ms < 0;
  const abs = Math.abs(ms);
  const mins = Math.round(abs / 60_000);
  const hrs = Math.round(abs / 3_600_000);
  const days = Math.round(abs / 86_400_000);
  let label: string;
  if (mins < 1) label = "just now";
  else if (mins < 60) label = `${mins}m`;
  else if (hrs < 48) label = `${hrs}h`;
  else label = `${days}d`;
  if (label === "just now") return label;
  return future ? `in ${label}` : `${label} ago`;
}

export function hoursSince(iso: string, now: Date): number {
  return (now.getTime() - new Date(iso).getTime()) / 3_600_000;
}

export function formatMoney(v: number): string {
  return `€${v.toFixed(1)}m`;
}
