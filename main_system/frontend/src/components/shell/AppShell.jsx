/* The application shell: header on top, navigation on the left, the routed
 * page in the middle, the measured status bar below.
 *
 * The shell owns the polling for the three things every page shares -- the
 * provider sweep, the AIS stream state and the alert summary -- so the
 * header pulse, the nav badge and the status bar all read one response
 * rather than each asking the server separately. */

import { api, useApi } from "../../lib/api";
import LeftNav, { useNavCollapsed } from "./LeftNav";
import StatusBar from "./StatusBar";
import TopHeader from "./TopHeader";

export default function AppShell({ children }) {
  const [collapsed, setCollapsed] = useNavCollapsed();

  const { data: status } = useApi(() => api.apiStatus(), [], { interval: 20000 });
  const { data: ais } = useApi(() => api.aisStatus(), [], { interval: 30000 });
  const { data: health } = useApi(() => api.systemHealth(), [], { interval: 30000 });
  const { data: workers } = useApi(() => api.workers(), [], { interval: 30000 });
  const { data: alerts } = useApi(() => api.alertsSummary(), [], { interval: 20000 });

  return (
    <div className="app">
      <TopHeader status={status} ais={ais} alertsSummary={alerts} />
      <div className={`body ${collapsed ? "nav-collapsed" : ""}`}>
        <LeftNav collapsed={collapsed} onToggle={() => setCollapsed((c) => !c)}
          alertsSummary={alerts} />
        <main className="main" id="main">{children}</main>
      </div>
      <StatusBar status={status} ais={ais} health={health} workers={workers} />
    </div>
  );
}
