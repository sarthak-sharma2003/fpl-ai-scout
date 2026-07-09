import type { ReactNode } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { HomeIcon, SwapIcon, CalendarIcon, BoltIcon, ChartIcon, ListIcon } from './icons';

const NAV = [
  { to: '/', label: 'Dashboard', icon: HomeIcon, end: true },
  { to: '/transfers', label: 'Transfers', icon: SwapIcon },
  { to: '/fixtures', label: 'Fixtures', icon: CalendarIcon },
  { to: '/signals', label: 'Signals', icon: BoltIcon },
  { to: '/analytics', label: 'Analytics', icon: ChartIcon },
  { to: '/rules', label: 'Rules', icon: ListIcon },
];

function Logo() {
  return (
    <div className="flex items-center gap-2">
      <div className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-emerald-400 to-emerald-600 text-[var(--pitch-950)] font-bold text-sm">
        FS
      </div>
      <span className="font-semibold tracking-tight text-[var(--ink-100)]">
        FPL AI Scout
      </span>
    </div>
  );
}

export default function Layout() {
  return (
    <div className="min-h-screen flex flex-col">
      {/* Desktop / tablet top nav */}
      <header className="hidden md:flex sticky top-0 z-20 items-center justify-between border-b border-[var(--pitch-line)] bg-[var(--pitch-950)]/90 backdrop-blur px-6 py-3">
        <Logo />
        <nav className="flex items-center gap-1">
          {NAV.map(({ to, label, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-emerald-500/15 text-emerald-300'
                    : 'text-[var(--ink-300)] hover:text-[var(--ink-100)] hover:bg-white/5'
                }`
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
      </header>

      {/* Mobile top bar (logo only — nav lives at the bottom, thumb reach) */}
      <header className="md:hidden sticky top-0 z-20 flex items-center justify-between border-b border-[var(--pitch-line)] bg-[var(--pitch-950)]/90 backdrop-blur px-4 py-3">
        <Logo />
      </header>

      <main className="flex-1 px-4 py-5 md:px-8 md:py-8 pb-24 md:pb-8 max-w-5xl w-full mx-auto">
        <Outlet />
      </main>

      {/* Mobile bottom nav — this is where deadline-day decisions get checked */}
      <nav className="md:hidden fixed bottom-0 inset-x-0 z-20 border-t border-[var(--pitch-line)] bg-[var(--pitch-950)]/95 backdrop-blur">
        <div className="grid grid-cols-6">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex flex-col items-center gap-0.5 py-2.5 text-[10px] font-medium ${
                  isActive ? 'text-emerald-300' : 'text-[var(--ink-500)]'
                }`
              }
            >
              <Icon className="h-5 w-5" />
              {label}
            </NavLink>
          ))}
        </div>
      </nav>
    </div>
  );
}

export function PageHeader({ title, subtitle, right }: { title: string; subtitle?: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex items-start justify-between mb-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-[var(--ink-100)]">{title}</h1>
        {subtitle && <p className="text-sm text-[var(--ink-500)] mt-1">{subtitle}</p>}
      </div>
      {right}
    </div>
  );
}
