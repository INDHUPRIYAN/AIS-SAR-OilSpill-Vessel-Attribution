/* The URL contract and the one navigation.
 *
 * Four properties that used to hold by care and now hold by test:
 *   - every screen the shell lists has a page, and every page has a listing
 *   - every address the app ever answered to still resolves, to a live route
 *   - the two shapes the backend's search emits land where they mean
 *   - the sidebar puts incident routing where the role's work is
 */

import { describe, expect, it } from "vitest";
import { matchPath } from "react-router-dom";

import { PAGES } from "../src/App";
import { navModel } from "../src/components/shell/LeftNav";
import { MAIN_ORDER, ROUTES, crumbsFor, pathsOf, routeFor } from "../src/lib/shell";
import { LEGACY_PATHS, canonical, url, workspaceUrl } from "../src/lib/urls";

const live = (pathname) => ROUTES.some((r) => pathsOf(r).some((p) => matchPath({ path: p, end: true }, pathname)));
const pathOf = (address) => address.split("?")[0];

describe("routes and pages", () => {
  it("has exactly one page per listed screen", () => {
    expect(Object.keys(PAGES).sort()).toEqual(ROUTES.map((r) => r.id).sort());
  });

  it("gives every screen a unique id and an address its own patterns match", () => {
    expect(new Set(ROUTES.map((r) => r.id)).size).toBe(ROUTES.length);
    for (const r of ROUTES.filter((x) => x.palette !== false)) {
      expect(routeFor(r.to)?.id, r.to).toBe(r.id);
    }
  });

  it("reads a static segment as itself, not as an investigation id", () => {
    expect(routeFor("/investigations/registry").id).toBe("registry");
    expect(routeFor("/investigations/new").id).toBe("workspace");
    expect(routeFor("/investigations/inv-7/hindcast").id).toBe("workspace");
    expect(routeFor("/investigations").id).toBe("investigations");
    expect(routeFor("/nowhere")).toBeNull();
  });
});

describe("legacy addresses", () => {
  it("never shadows a live route", () => {
    for (const p of LEGACY_PATHS) expect(live(p), p).toBe(false);
  });

  it("resolves every legacy path to a live route", () => {
    for (const p of LEGACY_PATHS) {
      const to = canonical(p, "");
      expect(to, p).toBeTruthy();
      expect(live(pathOf(to)), `${p} -> ${to}`).toBe(true);
    }
  });

  it("moves workspace identity into the path and keeps the rest as query", () => {
    expect(canonical("/investigation", "?inv=inv-1")).toBe("/investigations/inv-1");
    expect(canonical("/investigation", "?inv=inv-1&stage=drift")).toBe("/investigations/inv-1/drift");
    expect(canonical("/investigation", "?run=r-9&stage=ais")).toBe("/investigations/run/r-9/ais");
    expect(canonical("/investigation", "?inv=inv-1&run=r-9&present=1"))
      .toBe("/investigations/inv-1?run=r-9&present=1");
    expect(canonical("/investigation", "?new=1")).toBe("/investigations/new");
    expect(canonical("/investigation", "")).toBe("/investigations/latest");
  });

  it("lands the backend search's own link shapes where they mean", () => {
    // backend/api/search.py emits these; the server is not ours to change.
    expect(canonical("/incidents", "?incident=INC-4")).toBe("/operations/incidents?focus=INC-4");
    expect(canonical("/investigation", "?scene=S1A_X")).toBe("/detections?scene=S1A_X");
  });

  it("repairs the links that were broken at baseline", () => {
    expect(canonical("/system", "?tab=workers")).toBe("/system/health?tab=runtime");
    expect(canonical("/report", "")).toBe("/reports");
    expect(canonical("/report", "?run=r-9")).toBe("/reports/print/r-9");
    expect(canonical("/vessels", "?mmsi=419000123")).toBe("/vessels/419000123");
    expect(canonical("/vessels", "")).toBeNull();
  });

  it("leaves a canonical address alone", () => {
    expect(canonical("/investigations/inv-1/drift", "")).toBeNull();
    expect(canonical("/system/zones", "?zone=z1")).toBeNull();
  });
});

describe("builders", () => {
  it("round-trips the workspace through its own address", () => {
    const to = workspaceUrl({ inv: "inv-1", run: "r-9", stage: "drift" });
    expect(to).toBe("/investigations/inv-1/drift?run=r-9");
    expect(live(pathOf(to))).toBe(true);
  });

  it("builds only live addresses", () => {
    const built = [url.dashboard(), url.investigations(), url.newInvestigation(), url.runRegistry(),
      url.map({ zone: "z" }), url.detections("S1"), url.sceneViewer("r"), url.vessel("1"), url.vessel(),
      url.reports(), url.reportPrint("r"), url.incidents("i"), url.alerts(), url.desk(), url.replay("r"),
      url.zones("z"), url.engines("j"), url.systemHealth("jobs")];
    for (const to of built) expect(live(pathOf(to)), to).toBe(true);
  });
});

describe("the one sidebar", () => {
  it("shows the core product first, in order, to every role", () => {
    for (const role of ["analyst", "zone_officer", "admin"]) {
      expect(navModel(role).main.map((r) => r.id)).toEqual(MAIN_ORDER);
    }
  });

  it("puts incident routing in the main sidebar for a zone officer only", () => {
    const officer = navModel("zone_officer");
    expect(officer.mainGroups.map((g) => g.id)).toEqual(["operations"]);
    expect(officer.lowerGroups[0].items.some((r) => r.group === "operations")).toBe(false);

    const analyst = navModel("analyst");
    expect(analyst.mainGroups).toEqual([]);
    expect(analyst.lowerGroups[0].items.filter((r) => r.group === "operations").map((r) => r.id))
      .toEqual(["desk", "incidents", "alerts", "replay"]);
  });

  it("hides what the server would refuse, without unlisting the route", () => {
    const ids = (role) => navModel(role).lowerGroups[0].items.map((r) => r.id);
    expect(ids("analyst")).not.toContain("credentials");
    expect(ids("super_admin")).toContain("credentials");
    expect(routeFor("/system/credentials").id).toBe("credentials");
  });
});

describe("breadcrumbs", () => {
  it("names the investigation and the stage, linking back up", () => {
    expect(crumbsFor("/investigations/inv-1/drift", "Drift hindcast & origin")).toEqual([
      { label: "Investigations", to: "/investigations" },
      { label: "inv-1", to: "/investigations/inv-1", mono: true },
      { label: "Drift hindcast & origin" },
    ]);
  });

  it("names the group for a system page and the vessel for a dossier", () => {
    expect(crumbsFor("/system/zones").map((c) => c.label)).toEqual(["System", "Zones"]);
    expect(crumbsFor("/vessels/419000123").map((c) => c.label)).toEqual(["Vessels", "MMSI 419000123"]);
    expect(crumbsFor("/nowhere")).toEqual([{ label: "Not found" }]);
  });
});

describe("sidebar icons", () => {
  it("has a real icon for every screen it lists", async () => {
    const { NAV_ICONS } = await import("../src/components/shell/LeftNav");
    for (const r of ROUTES.filter((x) => x.icon)) expect(NAV_ICONS[r.icon], r.id).toBeTruthy();
  });
});
