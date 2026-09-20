/* The application shell: header on top, navigation on the left, the routed
 * page in the middle, the measured status bar below.
 *
 * The shell owns the polling for the three things every page shares -- the
 * provider sweep, the AIS stream state and the alert summary -- so the
 * header pulse, the nav badge and the status bar all read one response
 * rather than each asking the server separately. */

import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

import { api, useApi } from "../../lib/api";
import LeftNav, { useNavCollapsed } from "./LeftNav";
import StatusBar from "./StatusBar";
import TopHeader from "./TopHeader";

const DRAWER_QUERY = "(max-width: 1100px)";

export default function AppShell({ children }) {
  const [collapsed, setCollapsed] = useNavCollapsed();
  // At tablet width the sidebar is a drawer over the page, not a column.
  const [drawerOpen, setDrawerOpen] = useState(false);
  const location = useLocation();
  useEffect(() => { setDrawerOpen(false); }, [location.pathname]);
  useEffect(() => {
    if (!drawerOpen) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setDrawerOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);
  const toggleNav = () => {
    if (window.matchMedia?.(DRAWER_QUERY).matches) setDrawerOpen((o) => !o);
    else setCollapsed((c) => !c);
  };

  const { data: status } = useApi(() => api.apiStatus(), [], { interval: 20000 });
  const { data: ais } = useApi(() => api.aisStatus(), [], { interval: 30000 });
  const { data: health } = useApi(() => api.systemHealth(), [], { interval: 30000 });
  const { data: workers } = useApi(() => api.workers(), [], { interval: 30000 });
  const { data: alerts } = useApi(() => api.alertsSummary(), [], { interval: 20000 });

  return (
    <div className="app">
      <TopHeader status={status} ais={ais} alertsSummary={alerts}
        onToggleNav={toggleNav} />
      <div className={`body ${collapsed ? "nav-collapsed" : ""}`}>
        <LeftNav collapsed={collapsed && !drawerOpen} alertsSummary={alerts} drawerOpen={drawerOpen}
          onNavigate={() => setDrawerOpen(false)} />
        {drawerOpen && <div className="nav-scrim" onClick={() => setDrawerOpen(false)} aria-hidden="true" />}
        <main className="main" id="main">{children}</main>
      </div>
      <StatusBar status={status} ais={ais} health={health} workers={workers} />
    </div>
  );
}
