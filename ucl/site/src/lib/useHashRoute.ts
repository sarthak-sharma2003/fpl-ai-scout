import { useEffect, useState } from "react";

export const VIEWS = ["now", "plan", "players", "teams"] as const;
export type View = (typeof VIEWS)[number];

function readHash(): View {
  // Tolerate both "#plan" (what navigate() writes) and "#/plan" (the
  // leading-slash convention a bookmarked/typed deep link might use).
  const h = window.location.hash.replace(/^#\/?/, "");
  return (VIEWS as readonly string[]).includes(h) ? (h as View) : "now";
}

/** Tab state lives in the URL hash — four tabs don't need a router, and this
 * keeps back/forward and reload landing on the same view. */
export function useHashRoute(): [View, (v: View) => void] {
  const [view, setView] = useState<View>(readHash);

  useEffect(() => {
    const onHashChange = () => setView(readHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const navigate = (v: View) => {
    window.location.hash = v;
    setView(v);
  };

  return [view, navigate];
}
