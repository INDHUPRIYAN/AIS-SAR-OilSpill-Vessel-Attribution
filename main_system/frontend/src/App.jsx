/* App: theme, session, shell state, and the routed pages.
 *
 * The router, the nav and the command palette are all built from `ROUTES` in lib/shell,
 * so "⌘K reaches every screen" holds by construction rather than by anyone
 * remembering to add a screen twice. The shell chrome itself lives in
 * components/shell. */

import { Suspense, lazy } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { MotionConfig } from "framer-motion";
import { Waves } from "lucide-react";

/* Each page is its own chunk: the shell and the sign-in form load first, and
 * a map-heavy page (the workspace, the Live Map) is fetched when it is opened
 * rather than on every visit to the Reports list. */
const Operations = lazy(() => import("./pages/Operations"));
const Satellite = lazy(() => import("./pages/Satellite"));
const SarDatabase = lazy(() => import("./pages/SarDatabase"));
const Environment = lazy(() => import("./pages/Environment"));
const Reports = lazy(() => import("./pages/Reports"));
const Models = lazy(() => import("./pages/Models"));
const SystemOps = lazy(() => import("./pages/SystemOps"));
const Audit = lazy(() => import("./pages/Audit"));
const HindcastEngines = lazy(() => import("./pages/HindcastEngines"));
const GlobeViewPage = lazy(() => import("./pages/GlobeView"));
const OfficerDashboard = lazy(() => import("./pages/OfficerDashboard"));
const OfficersPage = lazy(() => import("./pages/Officers"));
const ZonesPage = lazy(() => import("./pages/Zones"));
const Incident = lazy(() => import("./pages/Incident"));
const Investigation = lazy(() => import("./pages/Investigation"));
const Investigations = lazy(() => import("./pages/Investigations"));
const Monitoring = lazy(() => import("./pages/Monitoring"));
const Catalog = lazy(() => import("./pages/Catalog"));
const Alerts = lazy(() => import("./pages/Alerts"));
const Keys = lazy(() => import("./pages/Keys"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Analytics = lazy(() => import("./pages/Analytics"));
const About = lazy(() => import("./pages/About"));
const Report = lazy(() => import("./pages/Report"));
const Incidents = lazy(() => import("./pages/Incidents"));
const Vessels = lazy(() => import("./pages/Vessels"));
import SignIn from "./pages/SignIn";
import NotFound from "./pages/NotFound";
import CommandPalette, { ShortcutOverlay } from "./components/CommandPalette";
import AppShell from "./components/shell/AppShell";
import { SessionProvider, useSession } from "./lib/session";
import { ROUTES, ShellProvider, pathsOf } from "./lib/shell";
import { LEGACY_PATHS, canonical } from "./lib/urls";
import { ThemeProvider } from "./lib/theme";
import "./incident.css";
import "./workspace.css";
import "./workspace-panels.css";

/* One page per ROUTES id. The router below is generated from ROUTES, so a
 * screen the shell lists always has a route and a route always has a name;
 * tests-unit/routes.test.jsx holds the two lists to each other. */
export const PAGES = {
  dashboard: Operations,
  investigations: Investigations,
  workspace: Investigation,
  registry: Dashboard,
  map: GlobeViewPage,
  detections: SarDatabase,
  "scene-viewer": Satellite,
  vessels: Vessels,
  reports: Reports,
  "report-print": Report,
  desk: OfficerDashboard,
  incidents: Incidents,
  alerts: Alerts,
  replay: Incident,
  engines: HindcastEngines,
  "data-sources": Catalog,
  "api-monitor": Monitoring,
  zones: ZonesPage,
  health: SystemOps,
  models: Models,
  analytics: Analytics,
  environment: Environment,
  audit: Audit,
  users: OfficersPage,
  credentials: Keys,
  help: About,
};

/* An address that worked once keeps working: every legacy path resolves
 * through lib/urls `canonical()` and replaces itself in history, so Back
 * does not bounce off the redirect. */
function LegacyRedirect() {
  const location = useLocation();
  const to = canonical(location.pathname, location.search);
  return to ? <Navigate to={`${to}${location.hash}`} replace /> : <NotFound />;
}

/* `/vessels?mmsi=` predates `/vessels/:mmsi`. The path is live, so this one
 * legacy shape is folded in front of the page rather than routed. */
function VesselsRoute() {
  const location = useLocation();
  const to = canonical(location.pathname, location.search);
  return to ? <Navigate to={to} replace /> : <Vessels />;
}

/* While a page's chunk arrives. Quiet and brief: it is a network fetch of
 * code, not work the system is doing, so it claims nothing. */
function PageLoading() {
  return (
    <div className="page-loading" role="status" aria-live="polite" data-testid="page-loading">
      <span className="spinner" /> <span className="tiny muted">Loading</span>
    </div>
  );
}

function App() {
  return (
    <AppShell>
      <Suspense fallback={<PageLoading />}>
      <Routes>
        {ROUTES.flatMap((r) => {
          const Page = r.id === "vessels" ? VesselsRoute : PAGES[r.id];
          return pathsOf(r).map((path) => <Route key={`${r.id}:${path}`} path={path} element={<Page />} />);
        })}
        {LEGACY_PATHS.map((path) => <Route key={path} path={path} element={<LegacyRedirect />} />)}
        <Route path="*" element={<NotFound />} />
      </Routes>
      </Suspense>
      <CommandPalette />
      <ShortcutOverlay />
    </AppShell>
  );
}

/* A brief boot frame while /auth/me resolves. Deliberately quiet: showing the
 * sign-in form here would flash it at every signed-in reload. */
function Booting() {
  return (
    <div style={{ height: "100vh", display: "grid", placeItems: "center", background: "var(--bg-0)" }}>
      <div className="row" style={{ gap: 12, color: "var(--ink-2)" }}>
        <div className="brand-mark"><Waves size={16} /></div>
        <span className="mono tiny">OCEANTRACE · establishing session</span>
        <span className="spinner" />
      </div>
    </div>
  );
}

/* The whole app sits behind a session. Rendering the shell for a signed-out
 * user would show a frame full of failed panels, since every /api route
 * requires authentication -- the sign-in form is the honest state. */
function Gate({ children }) {
  const { user, checking, loginOpen } = useSession();
  if (checking) return <Booting />;
  // The public evaluator view opens the form on request (the Login button);
  // a production deployment opens it whenever there is no session.
  if (!user || loginOpen) return <SignIn />;
  return children;
}

export default function AppWithSession() {
  return (
    /* `reducedMotion="user"` makes framer-motion honour the OS setting for
     * every animation in the app at once: camera flights become cuts and
     * pulses become solid dots. Information is never lost, only motion. */
    <MotionConfig reducedMotion="user">
      <ThemeProvider>
        <SessionProvider>
          <Gate>
            <ShellProvider>
              <App />
            </ShellProvider>
          </Gate>
        </SessionProvider>
      </ThemeProvider>
    </MotionConfig>
  );
}
