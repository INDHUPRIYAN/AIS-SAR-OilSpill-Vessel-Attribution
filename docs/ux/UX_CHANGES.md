# UX_CHANGES — what changed, phase by phase

Companion to [UX_AUDIT.md](UX_AUDIT.md). Each phase appends a section.
Gate for every phase: lint · typecheck (maps only, from P2) · unit · build ·
e2e · BASELINE flows.

---

## P1 — Shell, information architecture, URL contract

### Before → after

| | Before | After |
|---|---|---|
| Navigation surfaces | 9 (rail, "More" flyout, header nav, context strip, palette, status-bar links, 2 tile grids, user menu) | 1 sidebar + palette. Header nav, context strip and flyout removed |
| Sidebar | 94 px icon rail, 11 items + 14 in a flyout, 5 sections | Dashboard · Investigations · Live Map · Detections · Vessels · Reports — rule — System ▸ · Help. Collapsible (icons + tooltips, groups become flyouts); drawer ≤ 1100 px |
| Role-aware nav | hides 3 admin pages | same, plus: **zone officer** gets an Operations group (My Desk, Incidents, Alerts, Replay) in the main sidebar; every other role finds it under System ▸ Incident routing |
| Where am I | non-clickable section/title strip | real breadcrumbs from the URL: `Investigations › inv-… › Drift hindcast & origin` |
| Routes | 26 flat, no path params, declared twice by hand | generated from `ROUTES`; identity in the path |
| Unknown address | silent redirect to `/`, URL erased | 404 page that shows the address and keeps it |
| Tabs | component state, lost on refresh | `?tab=` on System Health, Data Sources, ML Models |
| Lint | none | ESLint 9 flat config, `npm run lint` |

### Sitemap

```
/                                   Dashboard
/investigations                     register
/investigations/new                 workspace, new case
/investigations/latest[/:stage]     workspace, most recent
/investigations/:inv[/:stage]       workspace           (?run= pins a run)
/investigations/run/:run[/:stage]   workspace, unfiled run
/investigations/registry            run registry        (merges into register in P7)
/map                                Live Map
/detections                         scene registry      (?scene=)
/detections/viewer                  scene viewer        (merges in P7)
/vessels[/:mmsi]                    vessels + dossier
/reports                            library
/reports/print/:run                 printable report
/operations/{desk,incidents,alerts,replay}
/system/{engines,data-sources,api-monitor,zones,health,models,analytics,
         environment,audit,users,credentials}
/help
```

All 22 legacy addresses redirect permanently, query preserved, history
replaced (Back does not bounce). Includes the two shapes the backend search
emits, which were broken at baseline: `/incidents?incident=` → `?focus=`,
`/investigation?scene=` → `/detections?scene=`.

### Components

- **Created:** `lib/urls.js` (builders, `canonical()`, `usePathParams`,
  `useUrlTab`), `components/shell/Breadcrumbs.jsx`, `pages/NotFound.jsx`.
- **Refactored:** `lib/shell.jsx` (`ROUTES` with ids, groups, patterns;
  `routeFor` by pattern; `crumbsFor`), `LeftNav.jsx` (sidebar, groups, drawer),
  `TopHeader.jsx`, `AppShell.jsx`, `App.jsx` (router generated from `ROUTES`).
- **Reused unchanged:** all 26 pages. `Investigation.jsx` and `Vessels.jsx`
  keep their `useSearchParams`-shaped code through a path-backed adapter, so
  the workspace's 1100 lines were not touched to move its identity into the path.

### Fixed from the functionality matrix

Rows 1–7: four broken deep links, `super_admin` locked out of Credentials,
`?run=undefined` link, blank `/report`.

Also found by the gate: after the sidebar widened, the workspace stage row
overflowed and its prev/next buttons slid under the right panel (e2e 5 caught
it). The chip row now shrinks and scrolls; the buttons do not move.

### Tests

Unit 183 → 199 (`routes.test.jsx`: route/page parity, legacy addresses all
resolve to live routes, nav placement per role, breadcrumbs). E2E 15 → 21
(`shell.spec.js`: every screen renders without script error, legacy redirects,
404, back/forward, URL tabs, tablet drawer).

### Deferred

- ~100 in-page links still spell legacy addresses and rely on the redirect.
  They move to `url.*` builders in P9.
- 88 lint warnings (75 unused imports in legacy pages, 13 hook-dependency).
  `--max-warnings 88` ratchets it; P9 drives it to zero.
- Stage ids in the path are the existing twelve; P3 maps them onto the six-step stepper.
- Notifications panel (bell still links to Alerts) — P8.
- Map camera and timeline position in the URL — P2.
