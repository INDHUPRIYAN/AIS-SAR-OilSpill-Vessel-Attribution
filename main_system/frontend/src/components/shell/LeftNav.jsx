/* Persistent left navigation, generated from ROUTES so a screen cannot exist
 * that the nav does not know about. Grouped by section, collapsible to an
 * icon rail, and role-aware -- an entry the server would 403 is not shown,
 * but every route remains reachable by URL and from the palette. */

import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import * as Icons from "lucide-react";

import { useSession } from "../../lib/session";
import { NAV_SECTIONS, ROUTES } from "../../lib/shell";
import { roleLabel } from "./TopHeader";

const NAV_KEY = "oceantrace.nav";

function readCollapsed() {
  try { return localStorage.getItem(NAV_KEY) === "collapsed"; } catch { return false; }
}

export function useNavCollapsed() {
  const [collapsed, setCollapsed] = useState(readCollapsed);
  useEffect(() => {
    try { localStorage.setItem(NAV_KEY, collapsed ? "collapsed" : "open"); } catch { /* ignore */ }
  }, [collapsed]);
  return [collapsed, setCollapsed];
}

export default function LeftNav({ collapsed, onToggle, alertsSummary }) {
  const { user } = useSession();
  const role = user?.role;
  const openAlerts = alertsSummary?.open ?? 0;
  const critical = alertsSummary?.by_severity?.critical ?? 0;

  const visible = ROUTES.filter((r) => r.nav && (!r.roles || r.roles.includes(role)));

  return (
    <nav className={`nav ${collapsed ? "nav-collapsed" : ""}`} aria-label="Primary"
      data-testid="left-nav">
      <div className="nav-scroll">
        {NAV_SECTIONS.map((section) => {
          const items = visible.filter((r) => r.section === section);
          if (!items.length) return null;
          return (
            <div className="nav-section" key={section}>
              <div className="nav-section-label">{section}</div>
              {items.map((r) => {
                const Icon = Icons[r.icon] || Icons.Radar;
                const badge = r.to === "/alerts" && openAlerts > 0 ? openAlerts : null;
                return (
                  <NavLink key={r.to} to={r.to} end={r.to === "/"} title={r.label}
                    className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
                    data-testid={`nav-${r.to.replace("/", "") || "home"}`}>
                    <Icon size={15} />
                    <span className="nav-item-label">{r.label}</span>
                    {badge != null && (
                      <span className={`nav-item-badge ${critical ? "crit" : "warn"}`}>{badge}</span>
                    )}
                  </NavLink>
                );
              })}
            </div>
          );
        })}
      </div>
      <div className="nav-role" title={user?.email}>
        <span className="nav-role-k">Signed in as</span>
        <span className="nav-role-v">{roleLabel(role)}</span>
      </div>
      <div className="nav-foot">
        <button className="btn btn-ghost btn-sm nav-collapse-btn" onClick={onToggle}
          title={collapsed ? "Expand navigation" : "Collapse navigation"} data-testid="nav-toggle">
          {collapsed ? <Icons.PanelLeftOpen size={14} /> : <Icons.PanelLeftClose size={14} />}
          <span>Collapse</span>
        </button>
      </div>
    </nav>
  );
}
