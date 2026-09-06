# Sensor Choice and Resolution Justification

**PS 26143 (NTRO) · OilGuard AI**
*Why Sentinel-1 SAR at ~10 m is the right primary sensor, why very-high-resolution
optical is not, and what a tiered future architecture looks like. Deck-ready arguments;
one section per slide.*

---

## 1. The detection problem is contrast-limited, not resolution-limited

Oil damps the sea's capillary and short gravity waves. Under moderate wind, the sea is
radar-bright and a slick is radar-dark: detection is a **contrast** measurement (our
Engine A reports it as the damping ratio, mean sea dB − mean slick dB), not a
shape-recognition problem needing fine detail.

The GSD maths:

- Sentinel-1 IW GRDH: **10 m pixel spacing** (~20 × 22 m spatial resolution), 250 km
  swath.
- An operationally significant slick is hundreds of metres to tens of km long. Even a
  minimal 100 m × 1 km discharge ribbon spans **10 × 100 pixels ≈ 1,000 pixels** — far
  above any detector's minimum-object size.
- The smallest slick we would ever alert on (~50 m wide) is still 5 pixels across.
  Nothing enforcement-relevant is lost at 10 m.

What actually limits detection is **wind**: below ~2–3 m/s the whole sea is smooth
(everything looks like oil), above ~10–12 m/s slicks disperse and the damping contrast
collapses. No amount of resolution fixes either regime. Resolution is not our binding
constraint; radiometry and wind are.

## 2. Why not VHR optical (30–50 cm)?

| Constraint | VHR optical | Sentinel-1 SAR |
|---|---|---|
| Clouds | blind — Indian coasts are cloud-covered much of the monsoon year | sees through cloud |
| Night | blind — and illegal discharges favour darkness | day/night identical |
| Swath | ~10–20 km — surveying an EEZ is thousands of taskings | 250 km per pass |
| Cost | commercial tasking, per-scene fees | free and open (Copernicus) |
| Physics | sun-glint dependent; slick contrast is viewing-geometry luck | damping physics works at all look angles in the wind window |
| Revisit | on-demand only, days of lead time | systematic, every pass archived |

For the *detection and monitoring* mission — routine EEZ surveillance, historic
retrospectives, guaranteed revisit — VHR optical fails on every axis that matters.
Where VHR genuinely wins is **vessel identification** (reading a hull). But in this
system, vessel identity comes from **AIS correlation**, which is precisely the pipeline
we built; VHR is a complement for the endgame, not a substitute for the pipeline.

A retrospective case ("oil came ashore last week — which vessel?") settles it: you
cannot task a commercial optical satellite into the past. The free, systematically
archived Sentinel-1 record is the only sensor that was *already looking*.

## 3. Future scope — a tiered SAR → VHR architecture

The production evolution is a cueing chain, each tier paying for the next:

1. **Tier 1 — Wide-area SAR (built).** Sentinel-1 (plus RISAT/EOS-04 as an indigenous
   feed) sweeps the EEZ; detection + drift + AIS attribution run automatically.
2. **Tier 2 — Tasked VHR on suspects (future).** When attribution yields a high-score
   suspect *without* AIS confirmation (dark vessel, spoofed identity), the origin
   forecast gives a precise look-point to task VHR optical or spotlight-mode SAR for
   hull-level confirmation.
3. **Tier 3 — Cross-sensor fusion (future).** SAR ship detections (CFAR) crossed
   against AIS to surface dark vessels as attribution candidates; VHR imagery attached
   to the investigation bundle as corroborating evidence.

This keeps the always-on layer free and wide, and spends expensive narrow sensors only
where the automated pipeline has already localised a target — the economically and
operationally sound ordering.

## 4. Related standing rule — super-resolution

Super-resolution was evaluated as an approach and **deliberately excluded**; no SR code
exists in this repository. All geometry — area, perimeter, ellipse axes, orientation,
centroid — is computed from native-resolution pixels. SAR speckle is multiplicative
noise, not detail; SR models sharpen it into fabricated texture, and our geometry feeds
drift, which feeds attribution, which could support an accusation. Restoration is
defensible; generation is not. Should SR ever be added for display, this rule keeps it
out of the measurement path.
