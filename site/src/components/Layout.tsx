import { useState, type ReactNode } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { useJson } from '../lib/useJson';
import Xabi from './Xabi';
import type { Analytics } from '../types';

const UCL_SCOUT_URL = '/fpl-ai-scout/ucl/';

/** `external` entries leave the SPA entirely, so they render as a plain
 * anchor rather than a NavLink — UCL Scout is a sibling build with its own
 * data contract and deploy, not another route in this app. */
const NAV = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/my-team', label: 'My Team' },
  { to: '/players', label: 'Players' },
  { to: '/fixtures', label: 'Fixtures' },
  { to: '/chips', label: 'Chips' },
  { to: '/league', label: 'League' },
  { to: '/transfers', label: 'Transfers' },
  { to: '/signals', label: 'Signals' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/rules', label: 'Rules' },
  { to: UCL_SCOUT_URL, label: 'UCL', external: true },
];

function Wordmark() {
  return (
    <NavLink to="/" className="block shrink-0 leading-none" aria-label="Xabi's Long-Xo — home">
      <span className="font-display text-[22px] font-bold uppercase italic leading-none tracking-tight text-ink-100">
        Xabi's&nbsp;<span className="text-volt-deep">Long-Xo</span>
      </span>
      <span className="mt-1 block font-mono text-[8px] uppercase tracking-[0.34em] text-ink-500">
        FPL 26/27 war room
      </span>
    </NavLink>
  );
}

function NavItem({ to, label, end, external }: {
  to: string; label: string; end?: boolean; external?: boolean;
}) {
  if (external) {
    return (
      <a
        href={to}
        className={`${navClass({ isActive: false })} gap-1`}
        title="UCL Scout — Champions League Fantasy (separate build)"
      >
        {label}
        <span aria-hidden className="text-[8px] leading-none">↗</span>
        <span className="sr-only">(opens UCL Scout)</span>
      </a>
    );
  }
  return (
    <NavLink to={to} end={end} className={navClass}>
      {label}
    </NavLink>
  );
}

function navClass({ isActive }: { isActive: boolean }) {
  return `flex items-center whitespace-nowrap border-b-2 px-2.5 pb-2 pt-2.5 font-mono text-[11px] font-bold uppercase tracking-[0.14em] transition-colors md:pb-0 md:pt-0 ${
    isActive
      ? 'border-volt text-volt-deep'
      : 'border-transparent text-ink-500 hover:text-ink-100'
  }`;
}

/** Floodlights: day/night stock for the whole war room.
 *
 * index.html resolves `data-theme` before first paint (stored choice, else
 * night), so this only flips it and records a deliberate choice — nothing is
 * written to localStorage until the manager actually presses it, so a first
 * visit is night everywhere. */
function Floodlights() {
  const [theme, setTheme] = useState(
    () => (document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'),
  );
  const next = theme === 'dark' ? 'light' : 'dark';

  function flip() {
    document.documentElement.dataset.theme = next;
    const meta = document.querySelector<HTMLMetaElement>('meta[name=theme-color]');
    if (meta) meta.content = next === 'dark' ? '#121110' : '#f4f0e6';
    try {
      localStorage.setItem('theme', next);
    } catch {
      /* private mode — the flip still holds for this session */
    }
    setTheme(next);
  }

  return (
    <button
      type="button"
      onClick={flip}
      title={`Switch to ${next} stock`}
      className="flex items-center gap-1.5 rounded-sm border border-line px-2 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-ink-500 transition-colors hover:border-volt/50 hover:text-ink-100"
    >
      <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" aria-hidden>
        {theme === 'dark' ? (
          /* moon — offer the night we are already in as the current state */
          <path
            fill="currentColor"
            d="M20.2 14.6A8.6 8.6 0 0 1 9.4 3.8a8.6 8.6 0 1 0 10.8 10.8Z"
          />
        ) : (
          <>
            <circle cx="12" cy="12" r="4.4" fill="currentColor" />
            <path
              d="M12 1.8v2.6M12 19.6v2.6M22.2 12h-2.6M4.4 12H1.8M19.2 4.8l-1.9 1.9M6.7 17.3l-1.9 1.9M19.2 19.2l-1.9-1.9M6.7 6.7 4.8 4.8"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </>
        )}
      </svg>
      <span className="hidden sm:inline">{theme === 'dark' ? 'Night' : 'Day'}</span>
      <span className="sr-only">
        Current theme: {theme}. Press to switch to {next}.
      </span>
    </button>
  );
}

function Footer() {
  // One fetch per session (Layout persists across routes); powers the
  // "model vX" stamp without every page threading it through.
  const analytics = useJson<Analytics>('analytics.json');
  const version = analytics.status === 'ready' ? analytics.data.model_version : null;
  return (
    <footer className="border-t border-line px-4 py-4 md:px-8">
      <p className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-500">
        <span className="text-ink-300">Xabi's Long-Xo</span>
        <span aria-hidden>·</span>
        <span>model {version ?? '—'}</span>
        <span aria-hidden>·</span>
        <span>updated nightly</span>
        <span aria-hidden>·</span>
        <span>$0-budget build</span>
      </p>
    </footer>
  );
}

export default function Layout() {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-20 border-b border-line bg-pitch-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-stretch justify-between gap-6 px-4 md:px-8">
          <div className="py-2.5 md:py-3">
            <Wordmark />
          </div>
          <div className="flex min-w-0 items-stretch gap-4">
            {/* Desktop nav: broadcast-ticker tabs, volt underline on air */}
            <nav className="hidden min-w-0 items-stretch overflow-x-auto md:flex [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
              {NAV.map((item) => (
                <NavItem key={item.to} {...item} />
              ))}
            </nav>
            <div className="flex shrink-0 items-center py-2.5 md:py-3">
              <Floodlights />
            </div>
          </div>
        </div>
        {/* Mobile nav: scrollable strip under the wordmark */}
        <nav className="flex overflow-x-auto border-t border-line/60 px-2 md:hidden [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {NAV.map((item) => (
            <NavItem key={item.to} {...item} />
          ))}
        </nav>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 md:px-8 md:py-8">
        <Outlet />
      </main>

      <Footer />
      {/* Mounted in the layout, not a page: the manager should be reachable from
          whatever screen prompted the question. */}
      <Xabi />
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  right,
}: {
  title: string;
  subtitle?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        <h1 className="font-display text-[34px] font-bold uppercase leading-none tracking-tight text-ink-100 md:text-[40px]">
          {title}
        </h1>
        {subtitle && (
          <p className="mt-1.5 max-w-2xl text-[13px] leading-relaxed text-ink-500">{subtitle}</p>
        )}
      </div>
      {right}
    </div>
  );
}
