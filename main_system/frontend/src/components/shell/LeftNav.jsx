/* The one navigation: a collapsible sidebar generated from ROUTES, so a
 * screen cannot exist that the sidebar does not know about.
 *
 * Main section first (the investigation product), then a rule, then the
 * System group and Help. Incident routing (Operations) is a main-sidebar
 * group for a zone officer and part of System for everyone else. Role-aware
 * -- an entry the server would 403 is not shown, but every route remains
 * reachable by URL and from the palette. Collapsing hides labels (tooltips
 * take over) and turns groups into flyouts; at tablet width the sidebar is a
 * drawer the header's menu button opens. */

import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import * as Icons from "lucide-react";

import { useSession } from "../../lib/session";
import { GROUP_LABEL, MAIN_ORDER, ROUTES, operationsInMain, routeFor } from "../../lib/shell";
import { roleLabel } from "./TopHeader";

const NAV_KEY = "oceantrace.nav";
const GROUPS_KEY = "oceantrace.nav.groups";

function readCollapsed() {
  try { return localStorage.getItem(NAV_KEY) === "collapsed"; } catch { return false; }
}

function readGroups() {
  try { return JSON.parse(localStorage.getItem(GROUPS_KEY) || "{}") || {}; } catch { return {}; }
}

export function useNavCollapsed() {
  const [collapsed, setCollapsed] = useState(readCollapsed);
  useEffect(() => {
    try { localStorage.setItem(NAV_KEY, collapsed ? "collapsed" : "open"); } catch { /* ignore */ }
  }, [collapsed]);
  return [collapsed, setCollapsed];
}

const GROUP_ICON = { operations: "Siren", system: "Settings2" };
const SUBHEAD = { operations: "Incident routing" };

/** The sidebar's structure for a role: main items, then groups. Exported so
 *  the tests can assert placement without rendering. */
export function navModel(role) {
  const visible = ROUTES.filter((r) => r.group && (!r.roles || r.roles.includes(role)));
  const byId = Object.fromEntries(visible.map((r) => [r.id, r]));
  const group = (g) => visible.filter((r) => r.group === g);
  const opsMain = operationsInMain(role);
  return {
    main: MAIN_ORDER.map((id) => byId[id]).filter(Boolean),
    mainGroups: opsMain ? [{ id: "operations", items: group("operations") }] : [],
    lowerGroups: [{
      id: "system",
      // For everyone but a zone officer, incident routing is a System concern.
      items: opsMain ? group("system") : [...group("system"), ...group("operations")],
    }],
    help: group("help"),
  };
}

function Item({ route, badge, critical, sub = false, onNavigate }) {
  const Icon = Icons[route.icon] || Icons.Circle;
  return (
    <NavLink to={route.to} end={route.to === "/" || route.id === "investigations"
      || route.id === "detections" || route.id === "reports"}
      title={route.hint ? `${route.label} — ${route.hint}` : route.label}
      onClick={onNavigate}
      className={({ isActive }) => `nav-item ${sub ? "nav-sub" : ""} ${isActive ? "active" : ""}`}
      data-testid={`nav-${route.id}`}>
      <Icon size={sub ? 15 : 18} strokeWidth={1.75} aria-hidden="true" />
      <span className="nav-item-label">{route.label}</span>
      {badge != null && (
        <span className={`nav-item-badge ${critical ? "crit" : "warn"}`}
          aria-label={`${badge} open alerts`}>{badge}</span>
      )}
    </NavLink>
  );
}

