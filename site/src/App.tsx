import { HashRouter, Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import Dashboard from './pages/Dashboard';
import Transfers from './pages/Transfers';
import Fixtures from './pages/Fixtures';
import Signals from './pages/Signals';
import Analytics from './pages/Analytics';
import Rules from './pages/Rules';

// HashRouter (not BrowserRouter): on GitHub Pages there's no server-side
// fallback, so a refresh or deep-link to /transfers would 404. Hash routes
// (/#/transfers) are served by the single index.html and resolved entirely
// client-side — deep links and refreshes just work, no 404.html redirect hack.
// Asset/data fetches still use import.meta.env.BASE_URL (the /fpl-ai-scout/
// project path), which is unaffected by the hash.
export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="transfers" element={<Transfers />} />
          <Route path="fixtures" element={<Fixtures />} />
          <Route path="signals" element={<Signals />} />
          <Route path="analytics" element={<Analytics />} />
          <Route path="rules" element={<Rules />} />
        </Route>
      </Routes>
    </HashRouter>
  );
}
