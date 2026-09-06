# OCEANTRACE — PRODUCTION OSINT UI/UX SPECIFICATION

**Document:** OCEANTRACE_PRODUCTION_OSINT_UI_UX_SPEC.md · v1.0
**Companion to:** `OceanTrace_Production_UI_Spec.md` (pages P0–P18). That document says *what exists*; this one says *how it looks, moves, and feels*.
**Design stance:** inspired by OSINT command-center interaction patterns (persistent map canvas, mode switching, intelligence rails, command palette, ZULU discipline) — **not** a pixel-for-pixel copy of OSIRIS, WorldMonitor, or any other product. OceanTrace's identity comes from its own layer semantics: SAR grayscale, crimson slick, magenta hindcast, gold origin, amber forecast, cyan AIS.
**Prime directive:** the map is the application. Everything else is furniture around it.

---

## 0. DESIGN TOKENS (the single source of truth)

### 0.1 Color

| Token | Hex / value | Role |
|---|---|---|
| `--bg-void` | `#02060C` | App background, behind the globe |
| `--bg-ocean` | `#050F1C` | Globe/ocean base, 2D map water |
| `--surface-1` | `rgba(6,16,27,0.78)` | Glass panels (primary) |
| `--surface-2` | `rgba(10,24,38,0.88)` | Raised panels, drawers, palette |
| `--surface-solid` | `#0A1826` | Menus, tooltips (no blur budget) |
| `--line-soft` | `rgba(94,178,230,0.14)` | Hairline borders, dividers |
| `--line-strong` | `rgba(94,178,230,0.32)` | Focused/hovered borders, corner ticks |
| `--ink-hi` | `#E8F4FC` | Primary text |
| `--ink` | `#C9E2F2` | Body text |
| `--ink-dim` | `#5D7F95` | Secondary text, labels, units |
| `--cyan` | `#22D3EE` | AIS, interactive accents, live data |
| `--teal` | `#2DD4BF` | Success, healthy systems, PASS |
| `--gold` | `#D4A938` | Origin, provenance warnings, review states |
| `--amber` | `#FFB347` | Forecast, degraded states |
| `--crimson` | `#FF3B57` | Spill, alerts, AIS gaps, destructive |
| `--magenta` | `#E14AD4` | Hindcast particles only — never UI chrome |
| `--filtered` | `rgba(148,163,184,0.42)` | Excluded vessels, disabled data |

Usage discipline: **cyan is the only interactive accent.** Gold/crimson/amber/magenta are *data semantics* and never decorate buttons, links, or brand moments. A screen with no alerts and no run open shows almost no color at all — restraint is the aesthetic.

### 0.2 Typography

| Role | Face | Size / weight / tracking |
|---|---|---|
| Data, labels, coords, IDs, time | `"IBM Plex Mono", "JetBrains Mono", ui-monospace` | 10–13px · 400/500 · +0.02em |
| Panel titles, section heads | Same mono | 10px · 500 · +0.18em, uppercase |
| Prose (reports, notes, empty states) | `"Inter", system-ui` | 13px/1.55 · 400 |
| Numeric emphasis (scores, counts) | Mono | 15–20px · 500, tabular figures |

One mono family carries the command-center voice; Inter appears only where humans read paragraphs. No third face, ever. Tabular numerals (`font-variant-numeric: tabular-nums`) on anything that updates live.

### 0.3 Space, radius, elevation

- Spacing scale: 4 / 8 / 12 / 16 / 24 / 32. Panels pad 12; dense tables pad 8.
- Radius: `2px` on everything (panels, chips, inputs). The near-square corner *is* the identity; no pill shapes except the ZULU clock chip.
- Elevation is done with **border + backdrop blur**, not shadows: `--surface-1` + `1px --line-soft` + `backdrop-filter: blur(10px) saturate(1.1)`. One soft shadow (`0 8px 32px rgba(0,0,0,0.45)`) is reserved for drawers and the command palette only.
- Corner ticks (the 10×10px L-brackets) mark exactly two things: the *active investigation panel* and the *selected map annotation*. They are a focus device, not wallpaper.

### 0.4 Z-order model (fixed, global)

```
z0   map/globe canvas (persistent, never unmounts)
z10  map-anchored overlays (tooltips, vessel labels, measure readouts)
z20  shell chrome (top bar, left rail, bottom timeline)
z30  docked panels (right intelligence rail, floating panels)
z40  slide-overs & drawers
z50  command palette, modals
z60  toasts
z70  system banners (offline, degraded, environment)
```

### 0.5 Motion

| Token | Value | Used for |
|---|---|---|
| `--t-fast` | 120ms · `cubic-bezier(.2,.8,.2,1)` | Hover, toggles, chip states |
| `--t-panel` | 220ms · same | Panels, drawers, rail expand |
| `--t-mode` | 450ms · `cubic-bezier(.4,0,.2,1)` | 3D↔2D↔MAP↔SAT mode morphs |
| `--t-camera` | 900–1700ms · ease-in-out | Camera flights (distance-scaled) |

Rules: nothing on the map animates unless it represents **time, state change, or user action**. No idle shimmer, no looping glows except (a) active-incident pulse rings and (b) the live-data heartbeat dot. `prefers-reduced-motion`: camera flights become cuts, particle trails become static positions, pulses become solid dots — information is never lost, only motion.

---

## 1. GLOBAL SHELL

The shell is one persistent frame; content inside it swaps without reload. Nothing in this section ever unmounts during a session.

### 1.1 Top navigation (44px, `z20`)

Left → right:

1. **Brand block** — `OCEAN|TRACE` wordmark (cyan on the pipe), 10px sublabel `MARITIME SPILL ATTRIBUTION`. Click = go to `/monitor` (P1). Never a hamburger.
2. **Mode switcher** — segmented control `3D · 2D · MAP · SAT` (see §2). Always visible, always enabled; SAT disabled-with-reason when no scene is in context (tooltip: "Open a run or select a scene to enter satellite mode").
3. **Context breadcrumb** — `INC-2017-CHN-001 ▸ RUN-…-001` when an investigation is open; each segment clickable; overflow ellipsizes the middle. Empty when browsing globally.
4. **Provenance chip row** *(center, the honesty strip)* — `S1: REAL` · `AIS: SYNTHETIC` (gold) · `MET: CACHE` · `ENGINE B`. Chips reflect the **run in context**, not global config. Hover any chip → popover with source detail, valid-time vs scene-time, and a "view manifest" link. No run in context → chips collapse to a single `NO RUN CONTEXT` ghost chip.
5. **Search / palette trigger** — magnifier + `⌘K` hint; opens §1.6.
6. **Alert bell** — badge count of unacknowledged alerts; click opens notification drawer (§1.7). Bell turns crimson and pulses once (single pulse, not looping) on new P0 alert.
7. **ZULU clock** — `HH:MM:SSZ`, cyan, tabular. Click toggles a second line showing IST, always suffixed `IST`. The clock is the only pill-shaped element in the app.
8. **User chip** — initials, role tag (`INV`/`ANL`/`REV`/`ADM`), menu: profile, theme density, sign out.

Top bar background: `--surface-1` fading to transparent at the bottom edge so the globe reads through.

### 1.2 Left navigation (icon rail, 52px collapsed / 216px expanded, `z20`)

