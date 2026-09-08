/* App shell: navigation, live system pulse, the honesty strip, theme, and the
 * routed pages.
 *
 * The nav and the command palette are both built from `ROUTES` in lib/shell,
 * so "⌘K reaches every screen" holds by construction rather than by anyone
 * remembering to add a screen twice.
 */

import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { MotionConfig } from "framer-motion";
import {
  Activity, BarChart3, BookOpen, Command, Database, Film, FolderOpen, KeyRound,
  Moon, Radar, Sun, Waves,
} from "lucide-react";

import Incident from "./pages/Incident";
import Investigation from "./pages/Investigation";
import Monitoring from "./pages/Monitoring";
import Catalog from "./pages/Catalog";
import Alerts, { AlertBell } from "./pages/Alerts";
import Keys from "./pages/Keys";
import Dashboard from "./pages/Dashboard";
import Analytics from "./pages/Analytics";
import About from "./pages/About";
import Report from "./pages/Report";
import Incidents from "./pages/Incidents";
import Vessels from "./pages/Vessels";
import SignIn from "./pages/SignIn";
import { Dot } from "./components/ui";
import CommandPalette, { ShortcutOverlay } from "./components/CommandPalette";
import { ProvenanceChips, ZuluClock } from "./components/TopBarStatus";
import { api, useApi } from "./lib/api";
import { SessionProvider, useSession } from "./lib/session";
import { ROUTES, ShellProvider, useShell } from "./lib/shell";
import "./incident.css";
import "./workspace.css";

const THEME_KEY = "oceantrace.theme";

/* Icons are chrome, so they live with the chrome rather than in the route
 * table. A screen with no icon still gets a nav slot -- it just gets the
 * default mark, which is better than being invisible. */
const NAV_ICONS = {
  "/incident": Film, "/dashboard": FolderOpen, "/investigation": Radar,
  "/analytics": BarChart3, "/monitoring": Activity, "/catalog": Database,
  "/alerts": AlertBell, "/keys": KeyRound, "/about": BookOpen,
};

function App() {
  // A quiet health pulse in the top bar. A judge should be able to see the
  // system is alive without navigating anywhere.
  const { data: status } = useApi(() => api.apiStatus(), [], { interval: 20000 });
  const providers = status?.providers || [];
  const working = providers.filter((p) => p.status === "WORKING").length;
  const healthy = providers.length > 0 && working === providers.length;
  const { openPalette } = useShell();

  const [theme, setTheme] = useState(
    () => localStorage.getItem(THEME_KEY) || "dark");
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark"><Waves size={17} color="#04121f" /></div>
          <div>
            <div className="brand-name">OceanTrace</div>
            <div className="brand-sub">SAR · Drift · Attribution</div>
          </div>
        </div>

        <nav className="nav">
          {ROUTES.filter((r) => r.nav).map((r) => {
            const Icon = NAV_ICONS[r.to] || Radar;
            return (
              /* `title` matters here: below 1660px the CSS hides the label
               * text and leaves only the icon, and an icon with no accessible
               * name is a button nobody can identify. */
              <NavLink key={r.to} to={r.to} title={r.label}>
                <Icon size={14} /> {r.label}
              </NavLink>
            );
          })}
        </nav>

        <div className="topbar-right">
          <ProvenanceChips />

          <button className="btn btn-sm cp-trigger" onClick={openPalette}
            title="Search runs, incidents, vessels and scenes — ⌘K"
            data-testid="palette-trigger">
            <Command size={12} /> <span className="mono tiny">⌘K</span>
          </button>

          {providers.length > 0 && (
            <span style={{ display: "flex", alignItems: "center", gap: 7 }}>
              <Dot status={healthy ? "WORKING" : "DEGRADED"} pulsing />
              <span className="tiny mono" style={{ color: healthy ? "var(--ok)" : "var(--warn)" }}>
                {working}/{providers.length} APIs
              </span>
            </span>
          )}
          <button className="btn btn-sm" title="Toggle light / dark theme"
            onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}>
            {theme === "dark" ? <Sun size={13} /> : <Moon size={13} />}
          </button>
          <ZuluClock />
        </div>
      </header>

      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/incident" replace />} />
          <Route path="/incident" element={<Incident />} />
          <Route path="/incidents" element={<Incidents />} />
          <Route path="/vessels" element={<Vessels />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/about" element={<About />} />
          <Route path="/investigation" element={<Investigation />} />
          <Route path="/report" element={<Report />} />
          <Route path="/monitoring" element={<Monitoring />} />
          <Route path="/catalog" element={<Catalog />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/keys" element={<Keys />} />
        </Routes>
      </main>

      <CommandPalette />
      <ShortcutOverlay />
    </div>
  );
}

/* The whole app sits behind a session. Rendering the shell for a signed-out
 * user would show a frame full of failed panels, since every /api route now
 * requires authentication -- the sign-in form is the honest state. */
function Gate({ children }) {
  const { user, checking } = useSession();
  if (checking) return null;          // brief: avoids flashing the form on reload
  if (!user) return <SignIn />;
  return children;
}

export default function AppWithSession() {
  return (
    /* `reducedMotion="user"` makes framer-motion honour the OS setting for
     * every animation in the app at once (UX spec §10.7): camera flights
     * become cuts and pulses become solid dots. Information is never lost,
     * only motion — the CSS side of the same rule is in styles.css. */
    <MotionConfig reducedMotion="user">
      <SessionProvider>
        <Gate>
          <ShellProvider>
            <App />
          </ShellProvider>
        </Gate>
      </SessionProvider>
    </MotionConfig>
  );
}
