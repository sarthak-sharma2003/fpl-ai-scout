import { useEffect, useState } from 'react';

export type Loadable<T> =
  | { status: 'loading' }
  | { status: 'error'; error: string }
  | { status: 'ready'; data: T };

/** Fetches a static JSON file published by `fplscout publish` (site/public/data/…),
 * respecting Vite's configured base path so it works both in dev and once deployed
 * under github.io/fpl-ai-scout/. */
export function useJson<T>(path: string): Loadable<T> {
  const [state, setState] = useState<Loadable<T>>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    fetch(`${import.meta.env.BASE_URL}data/${path}`)
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json() as Promise<T>;
      })
      .then((data) => {
        if (!cancelled) setState({ status: 'ready', data });
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ status: 'error', error: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  return state;
}