- Icon-only by default; hover-expand after 250ms or pin-expand via chevron. Expansion pushes nothing — it overlays the map edge (`z20`).
- Sections (top group): Monitor `P1` · Alerts `P2` · Incidents `P4` · Runs `P13` · Vessels `P11` · Data `P14` · Models `P15`. (Bottom group): Health `P17` · Audit `P18` · Admin `P16`.
- Active item: 2px cyan left edge + icon at `--ink-hi`. Badge dots: crimson on Alerts (unacked count), amber on Health when degraded.
- **New Investigation** is a distinct button at the rail top (crosshair-plus icon, cyan outline) — the one call-to-action in the chrome. Opens P3 as a slide-over, not a page.
- Keyboard: `[` toggles rail pin. Rail items are tabbable with visible focus ring (`1px --cyan` outer).

### 1.3 Right intelligence rail (360px, `z30`)

The context brain. Its content is determined by *selection state*, in priority order:

1. Nothing selected, no run → **collapsed to a 0-width edge tab** labeled `INTEL`.
2. Run open, nothing selected → **Run summary stack**: pipeline stage tracker mini, spill metrics card, funnel bar, top-3 suspects preview. Each card's header deep-links to its full view (P5–P10).
3. Map entity selected (vessel / slick / origin / forecast ring / annotation) → **Entity inspector** replaces the stack top: identity, live values at current timeline `t`, evidence, actions (`focus camera`, `open dossier`, `add to report`). A back arrow returns to the summary stack.
4. Suspect selected in attribution view → **Suspect deep card**: factor bars with weights, score arithmetic expander (`total = Σ wᵢ·sᵢ`), evidence list, gap ribbon on a micro-track.

Rail behavior: resizable 320–480px (drag handle, double-click resets); `]` toggles collapse; content scrolls, header stays. Rail cards are `--surface-1`, 12px pad, corner ticks on the *topmost card only*.

### 1.4 Bottom timeline (persistent, 76px, `z20`)

Appears whenever an investigation context exists; persists across every mode and every view (including tables like P13 — where it minimizes to a 24px strip showing only the scrubber + `t`).

- **Track**: `T−24h … T0 … T+24h` (range derives from run config). Gold band = release window. Crimson tick at `T0` labeled `ACQ`. Event markers (§7.11) sit above the track.
- **Left cluster**: play/pause, step ± (one AIS interpolation step, default 5min), speed menu `1× 5× 20× 60×` (1× = real-time, others = minutes-per-second).
- **Readout**: `T−08:20` (mono 15px) + absolute `2017-01-28 16:27Z` (dim). During the release window, a gold `WINDOW` tag lights.
- **Right cluster**: loop toggle, "sync views" toggle (locks 2D/3D/SAT to the same `t`), copy-deep-link (copies URL with `?t=`).
- Scrubbing is direct-manipulation: drag anywhere on the track; fine-scrub with `Shift` (per-minute). `Space` play/pause, `←/→` step, `Shift+←/→` jump to previous/next event marker.

### 1.5 Floating panels

Small, map-anchored utility panels — distinct from the rail:

- **Layers panel** (bottom-right, 200px): layer checkboxes with color swatches, opacity sliders on hover-expand, per-mode memory (3D remembers its own layer set vs SAT).
- **Legend** (attaches under Layers, collapsible): meaning of every color/symbol currently on screen — auto-generated from active layers, never stale.
- **Measure readout** (appears only in MAP mode near cursor work, §5).
- **Mini-map / view cube** (3D mode, top-right under chrome): orientation + click-to-north.

Floating panel rules: draggable within the viewport, snap to 8px grid and to screen edges, remember position per user, collapse to title bars, never overlap the timeline (auto-nudge).

### 1.6 Command / search palette (`⌘K` / `Ctrl+K`, `z50`)

One input, three result classes, fuzzy-matched, keyboard-first:

- **Navigate**: `runs`, `incident chn`, `health`, `audit` → routes. Typing a run ID or MMSI jumps straight to it.
- **Act**: `new investigation`, `toggle forecast layer`, `set speed 20x`, `export report`, `go to 16:30Z` (time parsing → moves the timeline), `mode sat`.
- **Find on map**: vessel names, incident names, coordinates (`13.16, 80.55` → flies camera), scene product IDs.

Layout: centered 640px, `--surface-2`, results grouped with section labels, selected row cyan-edged; footer shows key hints. Recent items on empty query. Every palette action is also available by mouse somewhere — the palette is an accelerator, never the only path.

### 1.7 Notification system

- **Toasts** (`z60`, bottom-center above timeline): max 3 stacked, 5s auto-dismiss, kinds: info (cyan edge), success (teal), warn (amber), alert (crimson, sticky until dismissed). Every toast that reports a completed background action carries the action's noun-verb ("Report published", "Run RUN-…-002 failed at DRIFT_HINDCAST — view").
- **Notification drawer** (from bell, `z40`, right slide-over 400px): grouped Today/Earlier; each row: severity dot, message, UTC time, deep link; "acknowledge all" for Investigators is per-severity (P0 alerts require individual ack — that ack is an audit event).
- **System banners** (`z70`, full-width under top bar): reserved for connection lost (crimson, with reconnect countdown), degraded providers (amber, "CDSE unreachable — runs proceed cache-only"), staging environment (violet). Banners push content down; they are never toasts.

### 1.8 Status & provenance indicators (the trust system)

A single icon+chip vocabulary used identically everywhere:

| Indicator | Form | Semantics |
|---|---|---|
| `REAL` | teal-outlined chip | Real sensor data (S1, real AIS archive) |
| `SYNTHETIC` | gold-filled chip | Synthetic/scenario data — **always visible wherever derived results appear**, incl. every suspect row and every export |
| `CACHE` | dim chip + clock glyph | Served from cache; hover shows fetch date + valid-time delta |
| `FALLBACK` | amber chip | Non-primary provider served this field |
| live dot | 6px teal dot, 2s heartbeat | Stream connected; goes hollow-amber when stale >10s, hollow-crimson when disconnected |
| stage states | `QUEUED` dim / `RUNNING` amber pulse / `CACHE ✓` dim-teal / `DONE ✓` teal / `FAILED ✕` crimson / `SKIPPED —` dim | Pipeline stages, everywhere stages appear |
| hash tag | `⌗ a3f9…` mono micro-chip | Artifact SHA-256 (truncated); click copies full hash |

Rule: provenance chips are **layout**, not decoration — they occupy reserved slots in the top bar, rail cards, suspect rows, report annex, and exports. A view with synthetic-derived content and no `SYNTHETIC` chip visible is a defect.

---

## 2. MAP EXPERIENCE — `3D · 2D · MAP · SAT`

### 2.1 The contract

Four *modes of one canvas*, not four pages. Switching modes:

- is **instant** (< 500ms perceived, `--t-mode` morph),
- **preserves**: camera target (lat/lng/zoom↔altitude mapping), timeline `t`, layer visibility (per-mode overrides remembered), current selection, investigation context,
- **never** triggers a route change beyond a query param (`?m=3d|2d|map|sat`) — back/forward steps through mode history,
- is available from: the top-bar segmented control, keys `1 2 3 4`, the palette (`mode sat`), and context ("Inspect scene" buttons jump to SAT centered on the scene).

### 2.2 The morph choreography

