/* App shell: navigation, live system pulse, theme, and the routed pages. */

import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import {
  Activity, BarChart3, BookOpen, Film, FolderOpen, KeyRound, Moon, Radar,
  Sun, Waves,
} from "lucide-react";

import Incident from "./pages/Incident";
import Investigation from "./pages/Investigation";
import Monitoring from "./pages/Monitoring";
import Keys from "./pages/Keys";
import Dashboard from "./pages/Dashboard";
import Analytics from "./pages/Analytics";
import About from "./pages/About";
import { Dot } from "./components/ui";
import { api, useApi } from "./lib/api";
import "./incident.css";
import "./workspace.css";

const THEME_KEY = "oceantrace.theme";

export default function App() {
  // A quiet health pulse in the top bar. A judge should be able to see the
  // system is alive without navigating anywhere.
  const { data: status } = useApi(() => api.apiStatus(), [], { interval: 20000 });
  const providers = status?.providers || [];
  const working = providers.filter((p) => p.status === "WORKING").length;
  const healthy = providers.length > 0 && working === providers.length;

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
          <NavLink to="/incident"><Film size={14} /> Incident Replay</NavLink>
          <NavLink to="/dashboard"><FolderOpen size={14} /> Investigations</NavLink>
          <NavLink to="/investigation"><Radar size={14} /> Workspace</NavLink>
          <NavLink to="/analytics"><BarChart3 size={14} /> Analytics</NavLink>
          <NavLink to="/monitoring"><Activity size={14} /> Monitoring</NavLink>
          <NavLink to="/keys"><KeyRound size={14} /> Keys</NavLink>
          <NavLink to="/about"><BookOpen size={14} /> About</NavLink>
        </nav>

        <div className="topbar-right">
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
          <span className="tiny muted">SIH 2026 · PS26143</span>
        </div>
      </header>

      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/incident" replace />} />
          <Route path="/incident" element={<Incident />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/about" element={<About />} />
          <Route path="/investigation" element={<Investigation />} />
          <Route path="/monitoring" element={<Monitoring />} />
          <Route path="/keys" element={<Keys />} />
        </Routes>
      </main>
    </div>
  );
}
