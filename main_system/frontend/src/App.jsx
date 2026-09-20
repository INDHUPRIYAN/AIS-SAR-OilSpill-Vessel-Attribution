/* App: theme, session, shell state, and the routed pages.
 *
 * The nav and the command palette are both built from `ROUTES` in lib/shell,
 * so "⌘K reaches every screen" holds by construction rather than by anyone
 * remembering to add a screen twice. The shell chrome itself lives in
 * components/shell. */

import { Navigate, Route, Routes } from "react-router-dom";
import { MotionConfig } from "framer-motion";
import { Waves } from "lucide-react";

import Operations from "./pages/Operations";
import Satellite from "./pages/Satellite";
import SarDatabase from "./pages/SarDatabase";
import Environment from "./pages/Environment";
import Reports from "./pages/Reports";
import Models from "./pages/Models";
import SystemOps from "./pages/SystemOps";
import Audit from "./pages/Audit";
import HindcastEngines from "./pages/HindcastEngines";
import GlobeViewPage from "./pages/GlobeView";
import OfficerDashboard from "./pages/OfficerDashboard";
import OfficersPage from "./pages/Officers";
import ZonesPage from "./pages/Zones";
import Incident from "./pages/Incident";
import Investigation from "./pages/Investigation";
import Investigations from "./pages/Investigations";
import Monitoring from "./pages/Monitoring";
import Catalog from "./pages/Catalog";
import Alerts from "./pages/Alerts";
import Keys from "./pages/Keys";
import Dashboard from "./pages/Dashboard";
import Analytics from "./pages/Analytics";
import About from "./pages/About";
import Report from "./pages/Report";
import Incidents from "./pages/Incidents";
import Vessels from "./pages/Vessels";
import SignIn from "./pages/SignIn";
import CommandPalette, { ShortcutOverlay } from "./components/CommandPalette";
import AppShell from "./components/shell/AppShell";
import { SessionProvider, useSession } from "./lib/session";
import { ShellProvider } from "./lib/shell";
import { ThemeProvider } from "./lib/theme";
import "./incident.css";
import "./workspace.css";
import "./workspace-panels.css";

function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Operations />} />
        <Route path="/globe" element={<GlobeViewPage />} />
        <Route path="/my-desk" element={<OfficerDashboard />} />
        <Route path="/zones" element={<ZonesPage />} />
        <Route path="/officers" element={<OfficersPage />} />
        <Route path="/incident" element={<Incident />} />
        <Route path="/incidents" element={<Incidents />} />
        <Route path="/vessels" element={<Vessels />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/about" element={<About />} />
        <Route path="/investigation" element={<Investigation />} />
        <Route path="/investigations" element={<Investigations />} />
        <Route path="/report" element={<Report />} />
        <Route path="/monitoring" element={<Monitoring />} />
        <Route path="/catalog" element={<Catalog />} />
        <Route path="/alerts" element={<Alerts />} />
        <Route path="/keys" element={<Keys />} />
        <Route path="/satellite" element={<Satellite />} />
        <Route path="/sar-database" element={<SarDatabase />} />
        <Route path="/environment" element={<Environment />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/models" element={<Models />} />
        <Route path="/system" element={<SystemOps />} />
        <Route path="/audit" element={<Audit />} />
        <Route path="/hindcast" element={<HindcastEngines />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
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