- **3D → 2D**: camera eases to nadir while the globe's projection interpolates to Mercator (deck.gl/MapLibre crossfade at matched viewpoint, 450ms). Vessels/layers persist through the fade — entities do not blink.
- **2D → 3D**: reverse; if zoom < z4, the globe pulls back to show curvature (altitude ≈ 1.6).
- **2D ↔ MAP**: no visual change to the basemap; MAP mode adds the analysis toolbar and switches cursor/interaction grammar (§5). 80ms toolbar slide-in only.
- **any → SAT**: camera flies to the active scene footprint; basemap dims to 30%; SAR raster fades in (300ms). If multiple scenes are in context, a scene strip appears (§6.2).
- Mode switch during playback does not pause playback.

### 2.3 Shared layer registry

One layer registry drives all modes; each layer declares per-mode renderers:

| Layer | 3D | 2D | SAT |
|---|---|---|---|
| Incidents | pulse rings + labels | markers + labels | hidden |
| Slick | extruded-0 polygon, crimson | polygon | polygon over raster |
| Hindcast particles | points, altitude 0.003 | points | points (dimmed) |
| Origin ellipse/ring | ring pulse + ellipse | ellipse + center | ellipse |
| Forecast | stacked footprints | footprints | footprints |
| AIS tracks/vessels | paths + point sprites | paths + oriented triangles | tracks (thin) |
| Filtered traffic | dim grey | dim grey + dash | hidden by default |
| Boundaries/ports/weather/currents | §4 | §4 | hidden |
| SAR/EO raster | low-res sphere drape (optional) | hidden | full tiles |

MAP mode renders whatever 2D renders, plus annotations.

---

## 3. 3D MODE (globe)

### 3.1 Globe & atmosphere
- Sphere base `--bg-ocean`; landmass as hex-dot fill `rgba(78,168,222,0.30)` at hex resolution 3 (production alt: subtle dark blue-marble drape at ≤2K texture with hex overlay — choose one per deployment, never both).
- Graticules at 10° in `--line-soft`; fade out below altitude 0.5 to reduce noise when zoomed in.
- Atmosphere rim: `#2EA8D8` at 0.16 altitude — the only glow in the app. No lens flares, no star-twinkle animation; the starfield is a static radial-gradient backdrop.
- Day/night terminator: optional layer (off by default) — thin gradient band, informational for acquisition-time context, toggleable in Layers.

### 3.2 Vessel visualization
- At globe altitudes (> 0.6): vessels are 2px luminous points; suspect cyan, selected gold, filtered dim, AIS-gap state crimson.
- Below 0.6: points grow to 4px with a 1px dark halo (readability over bright ocean areas); below 0.25 they gain heading ticks (2px stem showing course).
- Track rendering: suspects = animated dash flow (22s cycle, subtle — reads as direction, not decoration); filtered = solid, 40% alpha; selected = 3.4px gold with full-opacity dash.
- AIS gap: track breaks (no line); at current `t` inside a gap, the vessel dot holds last fix, turns crimson, and shows a hollow ring — the visual for "we don't actually know."
- Hover: tooltip (`z10`) with name, MMSI, type, speed at `t`, provenance badge; 80ms delay in/0 out.
- Density guard: above 300 visible vessels, unlabeled non-suspects cluster to heat-dots; clusters explode on zoom. Suspects never cluster.

### 3.3 Incident markers
- Active: crimson pulse ring (`ringMaxRadius` ~5.5°, 1 pulse / 0.9s) + label; investigating: gold ring slower; monitoring: cyan static dot; closed/archive: dim gold dot, no ring.
- Click: camera flight to incident (altitude 0.30) + right rail loads incident card. Double-click: open incident case file (P4 drawer).
- Label collision: labels hide before dots do; active incidents win ties.

### 3.4 Satellite / earth layers
- Optional low-res SAR drape of the active scene projected on the sphere (context only; analysis-grade raster lives in SAT mode) — 40% opacity, footprint outlined in `--line-strong`.
- Acquisition footprints of cataloged scenes as ghost outlines when the Data layer is on.

### 3.5 Camera controls
- Drag rotate; scroll/pinch zoom (altitude 0.06–2.6 clamp); right-drag or two-finger tilt (max 35° off-nadir); inertia on, damped.
- Auto-rotate (0.38°/s) only in idle global state (no run context, no selection, no input for 30s); any interaction cancels it permanently for the session.
- `R` reset (global view or run view depending on context); `N` north-up; double-click = fly-to point at next zoom step.
- All flights distance-scaled `--t-camera`; two chained flights merge (no zigzag).

### 3.6 Animated transitions (3D-specific)
- Run open: single orchestrated sequence — camera flight down (1.7s) while pipeline cascade plays in the rail; layers reveal in narrative order: slick (fade+ring flash once) → hindcast particles (stream back) → origin ring (draws in 400ms arc) → forecast (expand outward, staggered 120ms) → AIS tracks (draw-on along path, 600ms). Total ≤ 3.2s, skippable by any input, never repeated on revisit (only on first open per session).
- Selection: selected entity gets a one-shot 300ms focus ring; deselection is instant.

---

## 4. 2D MODE (MapLibre)

### 4.1 Basemap
- MapLibre GL, self-hosted dark ocean style: water `--bg-ocean`, land `#0B1520` with `--line-soft` coastlines, place labels off by default (toggle), bathymetry contours as near-invisible `rgba(94,178,230,0.05)` lines. No third-party tile branding in the canvas; attribution in the footer line.
- Projection: Mercator; scale bar bottom-left (km + nm — nautical miles matter here); mouse coordinate readout (`13.1623°N 80.5511°E`) in the footer strip, click-to-copy.

### 4.2 Vessels & routes
- Oriented triangle sprites (course-rotated), size by zoom (4–10px); color semantics identical to 3D (§3.2).
- Track polylines with time-fade option: past dims behind current `t`, future is dotted ghost (toggle "show future track", default on for suspects, off for others).
- Route inspection: click a track → vertices with timestamps on hover; `Alt`-hover scrubs the timeline to the hovered fix (map-driven time scrubbing).

### 4.3 Incidents & boundaries
- Incident markers as in 3D minus pulse scale (smaller rings).
- Boundaries layer: EEZ / territorial (12nm) / contiguous (24nm) lines in distinct dash patterns of the same dim blue — labeled on hover, never filled. Port approach areas optional.
- AOI of the current run: `--line-strong` dashed rectangle/polygon with corner ticks.

### 4.4 Ports
- Port markers (anchor glyph) with name labels at z ≥ 7; hover: port card (name, LOCODE, distance from slick centroid). Ports referenced in evidence strings ("bound for Kamarajar") get a gold underline link from the evidence text to the map marker.

### 4.5 Weather & currents
- Currents: animated particle streamlets (LIC-style flow field) in `rgba(45,212,191,0.35)`, density-capped, only at z ≥ 6, only when layer on; static arrows under reduced-motion.
- Wind: barbs at grid points, `--ink-dim`, decimated by zoom; hover shows u/v, speed, source + valid time.
- Both layers carry a provenance sub-chip in the Layers panel row (`CMEMS CACHE`, `ERA5 CACHE`) — forcing honesty lives in the layer control itself.

---

## 5. MAP / ANALYSIS MODE

MAP = 2D + an analysis grammar. Entering MAP slides in a left-edge vertical toolbar (40px wide, icons top-to-bottom): select, point, line, polygon, circle, measure, AOI, buffer, geofence.