function Group({ group, collapsed, currentId, open, onToggle, badgeFor, critical, onNavigate }) {
  const [fly, setFly] = useState(false);
  const ref = useRef(null);
  const location = useLocation();
  const Icon = Icons[GROUP_ICON[group.id]] || Icons.Folder;
  const holdsCurrent = group.items.some((r) => r.id === currentId);
  const groupBadge = group.items.map((r) => badgeFor(r)).find((b) => b != null) ?? null;

  useEffect(() => { setFly(false); }, [location.pathname]);
  useEffect(() => {
    if (!fly) return undefined;
    const onDown = (e) => { if (!ref.current?.contains(e.target)) setFly(false); };
    const onKey = (e) => { if (e.key === "Escape") setFly(false); };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("mousedown", onDown); window.removeEventListener("keydown", onKey); };
  }, [fly]);

  if (!group.items.length) return null;
  const expanded = collapsed ? fly : open;
  // Incident routing keeps its own heading when it is folded into System.
  const items = group.items.flatMap((r, i) => {
    const heads = group.id !== r.group && group.items[i - 1]?.group !== r.group;
    return [
      heads && <div key={`h-${r.group}`} className="nav-section-label nav-sub-label">{SUBHEAD[r.group]}</div>,
      <Item key={r.id} route={r} sub badge={badgeFor(r)} critical={critical} onNavigate={onNavigate} />,
    ];
  });

  return (
    <div className="nav-group" ref={ref}>
      <button type="button"
        className={`nav-item nav-group-head ${holdsCurrent && !expanded ? "active" : ""}`}
        onClick={() => (collapsed ? setFly((f) => !f) : onToggle(group.id))}
        aria-expanded={expanded} aria-haspopup={collapsed ? "menu" : undefined}
        title={GROUP_LABEL[group.id]} data-testid={`nav-group-${group.id}`}>
        <Icon size={18} strokeWidth={1.75} aria-hidden="true" />
        <span className="nav-item-label">{GROUP_LABEL[group.id]}</span>
        {groupBadge != null && !expanded && (
          <span className={`nav-item-badge ${critical ? "crit" : "warn"}`}>{groupBadge}</span>
        )}
        <Icons.ChevronRight size={13} className={`nav-chevron ${expanded ? "open" : ""}`} aria-hidden="true" />
      </button>
      {expanded && !collapsed && <div className="nav-group-items">{items}</div>}
      {expanded && collapsed && (
        <div className="nav-flyout" role="menu" data-testid={`nav-flyout-${group.id}`}>
          <div className="nav-section-label">{GROUP_LABEL[group.id]}</div>
          {items}
        </div>
      )}
    </div>
  );
}

export default function LeftNav({ collapsed, alertsSummary, drawerOpen = false, onNavigate }) {
  const { user } = useSession();
  const location = useLocation();
  const role = user?.role;
  const openAlerts = alertsSummary?.open ?? 0;
  const critical = (alertsSummary?.by_severity?.critical ?? 0) > 0;
  const model = navModel(role);
  const currentId = routeFor(location.pathname)?.id;

  const [groups, setGroups] = useState(readGroups);
  useEffect(() => {
    try { localStorage.setItem(GROUPS_KEY, JSON.stringify(groups)); } catch { /* ignore */ }
  }, [groups]);
  const toggle = (id) => setGroups((g) => ({ ...g, [id]: !isOpen(id, g) }));
  // A group holding the current page is open unless the user closed it.
  const isOpen = (id, g = groups) => {
    if (g[id] != null) return g[id];
    const all = [...model.mainGroups, ...model.lowerGroups].find((x) => x.id === id);
    return Boolean(all?.items.some((r) => r.id === currentId));
  };

  const badgeFor = (r) => (r.id === "alerts" && openAlerts > 0 ? openAlerts : null);
  const groupProps = { collapsed, currentId, onToggle: toggle, badgeFor, critical, onNavigate };

  return (
    <nav className={`nav ${collapsed ? "nav-collapsed" : ""} ${drawerOpen ? "nav-drawer-open" : ""}`}
      aria-label="Primary" data-testid="left-nav">
      <div className="nav-scroll">
        {model.main.map((r) => <Item key={r.id} route={r} onNavigate={onNavigate} />)}
        {model.mainGroups.map((g) => <Group key={g.id} group={g} open={isOpen(g.id)} {...groupProps} />)}
        <div className="nav-rule" role="separator" />
        {model.lowerGroups.map((g) => <Group key={g.id} group={g} open={isOpen(g.id)} {...groupProps} />)}
        {model.help.map((r) => <Item key={r.id} route={r} onNavigate={onNavigate} />)}
      </div>
      <div className="nav-role" title={user?.email}>
        <span className="nav-role-k">Role</span>
        <span className="nav-role-v">{roleLabel(role)}</span>
      </div>
    </nav>
  );
}
