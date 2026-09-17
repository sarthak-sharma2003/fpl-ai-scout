import { useAllData } from "./lib/useData";
import { useHashRoute } from "./lib/useHashRoute";
import { useTheme } from "./lib/useTheme";
import { TopBar } from "./components/TopBar";
import { LoadingScreen, ErrorScreen } from "./components/StatusScreens";
import { NowView } from "./views/NowView";
import { PlanView } from "./views/PlanView";
import { PlayersView } from "./views/PlayersView";
import { TeamsView } from "./views/TeamsView";
import { formatDateTime } from "./lib/format";

export default function App() {
  const state = useAllData();
  const [view, navigate] = useHashRoute();
  const [theme, setTheme] = useTheme();

  if (state.status === "loading") {
    return <LoadingScreen />;
  }

  if (state.status === "error") {
    return <ErrorScreen message={state.message} onRetry={() => window.location.reload()} />;
  }

  const { meta, squad, plan, players, teams } = state.data;

  return (
    <div className="app-shell">
      <a href="#main" className="visually-hidden-focusable">
        Skip to content
      </a>
      <TopBar meta={meta} view={view} onNavigate={navigate} theme={theme} onThemeChange={setTheme} />
      <main id="main" className="view">
        {view === "now" && <NowView squad={squad} />}
        {view === "plan" && <PlanView plan={plan} />}
        {view === "players" && <PlayersView players={players} />}
        {view === "teams" && <TeamsView teams={teams} />}
      </main>
      <footer className="footer">
        UCL Scout · Season {meta.season} · generated {formatDateTime(meta.generatedAt)}
      </footer>
    </div>
  );
}