### 5.1 Tools

| Tool | Interaction | Output |
|---|---|---|
| **Select** (`V`) | click entity; drag = marquee; `Shift` adds | selection set → rail inspector; marquee over vessels shows count chip |
| **Point** (`P`) | click | annotation pin with editable label + auto lat/lng; snaps to vessels/slick vertices within 8px |
| **Line** (`L`) | click-click…, double-click ends | polyline annotation; segment + total length live while drawing |
| **Polygon** (`G`) | click…, close on first vertex or double-click | polygon annotation; live area readout |
| **Circle** (`C`) | click center, drag radius | circle; radius readout in km/nm |
| **Measure** (`M`) | like line, but ephemeral | floating readout: geodesic distance km/nm + initial bearing; `Esc` clears |
| **AOI** (`A`) | drag rectangle or polygon mode via `Shift` | named AOI; actions: "New investigation here" (pre-fills P3), save to library |
| **Buffer** (`B`) | click any geometry (slick, origin, track, annotation) + numeric input | buffer ring, default 25 km pre-filled around origin cloud; "query AIS in buffer" action |
| **Geofence** (`F`) | draw polygon → arm | armed fence: any vessel entering/leaving during playback fires a timeline event marker + toast; fences persist per incident |

### 5.2 Grammar rules
- Active tool shown in the toolbar (cyan edge) and as a cursor change; `Esc` always returns to Select; `Enter` commits, `Backspace` removes last vertex.
- Vertex editing: click annotation → handles; drag to move; `Del` deletes annotation (confirm if referenced in a report).
- Every annotation: owned by the incident context, listed in a collapsible "Annotations" card in the rail, each with visibility eye, color dot (from a 6-swatch neutral set — annotation colors never collide with data semantics), and "add to report" pin.
- Measurements/annotations serialize into deep links and report exports (WKT + properties).

---

## 6. SATELLITE MODE (SAT)

### 6.1 Raster experience
- Sentinel-1 SAR: server-tiled (COG/XYZ), dB-stretched grayscale; stretch presets (`auto`, `ocean −28…−2 dB`, `manual` with histogram slider in a floating card). Never client-loads the full 28k×21k product.
- Sentinel-2 / EO: same tile contract when the EO adapter exists. Until then the EO source row is present but disabled with the honest label `EO ADAPTER NOT DEPLOYED` — the UI never pretends SAR is EO.
- Basemap dims to 30% beneath the raster; outside the footprint the basemap stays full — the scene reads as a lit table on a dark desk.

### 6.2 Scene strip & metadata
- Bottom-anchored scene strip (above timeline) when >1 scene is in context: thumbnail chips with date + sensor badge (`S1`/`S2`); click switches, `Alt+click` pins for comparison.
- Metadata card (rail): product ID, acquisition UTC (and IST-labeled secondary), mode/beam (IW GRDH), polarization, orbit/pass, pixel spacing, footprint area, source chain badge (`CDSE`/`ASF`/`CACHE`), product hash.
- Acquisition footprint outline always on in SAT; corner coordinates on hover.

### 6.3 Detection overlay
- Detection overlay group: mask (crimson, 42% fill), slick boundary (1.5px crimson), per-slick confidence tags, YOLO candidate boxes (thin dashed cyan, off by default — an expert toggle).
- **Opacity controls**: two sliders in the floating card — raster opacity, overlay opacity — plus `X` hold-to-peek (hides all overlays while held; the single most used gesture in QA).
- Mask/boundary coincidence is inspectable: `I` toggles "inspect" crosshair showing per-pixel dB under cursor + inside/outside-mask state.

### 6.4 Comparison
- **Swipe**: pin two scenes (or scene vs detection-overlay-off) → vertical swipe divider, draggable, `[`/`]` nudge.
- **Blink** (`K`): toggles pinned pair at 500ms — the classic change-detection gesture.
- **Side-by-side**: splits the canvas into two synced viewports (pan/zoom locked) for before/after acquisition dates.
- Comparison state encodes into the deep link (`?m=sat&cmp=swipe&a=<scene>&b=<scene>`).

---

## 7. INVESTIGATION ANIMATION

The pipeline logic is untouched. This section specifies only its *presentation*: the run's story told on the map, driven by the persistent timeline. Stage chain visualized:

```
Satellite → Detection → Characterization → Environment → Hindcast → Origin
→ Historic AIS → Filtering → Attribution → Forecast → Report
```

### 7.1 Two clocks, never confused
- **Pipeline clock**: real-world processing progress (stages running now). Lives in the stage tracker (rail + P5). Animates only while stages actually run.
- **Scenario clock**: the timeline `t` (T−24h…T+24h around acquisition). Everything in §7.3+ is driven by `t`. The UI labels them differently (`RUNNING 00:41` vs `T−08:20`) and they never share a control.

### 7.2 Stage transitions (pipeline clock)
- Stage tracker rows flip QUEUED → RUNNING (amber pulse) → DONE ✓ / CACHE ✓ (teal) with a 260ms cascade stagger when statuses arrive together.
- As each stage completes, its map layer performs a one-shot reveal (the §3.6 order); a completed stage's row becomes a link to its view (P6–P10).
- A FAILED stage: row turns crimson, downstream rows grey to `SKIPPED —`, and the map shows exactly the layers that exist — never stale substitutes. A crimson edge on the reveal sequence marks where the story stops.

### 7.3 Particle animation (hindcast)
- 100–500 magenta particles; position at scrub time `t` = interpolation along each particle's stored backward trajectory (fallback: seed→origin lerp shaped by an ease curve when per-step trajectories aren't persisted).
- Scrubbing backward from T0: particles stream out of the slick, spread (dispersion visibly grows), and pool into the origin cloud by window time. Forward scrubbing reverses it.
- During playback, particles leave 6-frame motion trails at 15% alpha (off under reduced-motion). Particle size constant 2px; opacity 0.75; never bloom.

### 7.4 Slick movement
- The detected slick polygon itself never moves (it is the observation at T0) — it dims to 40% when `t` moves away from T0 beyond ±1h, reinforcing "this is what the satellite saw, then."
- At `t` = T0 exactly, the slick flashes its boundary once (600ms) as the anchor moment; the timeline's `ACQ` tick mirrors the flash.

### 7.5 Vessel movement
- Vessel positions = interpolated fixes at `t` (§3.2 semantics). Course rotation eases over 150ms to avoid twitch.
- Entering an AIS gap: dot decelerates to the last fix, switches crimson + hollow ring, and a gap ribbon (crimson underline) lights on that vessel's rail card and its timeline event marker.
- Vessels with no data at `t` (before first / after last fix) fade to 20% at their endpoint positions.

### 7.6 Timeline (scenario clock)
- Specified in §1.4. Additional investigation bindings: gold release-window band, `ACQ` tick, event marker lane (§7.11), and per-suspect gap micro-bands (visible when a suspect is selected — their gaps project onto the timeline).

### 7.7 Playback & speed
- Speeds: `1× 5× 20× 60×` (minutes of scenario time per real second: 1/12, ~0.4, ~1.7, 5 — tuned so 60× crosses the full 48h span in ~35s). Default 20×.
- Play begins at current `t`; reaching T+24h stops (or loops if loop armed). Buffered/heavy frames drop particle trails first, then non-suspect vessels' per-frame updates — timing truth is never sacrificed (positions may skip frames, they never lag `t`).

