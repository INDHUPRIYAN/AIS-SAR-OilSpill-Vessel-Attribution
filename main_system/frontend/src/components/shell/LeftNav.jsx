/* Persistent left navigation: a narrow icon rail, generated from ROUTES so a
 * screen cannot exist that the rail does not know about.
 *
 * Ten primary entries are shown by their `rail` label; every other route is
 * one click away in the "More" flyout, grouped by section. Role-aware -- an
 * entry the server would 403 is not shown, but every route remains reachable
 * by URL and from the palette. Collapsing hides the labels, not the rail. */

import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import * as Icons from "lucide-react";

import { useSession } from "../../lib/session";
import { NAV_SECTIONS, RAIL_ORDER, ROUTES, routeFor } from "../../lib/shell";
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

export default function LeftNav({ collapsed, alertsSummary }) {
  const { user } = useSession();
  const location = useLocation();
  const role = user?.role;
  const openAlerts = alertsSummary?.open ?? 0;
  const critical = alertsSummary?.by_severity?.critical ?? 0;
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef(null);

  const visible = ROUTES.filter((r) => r.nav && (!r.roles || r.roles.includes(role)));
  const rail = RAIL_ORDER.map((label) => visible.find((r) => r.rail === label)).filter(Boolean);
  const railTo = new Set(rail.map((r) => r.to));
  const rest = visible.filter((r) => !railTo.has(r.to));
  const current = routeFor(location.pathname);
  const restIsActive = current && rest.some((r) => r.to === current.to);

  useEffect(() => {
    if (!moreOpen) return undefined;
    const onDown = (e) => { if (!moreRef.current?.contains(e.target)) setMoreOpen(false); };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [moreOpen]);
  useEffect(() => { setMoreOpen(false); }, [location.pathname]);

  return (
    <nav className={`nav ${collapsed ? "nav-collapsed" : ""}`} aria-label="Primary"
      data-testid="left-nav">
      <div className="nav-scroll">
        {rail.map((r) => {
          const Icon = Icons[r.icon] || Icons.Radar;
          const badge = r.to === "/alerts" && openAlerts > 0 ? openAlerts : null;
          return (
            <NavLink key={r.to} to={r.to} end={r.to === "/"} title={r.label}
              className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
              data-testid={`nav-${r.to.replace("/", "") || "home"}`}>
              <Icon size={20} strokeWidth={1.75} />
              <span className="nav-item-label">{r.rail}</span>
              {badge != null && (
                <span className={`nav-item-badge ${critical ? "crit" : "warn"}`}>{badge}</span>
              )}
            </NavLink>
          );
        })}

        {/* Everything else: alerts, replay, desk, satellite, monitors… */}
        <div className="nav-more-wrap" ref={moreRef}>
          <button className={`nav-item nav-more ${restIsActive ? "active" : ""} ${moreOpen ? "open" : ""}`}
            onClick={() => setMoreOpen((o) => !o)} title="More screens"
            aria-haspopup="menu" aria-expanded={moreOpen} data-testid="nav-more">
            <Icons.LayoutGrid size={20} strokeWidth={1.75} />
            <span className="nav-item-label">More</span>
            {openAlerts > 0 && (
              <span className={`nav-item-badge ${critical ? "crit" : "warn"}`}>{openAlerts}</span>
            )}
          </button>
          {moreOpen && (
            <div className="nav-flyout" role="menu" data-testid="nav-flyout">
              {NAV_SECTIONS.map((section) => {
                const items = rest.filter((r) => r.section === section);
                if (!items.length) return null;
                return (
                  <div className="nav-section" key={section}>
                    <div className="nav-section-label">{section}</div>
                    {items.map((r) => {
                      const Icon = Icons[r.icon] || Icons.Radar;
                      const badge = r.to === "/alerts" && openAlerts > 0 ? openAlerts : null;
                      return (
                        <NavLink key={r.to} to={r.to} end={r.to === "/"} title={r.hint || r.label}
                          className={({ isActive }) => `nav-fly-item ${isActive ? "active" : ""}`}
                          data-testid={`nav-${r.to.replace("/", "") || "home"}`}>
                          <Icon size={14} />
                          <span>{r.label}</span>
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
          )}
        </div>
      </div>
      <div className="nav-role" title={user?.email}>
        <span className="nav-role-k">Role</span>
        <span className="nav-role-v">{roleLabel(role)}</span>
      </div>
    </nav>
  );
}
