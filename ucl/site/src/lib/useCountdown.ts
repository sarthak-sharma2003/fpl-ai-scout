import { useEffect, useState } from "react";
import { countdown, type CountdownParts } from "./format";

export type Urgency = "calm" | "soon" | "urgent" | "expired";

export interface CountdownState extends CountdownParts {
  urgency: Urgency;
}

function urgencyOf(c: CountdownParts): Urgency {
  if (c.expired) return "expired";
  if (c.totalMs < 3_600_000) return "urgent"; // < 1h
  if (c.totalMs < 6 * 3_600_000) return "soon"; // < 6h
  return "calm";
}

/** Ticking countdown to `deadlineIso`, shared by the compact topbar readout
 * and the Now-view hero banner so the urgency bands only live in one place. */
export function useCountdown(deadlineIso: string): CountdownState {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const c = countdown(deadlineIso, now);
  return { ...c, urgency: urgencyOf(c) };
}