### 7.8 Step forward / backward
- `←/→` = one interpolation step (5min); `Shift+←/→` = previous/next event marker; `Home/End` = T−24h/T+24h; `0` = T0.
- Step actions during playback pause playback first (one action = one intent).

### 7.9 Origin convergence
- The origin ellipse and ring exist from hindcast completion, but their *emphasis* is time-driven: outside the release window the ring idles at 40% opacity; as `t` enters the window the ring brightens to full and pulses slowly (1.4s), and the density ellipse fill rises from 6% → 12%. Leaving the window reverses it.
- When particles reach maximum pooling (window midpoint), a one-shot "convergence" tick fires: the ring draws a single 360° sweep (700ms). Once per playback pass, never on scrub.

### 7.10 Forecast expansion
- Forecast footprints are gated by `t`: a horizon's polygon is ghost (10% fill, dashed) until `t` ≥ its horizon, then transitions to solid amber (26% fill) over 350ms with a single edge shimmer. Scrubbing backward re-ghosts them.
- The active horizon (largest with `t` ≥ h) shows its area chip (`+12h · 2.19 km²`) at its centroid.

### 7.11 Event markers
- A thin lane above the timeline track carries diamond markers, colored by kind:
  - crimson: AIS gap start/end (per suspect), stage failure
  - gold: release-window bounds, convergence midpoint
  - cyan: vessel enters/exits origin buffer, geofence hits
  - amber: forecast horizons (+6/+12/+24)
  - dim: annotations pinned to a time
- Hover: label + entity; click: scrub to marker + select entity; `Shift+←/→` walks them. Markers filter with the layer panel (hiding AIS hides its markers).

---

## 8. OSINT VISUAL LANGUAGE

**Positioning:** inspired by OSINT command-center interaction patterns — persistent situational canvas, ZULU discipline, dense-but-calm intelligence rails — executed with OceanTrace's own palette, marks, and restraint. Not a pixel-for-pixel copy of OSIRIS or any reference product.

### 8.1 Dark command-center aesthetic
- One environment: dark, always (no light theme; a print stylesheet exists only for reports). Blacks are blue-cast (`--bg-void`), never pure #000.
- Light is information: the brightest pixels on screen must be data (vessels, slick, scores), then interactive accents, then text, then chrome. If chrome outshines data, the screen is wrong.
- Density with air: 12px panel padding, 8px table rhythm — dense like an ops console, but every block separated by real hairlines, not gradients.

### 8.2 Color restraint (cyan/teal/gold/crimson)
- §0.1 tokens are exhaustive; no new hues enter without a token PR. Magenta = hindcast only. Gold never means "premium," it means *origin/provenance/review*. Crimson never decorates; it means *spill/alert/gap/failure*.
- Max simultaneous accent families per view: the data demands what it demands, but **chrome** may use only cyan + neutrals.

### 8.3 Glass panels & borders
- Glass recipe (only one): `--surface-1/2` + `blur(10px)` + `1px --line-soft`. Nested cards drop the blur (blur never nests). Hover raises border to `--line-strong`; nothing changes background on hover except list rows (+4% white).
- Corner ticks per §0.3 — two placements max on screen.

### 8.4 Information density, typography, hierarchy
- Hierarchy tools in order: position → size → weight → color. Color is last resort.
- Numbers align right and tabular; units live in `--ink-dim` after the value (`3.42 km²`); labels sit left in 10px caps-tracked mono. A metric row is scannable in one saccade: `LABEL………value UNIT`.
- Line length in prose ≤ 68ch; tables get sticky headers past 8 rows.

