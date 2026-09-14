/* The global header: identity, where you are, what run you are looking at,
 * the search box, the live pulse, alerts, theme and who you are.
 *
 * Nothing in this bar is decorative. The pulse is the measured provider
 * sweep; the bell count comes from the server's own alert summary so the
 * badge and the queue cannot disagree; the provenance strip describes the run
 * in context and nothing else. */

import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  Bell, ChevronDown, Inbox, LogOut, Moon, Search, ShieldCheck, Sun, Waves,
} from "lucide-react";

import { useSession } from "../../lib/session";
import { routeFor, useShell } from "../../lib/shell";
import { useTheme } from "../../lib/theme";
import { ProvenanceChips, ZuluClock } from "../TopBarStatus";
import { Kbd, LiveIndicator } from "../ui";

const ROLE_LABEL = {
  super_admin: "Super Admin", admin: "Admin", investigator: "Investigator",
  analyst: "Analyst", reviewer: "Reviewer", auditor: "Auditor", zone_officer: "Zone Officer",
};

export function roleLabel(role) {
  return ROLE_LABEL[role] || role || "—";
}

function initials(user) {
  const name = user?.display_name || user?.email || "";
  const parts = name.replace(/@.*/, "").split(/[\s._-]+/).filter(Boolean);
  return (parts.length >= 2 ? parts[0][0] + parts[1][0] : name.slice(0, 2)).toUpperCase() || "OT";
}

/** Derives the LIVE indicator from measured state: every provider working and
 *  the AIS stream functionally working → LIVE; some providers working →
 *  DEGRADED; none → OFFLINE; nothing measured yet → idle. */
export function systemPulse(status, ais) {
  const providers = status?.providers || [];
  if (!providers.length) return { tone: "idle", label: "NO PULSE", title: "No provider probe has reported yet." };
  const working = providers.filter((p) => p.status === "WORKING").length;
  const stream = ais?.stream;
  const streamOk = stream?.functionally_working;
  const title = `${working}/${providers.length} providers WORKING`
    + (stream ? ` · AIS stream ${streamOk ? "functionally working" : stream.state}` : "");
  if (working === 0) return { tone: "danger", label: "OFFLINE", title };
  if (working === providers.length && (streamOk || !stream || stream.state === "not_configured")) {
    return { tone: "ok", label: "LIVE", title };
  }
  return { tone: "warn", label: "DEGRADED", title };
}

export default function TopHeader({ status, ais, alertsSummary }) {
  const { user, signOut } = useSession();
  const { openPalette } = useShell();
  const { theme, toggle } = useTheme();
  const location = useLocation();
  const navigate = useNavigate();
  const route = routeFor(location.pathname);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onDown = (e) => { if (!menuRef.current?.contains(e.target)) setMenuOpen(false); };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [menuOpen]);

  const pulse = systemPulse(status, ais);
  const open = alertsSummary?.open ?? 0;
  const critical = alertsSummary?.by_severity?.critical ?? 0;

  return (
    <header className="hdr" data-testid="top-header">
      <Link className="brand" to="/" title="OceanTrace — overview">
        <div className="brand-mark"><Waves size={16} /></div>
        <div>
          <div className="brand-name">OCEAN<b>TRACE</b></div>
          <div className="brand-sub">Track · Detect · Trace · Investigate · Attribute · Protect</div>
        </div>
      </Link>

      <span className="hdr-sep" />

      <div className="hdr-ctx" data-testid="header-context">
        <span className="hdr-ctx-section">{route?.section || "OceanTrace"}</span>
        <span className="hdr-ctx-title">{route?.label || "—"}</span>
      </div>

      <div className="hdr-mid">
        <ProvenanceChips />
      </div>

      <div className="hdr-right">
        <button className="hdr-search" onClick={openPalette} data-testid="palette-trigger"
          title="Search runs, incidents, vessels and scenes — ⌘K">
          <Search size={13} />
          <span className="hint">Search vessel, incident, run, scene…</span>
          <Kbd>⌘K</Kbd>
        </button>

        <LiveIndicator tone={pulse.tone} label={pulse.label} title={pulse.title} />

        <button className={`hdr-btn ${open ? "on" : ""}`} onClick={() => navigate("/alerts")}
          title={open ? `${open} open alert(s)` : "no open alerts"} data-testid="alert-bell">
          <Bell size={15} />
          {open > 0 && <span className={`hdr-count ${critical ? "crit" : ""}`}>{open}</span>}
        </button>

        <button className="hdr-btn" onClick={toggle} data-testid="theme-toggle"
          title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}>
          {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
        </button>

        <ZuluClock />

        <div className="hdr-menu-wrap" ref={menuRef}>
          <button className="hdr-user" onClick={() => setMenuOpen((o) => !o)}
            aria-haspopup="menu" aria-expanded={menuOpen} data-testid="user-menu">
            <span className="avatar">{initials(user)}</span>
            <span className="hdr-user-text">
              <span className="hdr-user-role">{roleLabel(user?.role)}</span>
              <span className="hdr-user-name">{user?.display_name || user?.email}</span>
            </span>
            <ChevronDown size={12} />
          </button>
          {menuOpen && (
            <div className="hdr-menu" role="menu">
              <div className="hdr-menu-head">
                <div className="strong ellipsis">{user?.display_name || user?.email}</div>
                <div className="tiny mono muted ellipsis">{user?.email}</div>
                <div className="tiny mt-1" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <ShieldCheck size={11} color="var(--accent)" />
                  <span className="label">{roleLabel(user?.role)}</span>
                </div>
              </div>
              <Link className="hdr-menu-row" to="/my-desk" onClick={() => setMenuOpen(false)}>
                <Inbox size={13} /> My desk
              </Link>
              <button className="hdr-menu-row" onClick={toggle}>
                {theme === "dark" ? <Sun size={13} /> : <Moon size={13} />}
                {theme === "dark" ? "Light theme" : "Dark theme"}
              </button>
              <button className="hdr-menu-row" onClick={signOut} data-testid="sign-out">
                <LogOut size={13} /> Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
