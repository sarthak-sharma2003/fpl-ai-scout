import { Countdown } from "./Countdown";
import { MoonIcon, SunIcon } from "./Icons";
import { resolvedTheme, type Theme } from "../lib/useTheme";
import { relativeTo, hoursSince, formatDateTime } from "../lib/format";
import type { Meta } from "../types";
import { VIEWS, type View } from "../lib/useHashRoute";

const STALE_HOURS = 36;
const NAV_LABEL: Record<View, string> = {
  now: "Now",
  plan: "Plan",
  players: "Players",
  teams: "Teams",
};

export function TopBar({
  meta,
  view,
  onNavigate,
  theme,
  onThemeChange,
}: {
  meta: Meta;
  view: View;
  onNavigate: (v: View) => void;
  theme: Theme | null;
  onThemeChange: (t: Theme | null) => void;
}) {
  const stale = hoursSince(meta.generatedAt, new Date()) > STALE_HOURS;
  const resolved = resolvedTheme(theme);

  return (
    <div className="topbar">
      <div className="topbar__row">
        <span className="brand">
          <span className="brand__mark">UCL</span> <span className="brand__full">Scout</span>
        </span>
        <a href="/fpl-ai-scout/" className="nav__tab">
          FPL
        </a>
        <div className="topbar__spacer" />
        <Countdown deadlineIso={meta.deadline} matchday={meta.matchday} />
        <button
          type="button"
          className="theme-toggle"
          onClick={() => onThemeChange(resolved === "dark" ? "light" : "dark")}
          aria-label={resolved === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        >
          {resolved === "dark" ? <SunIcon /> : <MoonIcon />}
        </button>
      </div>
      <div className="meta-strip">
        <span
          className={stale ? "staleness staleness--warn" : "staleness"}
          title={`Data generated ${formatDateTime(meta.generatedAt)}`}
        >
          {stale ? "STALE — " : ""}data as of {relativeTo(meta.generatedAt, new Date())}
        </span>
        <span className="staleness">
          Season {meta.season} · MD{meta.matchday} of {meta.lastMatchday}
        </span>
      </div>
      <nav className="nav" aria-label="Main">
        <ul className="nav__list">
          {VIEWS.map((v) => (
            <li key={v}>
              <button
                type="button"
                className="nav__tab"
                aria-current={view === v ? "page" : undefined}
                onClick={() => onNavigate(v)}
              >
                {NAV_LABEL[v]}
              </button>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}
