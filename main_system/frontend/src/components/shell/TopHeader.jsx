/* The global header: identity, where you are (breadcrumbs), what run you are looking at,
 * the search box, the live pulse, alerts, theme and who you are.
 *
 * Nothing in this bar is decorative. The pulse is the measured provider
 * sweep; the bell count comes from the server's own alert summary so the
 * badge and the queue cannot disagree; the provenance strip describes the run
 * in context and nothing else. */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ChevronDown, Eye, Inbox, LogIn, LogOut, Menu, Moon, Search, ShieldCheck, Sun, Waves,
} from "lucide-react";

import { useSession } from "../../lib/session";
import { useShell } from "../../lib/shell";
import { url } from "../../lib/urls";
import { useTheme } from "../../lib/theme";
import { ProvenanceChips, ZuluClock } from "../TopBarStatus";
import { Kbd, LiveIndicator } from "../ui";
import Breadcrumbs from "./Breadcrumbs";
import Notifications from "./Notifications";

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

/** Derives the indicator from measured state, over the providers that are
 *  actually DEPLOYED (an adapter nothing consumes cannot be "down").
 *
 *    any provider FAILED / UNCONFIGURED / DEGRADED   -> DEGRADED, naming them
 *    every one up, all verified, stream working      -> LIVE
 *    every one up (verified or merely reachable)     -> OPERATIONAL
 *
 *  REACHABLE is not a fault: it is the most a no-key provider can ever prove
 *  from a ping. It used to count against the system, so a host with nothing
 *  wrong read "DEGRADED 4/12". The tooltip still says how many are verified. */
export function providerTally(status) {
  const all = status?.providers || [];
  const deployed = all.filter((p) => p.status !== "NOT_DEPLOYED");
  const working = deployed.filter((p) => p.status === "WORKING");
  const reachable = deployed.filter((p) => p.status === "REACHABLE");
  const down = deployed.filter((p) => p.status !== "WORKING" && p.status !== "REACHABLE");
  return { all, deployed, working, reachable, down, notDeployed: all.length - deployed.length };
}
export function systemPulse(status, ais) {
  const t = providerTally(status);
  if (!t.all.length) return { tone: "idle", label: "NO PULSE", title: "No provider probe has reported yet." };
  const stream = ais?.stream;
  const streamOk = stream?.functionally_working;
  const title = `${t.deployed.length - t.down.length}/${t.deployed.length} deployed providers up: ${t.working.length} verified by a real request, ${t.reachable.length} reachable`
    + (t.down.length ? ` · not up: ${t.down.map((p) => `${p.provider || p.name} ${p.status}`).join(", ")}` : "")
    + (t.notDeployed ? ` · ${t.notDeployed} not deployed` : "")
    + (stream ? ` · AIS stream ${streamOk ? "functionally working" : stream.state}` : "");
  if (t.deployed.length && t.down.length === t.deployed.length) return { tone: "danger", label: "OFFLINE", title };
  if (t.down.length) return { tone: "warn", label: "DEGRADED", title };
  if (t.reachable.length === 0 && (streamOk || !stream || stream.state === "not_configured")) return { tone: "ok", label: "LIVE", title };
  return { tone: "ok", label: "OPERATIONAL", title };
}

export default function TopHeader({ status, ais, alertsSummary, onToggleNav }) {
  const { user, signOut, isEvaluator, openLogin } = useSession();
  const { openPalette } = useShell();
  const { theme, toggle } = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onDown = (e) => { if (!menuRef.current?.contains(e.target)) setMenuOpen(false); };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [menuOpen]);

  const pulse = systemPulse(status, ais);

  return (
    <header className="hdr" data-testid="top-header">
      <button className="hdr-btn hdr-burger" onClick={onToggleNav} title="Collapse or expand the navigation"
        data-testid="nav-toggle" aria-label="Toggle navigation">
        <Menu size={18} />
      </button>

      <Link className="brand" to="/" title="OceanTrace — dashboard">
        <div className="brand-mark"><Waves size={17} /></div>
        <div>
          <div className="brand-name">OCEAN<b>TRACE</b></div>
          <div className="brand-sub">Maritime Intelligence</div>
        </div>
      </Link>

      <Breadcrumbs />

      <div className="hdr-mid">
        <ProvenanceChips />
      </div>

      <div className="hdr-right">
        <button className="hdr-search" onClick={openPalette} data-testid="palette-trigger"
          title="Search runs, incidents, vessels and scenes — ⌘K">
          <Search size={13} />
          <span className="hint">Search scenes, vessels, incidents…</span>
          <Kbd>⌘K</Kbd>
        </button>

        <LiveIndicator tone={pulse.tone} label={pulse.label} title={pulse.title} />

        <Notifications summary={alertsSummary} />

        <button className="hdr-btn" onClick={toggle} data-testid="theme-toggle"
          title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}>
          {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
        </button>

        <ZuluClock />

        {isEvaluator ? (
          <>
            <span className="hdr-eval" data-testid="evaluator-badge"
              title="Public evaluator view: no login, every screen open. Credential, account and zone-staffing changes need a production login.">
              <Eye size={13} /> Public evaluator view
            </span>
            <button className="btn btn-primary hdr-login" onClick={openLogin} data-testid="login-button"
              title="Sign in with a production account to see its role-based view">
              <LogIn size={14} /> Login
            </button>
          </>
        ) : (
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
              <Link className="hdr-menu-row" to={url.desk()} onClick={() => setMenuOpen(false)}>
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
        )}
      </div>
    </header>
  );
}
