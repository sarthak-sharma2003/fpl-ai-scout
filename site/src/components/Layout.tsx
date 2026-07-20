import type { ReactNode } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { useJson } from '../lib/useJson';
import type { Analytics } from '../types';

const NAV = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/players', label: 'Players' },
  { to: '/fixtures', label: 'Fixtures' },
  { to: '/chips', label: 'Chips' },
  { to: '/league', label: 'League' },
  { to: '/transfers', label: 'Transfers' },
  { to: '/signals', label: 'Signals' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/rules', label: 'Rules' },
];

function Wordmark() {
  return (
    <NavLink to="/" className="block shrink-0 leading-none" aria-label="Xabi's Long-Xo — home">
      <span className="font-display text-[22px] font-bold uppercase italic leading-none tracking-tight text-ink-100">
        Xabi's&nbsp;<span className="text-volt">Long-Xo</span>
      </span>
      <span className="mt-1 block font-mono text-[8px] uppercase tracking-[0.34em] text-ink-500">
        FPL 26/27 war room
      </span>
    </NavLink>
  );
}

function navClass({ isActive }: { isActive: boolean }) {
  return `flex items-center whitespace-nowrap border-b-2 px-2.5 pb-2 pt-2.5 font-mono text-[11px] font-bold uppercase tracking-[0.14em] transition-colors md:pb-0 md:pt-0 ${
    isActive
      ? 'border-volt text-volt'
      : 'border-transparent text-ink-500 hover:text-ink-100'
  }`;
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
          {/* Desktop nav: broadcast-ticker tabs, volt underline on air */}
          <nav className="hidden items-stretch overflow-x-auto md:flex">
            {NAV.map(({ to, label, end }) => (
              <NavLink key={to} to={to} end={end} className={navClass}>
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
        {/* Mobile nav: scrollable strip under the wordmark */}
        <nav className="flex overflow-x-auto border-t border-line/60 px-2 md:hidden [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {NAV.map(({ to, label, end }) => (
            <NavLink key={to} to={to} end={end} className={navClass}>
              {label}
            </NavLink>
          ))}
        </nav>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 md:px-8 md:py-8">
        <Outlet />
      </main>

      <Footer />
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