### 8.5 Map-first design
- Chrome occupies ≤ 22% of viewport in default state (rail collapsed). Panels overlay the map; the map never resizes except for drawers ≥ 480px (then it pans to keep selection visible).
- Anything describable *on the map* is shown on the map first, in a panel second (a suspect's gap = broken track before it is a table cell).

### 8.6 States
- **Hover**: 120ms; border brightens, row tints, cursor communicates affordance; map entities show tooltip + subtle 1px halo. Hover never moves layout.
- **Selected**: gold is the selection color for data entities (map + list stay in sync, both marked); cyan edge is selection for chrome (nav, palette rows, toolbar). One selected entity per class; multi-select shows a count chip.
- **Alert**: crimson, plus a non-color redundancy every time (icon, label, or badge) — color-blind-safe by construction.
- **Disabled**: 40% opacity + `not-allowed` + tooltip *reason* ("SAT unavailable — no scene in context"). Disabled without a reason is forbidden.
- **Focus (keyboard)**: 1px cyan outer ring, 2px offset, on every operable element. Focus is never suppressed.

### 8.7 Provenance badges
- §1.8 vocabulary, everywhere, same shapes. `SYNTHETIC` is the loudest badge in the system by design (filled gold) — honesty is a first-class visual.

### 8.8 Animation & transitions
- Purposeful only (§0.5). Signature moments, exhaustively: boot line-type (first load per session), run-open reveal (§3.6), convergence sweep (§7.9), forecast unlock shimmer (§7.10), stage cascade (§7.2). Everything else is 120–220ms utility easing.
- No parallax, no background video, no gradient sweeps, no glow breathing. The globe's slow idle rotation is the only ambient motion, and it dies on first interaction.

---

## 9. VIEW-BY-VIEW UX (P0–P18)

Template per view: **Layout · Primary content · Map interaction · Panels · Controls · Animations/Transitions · Empty · Loading · Error · Degraded · Responsive · Keyboard · Deep link.** ("Rail" = right intelligence rail; "drawer" = right slide-over ≥ 480px; "sheet" = centered modal.)

### P0 — Sign in
- **Layout:** centered 380px card over the idle rotating globe at far altitude (the product sells itself behind the form); environment banner top.
- **Primary:** SSO button, credential form, MFA step.
- **Map interaction:** none (globe is non-interactive backdrop, 40% dimmed).
- **Panels/Controls:** card only.
- **Animations:** card fades in 220ms; on success, the card dissolves and the same globe becomes interactive — the login-to-app transition is a camera handoff, not a page load.
- **Empty:** n/a. **Loading:** button spinner inline. **Error:** inline field error + card shake 1× (120ms, reduced-motion: none). **Degraded:** SSO down → credential fallback visible with notice.
- **Responsive:** card full-width ≤ 420px. **Keyboard:** full form tab order. **Deep link:** `/login?next=…` returns the user to the exact pre-auth deep link.

### P1 — Global Monitor
- **Layout:** full-canvas globe (3D default), left rail collapsed, right rail collapsed to INTEL tab, timeline hidden (no run context).
- **Primary:** incidents on the globe; alert ticker as a slim strip under the top bar (latest 1 line, click → drawer); stat chips (open incidents / runs queued / last detection) docked top-left under chrome.
- **Map interaction:** full 3D grammar (§3); incident click → rail incident card; double-click → P4 drawer for that incident.
- **Panels:** Layers + Legend floating; rail shows incident card on selection.
- **Controls:** mode switcher live (2D useful for regional scan); "New investigation" rail button.
- **Animations:** boot sequence (first session load only); active-incident pulses; ticker items slide in 220ms.
- **Empty:** zero incidents → globe + one quiet card: "No active incidents. The watch continues." + New investigation CTA.
- **Loading:** globe renders immediately; incidents skeleton as ghost dots resolve to real markers (< 1s target).
- **Error:** incidents API down → banner + last-cached incidents rendered with `CACHE` chip + stale timestamp.
- **Degraded:** stream stale → hollow-amber live dot + "last update HH:MMZ" chip.
- **Responsive:** ≤ 900px: rail becomes bottom sheet, stat chips collapse into one expandable chip.
- **Keyboard:** `1–4` modes, `⌘K`, `R` reset, `A` open alerts. **Deep link:** `/monitor?m=3d&cam=lat,lng,alt`.

### P2 — Alerts & Tasking
- **Layout:** left half = alert table drawer docked over the map; right half = map showing the hovered/selected alert's location (split emphasis 45/55).
- **Primary:** alert queue rows: severity dot, UTC time, region, detector confidence, SLA age bar (fills toward breach), status.
- **Map interaction:** hovering a row ghosts a marker + footprint on the map; selecting flies to it.
- **Panels:** rail = selected alert detail (scene thumb, confidence, source, actions).
- **Controls:** acknowledge / assign (user picker) / dismiss-with-reason (reason sheet, mandatory text); bulk ack per severity; filters (region, severity, status, age).
- **Animations:** new alert row slides in from top with a single crimson edge flash; SLA bars animate width only on data change.
- **Empty:** "Queue clear. 0 unacknowledged." + last-24h sparkline.
- **Loading:** 8 skeleton rows. **Error:** table error card with retry; map stays live.
- **Degraded:** detector feed stale → amber banner in the table header with last-received time.
- **Responsive:** table becomes full-screen list; map behind a "show map" toggle.
- **Keyboard:** `↑/↓` rows, `Enter` open, `E` acknowledge, `Shift+E` assign, `Del` dismiss (opens reason). **Deep link:** `/alerts?sev=p0&status=unacked`.

### P3 — New Investigation (wizard)
- **Layout:** right slide-over 520px over the live map; the map *is* step 1's input surface.
- **Primary:** 4 steps (input → scene → config → review) as a vertical progress spine inside the slide-over.
- **Map interaction:** step 1: draw AOI directly (MAP-mode AOI tool auto-armed) or click a cataloged footprint; step 2: candidate scene footprints render on the map, hover-syncs list↔map.
- **Panels:** none beyond the slide-over; Layers auto-shows "Scene catalog" layer during step 2.
- **Controls:** input mode tabs (AOI+time / scene ID / upload / incident link); time-window dual slider with UTC fields; AIS source select with `REAL`/`SYNTHETIC` badges; weight profile (role-gated); priority; submit.
- **Animations:** step transitions slide 220ms; scene candidates fade in as search streams; submit → slide-over collapses into a toast ("Run RUN-… queued — view") and the run's P5 opens in the rail.
- **Empty:** no scenes found → in-panel empty state with "widen window ±3 days" quick action.
- **Loading:** scene search streams results with a thin progress line per provider (`CDSE ✓ · ASF … · CACHE ✓`).
- **Error:** provider errors listed per-provider, not merged; validation errors inline at fields.
- **Degraded:** credentials down → step 2 header shows `CACHE-ONLY` chip, flow continues.
- **Responsive:** slide-over becomes full-screen sheet; AOI drawing keeps map visible above the sheet (60/40 split).
- **Keyboard:** `Enter` next step when valid, `Esc` cancels with confirm-if-dirty. **Deep link:** `/investigations/new?aoi=…&t0=…&t1=…` (pre-filled from AOI tool "investigate here").

### P4 — Incidents Registry
- **Layout:** docked left table (register) + map right; selecting an incident splits the rail into the case card.
- **Primary:** register rows: ID, name, region, opened, lifecycle status chip (open → investigating → attributed → closed), team, #runs, top suspect (if attributed) with `SYNTHETIC` badge when applicable.
- **Map interaction:** register hover ghosts location; select flies; case card's runs list hover-highlights each run's slick footprint.
- **Panels:** case drawer (deeper view): linked runs timeline, action log, notes, "start new run for this incident."
- **Controls:** lifecycle transitions (role-gated; attributed/closed = Reviewer only, with confirm sheet + note), filters, search.
- **Animations:** status chip transitions crossfade; nothing else.
- **Empty:** "No incidents. Alerts you acknowledge can be promoted here."
- **Loading:** table skeleton; map live. **Error:** register error card + retry. **Degraded:** n/a beyond global.
- **Responsive:** table-first, map toggle. **Keyboard:** `↑/↓/Enter`, `S` change status (permitted roles). **Deep link:** `/incidents/:id` opens the case drawer directly.

### P5 — Run Overview
- **Layout:** rail expands to a wide (480px) run console over the map; map shows whatever artifacts exist so far.
- **Primary:** stage tracker (11-step chain grouped into the 5 executable stages + report), each row: state, duration, cache badge, log tail expander; manifest accordion (inputs, sources, cache keys, artifact hashes with copy); provenance summary.
- **Map interaction:** completed stages' layers are live; clicking a stage row focuses its layer (camera + emphasis).
- **Panels:** log tail per stage (monospace, autoscroll toggle, 200-line window, "open full log" drawer).
- **Controls:** cancel run (confirm), retry failed stage (role-gated), re-run with same inputs (new run ID).
- **Animations:** §7.2 cascade; live RUNNING pulse; layer reveal per completion.
- **Empty:** n/a (a run always has at least a queued state).
- **Loading:** connecting to status stream → skeleton tracker with `CONNECTING` chip.
- **Error:** failed stage per §7.2; stream lost → banner + poll fallback with `POLLING 10s` chip.
- **Degraded:** cache-hit-only run → header chip `CACHE RUN` with tooltip.
- **Responsive:** console becomes full-screen sheet with a mini-map header.
- **Keyboard:** `↑/↓` stages, `Enter` open stage view, `L` toggle logs. **Deep link:** `/runs/:id/overview` (+`?stage=drift_hindcast` scrolls/expands).

### P6 — Detection & Scene view (`/runs/:id/detection`)
- **Layout:** SAT mode auto-entered, camera on footprint; rail = detection card stack.
- **Primary:** SAR raster + mask/boundary; per-slick confidence chips; scene metadata card; candidate counts (screened→segmented).
- **Map interaction:** §6 grammar (peek `X`, inspect `I`, stretch presets, swipe/blink if comparison pinned); slick click → characterization card preview.
- **Panels:** stretch/opacity floating card; EO tab present-but-honest when adapter absent.
- **Controls:** slick selector (multi-slick tabs), YOLO boxes toggle, "open characterization" per slick.
- **Animations:** raster fade-in 300ms; mask draws once on first reveal; nothing loops.
- **Empty (no detection):** raster shown + centered quiet card "No slick detected in this scene" with confidence context and "view clean-run report" link — the empty state is a *result*, not an absence.
- **Loading:** tile skeleton shimmer at low zoom only; metadata card skeleton.
- **Error:** tiles unavailable → vector-only fallback + amber notice; detection artifact missing → stage-failure card linking P5.
- **Degraded:** low-confidence-only → candidates rendered dashed with `UNCONFIRMED` chips.
- **Responsive:** rail collapses; opacity card docks bottom. **Keyboard:** `X` peek, `I` inspect, `K` blink, `[`/`]` swipe nudge. **Deep link:** encodes stretch, overlay opacity, camera, slick id.

### P7 — Characterization (`/runs/:id/characterization`)
- **Layout:** stays in current map mode; camera frames the slick; rail = metrics dossier.
- **Primary:** per-slick metric rows (area/perimeter/centroid/axes/orientation/damping — unit-labeled, copyable); age card with confidence badge and "why low" note; axes drawn on-map (major/minor as thin annotated lines when the dossier is open).
- **Map interaction:** hovering a metric highlights its geometric meaning on the map (hover "perimeter" → boundary glows once; "orientation" → axis arrow shows).
- **Panels:** rail only. **Controls:** slick tabs; download GeoJSON; copy JSON.
- **Animations:** metric↔map hover links (300ms one-shot); none else.
- **Empty:** age not estimable → age card states it plainly; missing metrics render `—` with tooltip, never 0.
- **Loading:** dossier skeleton. **Error:** artifact invalid → error card with hash + "view in P5."
- **Degraded:** n/a. **Responsive:** dossier bottom-sheet. **Keyboard:** `Tab` between slicks. **Deep link:** `?slick=SLK-001`.

### P8 — Drift Analysis (`/runs/:id/drift`)
- **Layout:** map-dominant; rail = drift console (origin card, forcing provenance, uncertainty readout); timeline is the primary control.
- **Primary:** hindcast particles, origin ring/ellipse, origin **time-window card** (the star datum: `12:47–19:47Z · 7.0 h`), forecast footprints with area chips, forcing sources with valid-time deltas.
- **Map interaction:** full §7 choreography on scrub/playback; ellipse click → sigma details; forecast ring click → horizon chip.
- **Panels:** forcing panel rows each carry provenance chip; amber flag on valid-time mismatch.
- **Controls:** timeline transport; "seed forward check" (runs the forecast-from-origin closure if the API allows; result annotates closure error on-map).
- **Animations:** §7.3, 7.9, 7.10 — this view owns the signature motion of the product.
- **Empty:** hindcast failed → detection context + failure card; **no fake origin ever renders**.
- **Loading:** particle layer streams in progressively; origin card skeleton until artifact ready.
- **Error:** artifact/diagnostics missing → console states exactly what is absent.
- **Degraded:** fallback forcing → amber chips + banner note in the console.
- **Responsive:** console → bottom sheet above timeline. **Keyboard:** full transport set (§1.4, §7.8). **Deep link:** `?t=-540&sel=origin` reproduces the exact temporal-visual state (evidence citation).

### P9 — AIS Traffic & Filtering (`/runs/:id/ais`)
- **Layout:** map-dominant; rail = funnel + exclusion ledger; timeline live.
- **Primary:** funnel bar (found → spatial → temporal → trajectory → candidates) with counts; vessel tracks time-synced; exclusion ledger rows (vessel, gate, `filter_reason`, inspect); AIS source card (`REAL` files+coverage / `SYNTHETIC` scenario id).
- **Map interaction:** ledger hover → dim vessel highlights + reason tooltip at its position; `Alt`-hover a track scrubs time; buffer geometry togglable with its query-trace popover (geometry+window provably from this run's origin artifacts).
- **Panels:** coverage warning card when AIS span ≠ window span (exact bounds shown).
- **Controls:** funnel segments clickable (filters map to that population); "show filtered" toggle; gate filter chips.
- **Animations:** funnel bar fills once on load (400ms, staggered); vessel motion per §7.5.
- **Empty:** zero traffic → "No vessels in the origin window — possible dark vessel" card with guidance link; map still shows origin + buffer.
- **Loading:** tracks stream by tile; funnel skeleton. **Error:** vessels artifact missing → stage-failure card.
- **Degraded:** decimated view chip (`SHOWING 1:4 FIXES`) at high densities.
- **Responsive:** ledger bottom-sheet. **Keyboard:** `F` cycle funnel populations, `↑/↓` ledger. **Deep link:** `?gate=trajectory&vessel=SYN-419105050`.

### P10 — Suspect Attribution (`/runs/:id/suspects`)
- **Layout:** rail expands to 420px suspects board; map shows candidates; timeline live.
- **Primary:** ranked rows (rank, name, MMSI, type, total, `SYNTHETIC` badge); expanded row: six factor bars with weights + values, evidence strings, score-arithmetic expander; anomaly inspector (events plotted on the selected track with time pins).
- **Map interaction:** select row ↔ select vessel (bidirectional, gold); evidence strings with time references are clickable → scrub timeline to that moment (evidence *replays*).
- **Panels:** weight-profile chip (`profile v3`) with view-only breakdown; synthetic banner pinned above the board when applicable.
- **Controls:** sort (rank default; sortable by any factor for analysis), compare mode (pin 2 suspects → factor bars side-by-side), "add to report" per suspect.
- **Animations:** factor bars fill once on expand (300ms); selection sync flashes the track head once.
- **Empty:** no candidates → honest empty board mirroring P9's dark-vessel card; **no forced ranking is ever displayed**.
- **Loading:** board skeleton rows. **Error:** suspects.json invalid → error card + hash + P5 link.
- **Degraded:** deprecated weight profile → gold chip "scored with profile v3 (current v5)".
- **Responsive:** board full-screen sheet, map peek header. **Keyboard:** `↑/↓` rank walk (map follows), `Enter` expand, `C` compare pin. **Deep link:** `?suspect=SYN-419001122&evidence=2` (opens row, scrubs to evidence time).

### P11 — Vessel Dossier (`/vessels/:mmsi`)
- **Layout:** drawer 560px over the map; map overlays that vessel's tracks across all runs (color-stepped by run age).
- **Primary:** identity card (+`SYNTHETIC` banner when applicable), appearance history table (incident, run, rank, score), gap/anomaly history strip, notes (role-gated).
- **Map interaction:** history row hover isolates that run's track; click loads that run context.
- **Controls:** follow (adds to a watchlist feeding P2), export dossier (audited).
- **Animations:** track layers crossfade on row hover.
- **Empty:** single-run vessel → history renders one row, no apology. **Loading:** drawer skeleton. **Error/Degraded:** standard cards.
- **Responsive:** full-screen sheet. **Keyboard:** `↑/↓` history. **Deep link:** `/vessels/SYN-419001122?run=…`.

### P12 — Review & Case Report (`/runs/:id/report`)
- **Layout:** the one view where the document leads: 720px reading column (Inter prose) left, live map preview right (each report figure is a deep-linked map state, hovering a figure loads it in the preview).
- **Primary:** auto-assembled sections (scene → … → suspects), inline annotations, mandatory provenance annex (non-removable, auto-generated: source table, valid-times, hashes, versions), review state banner (DRAFT watermark / IN REVIEW / PUBLISHED lock).
- **Map interaction:** preview only (non-interactive except zoom); "open live" jumps to the cited deep link.
- **Controls:** annotate, submit for review, approve/sign (Reviewer: name+UTC recorded), export PDF / GeoJSON bundle / JSON — exports stamped with run ID + hash; published = write-locked with a visible lock chip.
- **Animations:** figure-hover preview crossfade 220ms; publish → lock chip clicks in with one 300ms confirm tick.
- **Empty:** unattributed run → report generates honestly with a "no suspect identified" section.
- **Loading:** section skeletons as artifacts assemble. **Error:** missing artifact → its section renders a gap card (report cannot silently omit).
- **Degraded:** unreviewed export → forced `DRAFT` watermark.
- **Responsive:** single column, preview inline per figure. **Keyboard:** `⌘Enter` submit, section nav `J/K`. **Deep link:** `/runs/:id/report#suspects`.

### P13 — Runs History (`/runs`)
- **Layout:** full-width table drawer; timeline minimizes to strip; map behind shows footprints of visible rows (viewport-linked: filtering the table filters the map).
- **Primary:** run rows (ID, incident, scene date, operator, stage-state micro-tracker, provenance badges, top suspect, duration).
- **Map interaction:** row hover → footprint highlight; select → footprint + summary card.
- **Controls:** filters (date/region/status/AIS source/operator), compare (pin 2 → side-by-side sheet: origin windows, funnels, top-3), re-run.
- **Animations:** none beyond row hover.
- **Empty:** "No runs match." with clear-filters. **Loading:** skeleton rows. **Error:** standard. **Degraded:** n/a.
- **Responsive:** cards instead of rows ≤ 900px. **Keyboard:** table nav + `C` compare pin. **Deep link:** `/runs?incident=…&status=failed`.

### P14 — Data Catalog (`/data`)
- **Layout:** tabbed drawer (Scenes / AIS / Environment / Ingest) + map coverage rendering per tab.
- **Primary:** scenes: product table + footprints; AIS: region/day coverage grid (calendar-heat per region, `REAL` teal vs `SYNTHETIC` gold cells) + row counts; environment: coverage windows; ingest: job list with per-file error expanders.
- **Map interaction:** coverage cells hover → region highlight; scene rows → footprint.
- **Controls:** ingest archive (file/URL, streams progress), generate synthetic scenario (Analyst; badged end-to-end), purge per retention policy (Admin, confirm sheet).
- **Animations:** ingest progress bars only.
- **Empty:** per-tab instructive empties ("No real AIS ingested yet — synthetic scenarios are serving all runs" — honesty again).
- **Loading/Error:** per-tab skeletons; per-file ingest errors expandable, never merged.
- **Degraded:** provider-down chips on the relevant tab headers.
- **Responsive:** tabs stack. **Keyboard:** tab cycling `Alt+1..4`. **Deep link:** `/data?tab=ais&region=IN-E`.

### P15 — Models & Benchmarks (`/models`)
- **Layout:** two-column drawer: model cards left, benchmark board right; no map dependency (map idles behind, dimmed 20%).
- **Primary:** model cards (name, version, ONNX hash, deployed date, runs-used count); benchmark board (top-1/top-3/mean-rank per pipeline version, trend sparkline); hindcast honesty card ("physics — Engine B; no learned component" until changed).
- **Controls:** rerun benchmark (Analyst; queues job, streams progress), diff two versions.
- **Animations:** sparkline draws once. **Empty:** no benchmark yet → run CTA. **Loading:** cards skeleton. **Error:** standard. **Degraded:** regression detected → crimson delta chip pinned to the board header.
- **Responsive:** columns stack. **Keyboard:** n/a special. **Deep link:** `/models?compare=v1.3,v1.4`.

### P16 — Admin (`/admin/*`)
- **Layout:** classic settings drawer (nav list + form pane) — deliberately the most conventional view in the app; admin work should feel boring.
- **Primary:** users/roles CRUD; credentials with test buttons + last-success stamps (secrets write-only); weight-profile editor (sliders must sum to 1.0 with live renormalization preview and version notes); retention; thresholds.
- **Controls:** every mutation confirms with a diff preview ("before → after") because every mutation is an audit event.
- **Animations:** none. **Empty/Loading/Error:** standard forms. **Degraded:** failing credential rows self-flag amber.
- **Responsive:** standard. **Keyboard:** form order. **Deep link:** `/admin/config#weights`.

### P17 — System Health (`/system/health`)
- **Layout:** status grid drawer (services, providers, queue, storage, inference latency) + error-rate sparklines.
- **Primary:** per-service cards with state chips reusing §1.8 stage vocabulary; degraded-mode explanations in plain words.
- **Animations:** live dots only. **Empty:** n/a. **Loading:** grid skeleton. **Error:** if health API itself fails, full-view crimson card (the one place that's acceptable). **Degraded:** is the content.
- **Responsive:** cards stack. **Deep link:** `/system/health?svc=tiles`.

### P18 — Audit Log (`/system/audit`)
- **Layout:** single dense table drawer; monospace-forward; no map linkage.
- **Primary:** event rows (UTC, actor, action, target, before→after expander); filters (actor/action/date/target).
- **Controls:** export (itself audited); no delete exists anywhere in this view.
- **Animations:** none — stillness is the point. **Empty:** "No events match." **Loading:** skeleton. **Error:** standard. **Degraded:** n/a.
- **Responsive:** horizontal scroll table. **Keyboard:** table nav, `/` focuses filter. **Deep link:** `/system/audit?actor=…&action=report.publish`.

---

## 10. PRODUCTION INTERACTION RULES

The system behaves as one continuous instrument, not a website.

1. **No full-page reloads.** One SPA shell; route changes swap rail/drawer/mode state only. The map/globe canvas mounts once per session and survives every navigation. Hard reload restores full state from the URL + session store (mode, camera, `t`, selection, open panels).
2. **Persistent map.** Every view either uses the map or dims it behind a drawer — it never unmounts, never flashes, never loses camera state. Tables and settings are guests on top of the map, not replacements for it.
3. **Slide-over panels.** Creation and context tasks (P3 wizard, notification drawer, case files) arrive as right slide-overs (`--t-panel`), map remains visible and half-interactive (pan/zoom allowed, selection suspended). `Esc` closes; dirty state confirms.
4. **Modal/detail drawers.** Entity depth (vessel dossier, run console, compare sheets) uses drawers ≥ 480px with the shadow token; only destructive confirmations and sign-off ceremonies use centered modal sheets. Never more than one drawer + one sheet deep — a third level must instead navigate.
5. **Command palette.** `⌘K` reaches every route, entity, layer toggle, mode, and time jump (§1.6). Power path parity: anything demo-critical must be executable in ≤ 2 palette actions.
6. **URL deep linking.** The URL is the state: route + `?m=` mode + camera + `t` + selection + comparison. Any screen a reviewer sees can be reproduced by pasting a link — this is an evidentiary feature, not a convenience. "Copy deep link" exists on the timeline, on every entity inspector, and in every report figure.
7. **Keyboard navigation.** Global: `1–4` modes, `⌘K` palette, `Space` transport, `←/→` step, `[`/`]` rails, `R` reset, `?` shortcut overlay. Every list is arrow-navigable; every operable element focusable with visible ring; shortcut overlay (`?`) is generated from the live keymap so it can't drift.
8. **Smooth mode transitions.** §2.2 morphs; mode changes are camera events, not navigations. The back button walks mode/state history exactly as it walks routes.
9. **Persistent timeline.** Once a run context exists, the timeline survives every view (minimized where irrelevant), and `t` is global truth — every time-aware layer, card, readout, and evidence string renders against the same `t`, always.
10. **Persistent investigation context.** The open incident/run rides in the breadcrumb and rail across all views (including Data, Models, Health) until explicitly closed (`breadcrumb ✕`, confirm if unsaved report edits). Opening another run swaps context with a 220ms rail crossfade — never a reload. Context, mode, camera, `t`: those four things are the session, and the UI treats them as sacred.

**End of specification.**
