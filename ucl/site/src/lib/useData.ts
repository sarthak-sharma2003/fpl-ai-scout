import { useEffect, useState } from "react";
import type { AllData } from "../types";

const FILES = ["meta", "squad", "plan", "players", "fixtures", "teams"] as const;

export type DataState =
  | { status: "loading" }
  | { status: "error"; failed: string[]; message: string }
  | { status: "ready"; data: AllData };

/** Fetches every JSON file the app needs, once, on mount. Relative paths so
 * the built site works from any GitHub Pages subpath. */
export function useAllData(): DataState {
  const [state, setState] = useState<DataState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const results = await Promise.allSettled(
        FILES.map(async (name) => {
          // BASE_URL carries the deployed subpath (e.g. /ucl-scout/) — an
          // absolute "/data/..." path would 404 in production while working
          // fine in dev, so this must stay derived from it, never hardcoded.
          const res = await fetch(`${import.meta.env.BASE_URL}data/${name}.json`);
          if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
          return res.json();
        }),
      );
      if (cancelled) return;

      const failed = FILES.filter((_, i) => results[i].status === "rejected");
      if (failed.length > 0) {
        const detail = failed
          .map((name) => {
            const r = results[FILES.indexOf(name)];
            return r.status === "rejected" ? `${name}.json (${r.reason})` : name;
          })
          .join(", ");
        setState({
          status: "error",
          failed: [...failed],
          message: `Could not load: ${detail}`,
        });
        return;
      }

      const [meta, squad, plan, players, fixtures, teams] = results.map(
        (r) => (r as PromiseFulfilledResult<unknown>).value,
      );
      setState({
        status: "ready",
        data: { meta, squad, plan, players, fixtures, teams } as AllData,
      });
    }

    load();
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
