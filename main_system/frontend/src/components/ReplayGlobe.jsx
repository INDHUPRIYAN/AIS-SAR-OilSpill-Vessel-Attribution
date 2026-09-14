/* Incident replay, on the 3D globe.
 *
 * The same frame the 2D command map receives, drawn on the shared GlobeScene
 * so the replay can be watched as a geographic reconstruction: where the
 * traffic was, where the slick was found, where the drift says it came from,
 * and where it is predicted to go.
 *
 * ONE THING THE GLOBE CANNOT DO, stated rather than faked: the SAR raster.
 * `BitmapLayer` reprojects a Web Mercator image against the viewport, and on a
 * sphere each tile collapses into a skewed quad. So the scene imagery, the
 * detection mask and the scan line stay MAP-only, and the caller offers a MAP
 * toggle for them. Everything that is vector geometry -- tracks, slick,
 * origin cloud, forecast, the scene footprint -- renders here identically.
 *
 * Every position comes from the run's contract files. Vessel motion
 * interpolates real AIS fixes; the hindcast replays the drift engine's own
 * particles. The only synthetic motion is the reveal timing.
 */

import { useMemo } from "react";
import {
  GeoJsonLayer, IconLayer, PathLayer, PolygonLayer, ScatterplotLayer, TextLayer,
} from "@deck.gl/layers";

import GlobeScene from "./globe/GlobeScene";
import { MapTip } from "./ui";
import {
  circleRing, destination, trackPathUntil, trackStateAt, clamp01, easeOut, lerp,
} from "../lib/replay";

/* Same semantic palette as the 2D replay, so a colour means the same thing in
 * both views. */
export const REPLAY_COLORS = {
  slick: [245, 158, 11],
  hindcast: [56, 189, 248],
  origin: [45, 212, 191],
  forecast: [167, 139, 250],
  vessel: [130, 148, 178],
  candidate: [244, 63, 94],
  scene: [130, 148, 178],
};

/** A triangular ship glyph as an SVG data URI, so vessels can be rotated to
 *  their heading without a sprite sheet. */
const SHIP_ICON = {
  /* width/height MUST be on the <svg> element itself. Without them the image
   * has no natural dimensions and `createImageBitmap` refuses it, so deck
   * logs an error and draws no vessels at all. */
  url: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
    + '<polygon points="12,2 20,22 12,17 4,22" fill="white"/></svg>')}`,
  width: 24, height: 24, anchorY: 12, mask: true,
};

export default function ReplayGlobe({
  bundle: b, frame, effects, toggles, viewState, onViewStateChange,
  onPointerMove, onPointerLeave, theme = "dark", selectedMmsi, onSelect, hover, onHover,
  showLocator = true,
}) {
  const { simT } = frame || {};

  const layers = useMemo(() => {
    if (!b) return [];
    const out = [];
    const C = REPLAY_COLORS;

    /* --- incident locator ------------------------------------------------
     * The globe's job is orientation: at the zoom where a planet still reads
     * as a planet, a 1.7 km slick is smaller than a pixel. So the incident
     * gets a locator -- concentric rings and a label at the REAL scene centre
     * -- which stays legible while the fine geometry below scales up as the
     * operator zooms in. It marks a position; it never stands in for the
     * slick's shape, which is drawn from the contract file like everything
     * else. */
    if (b.sceneCenter && showLocator) {
      const [cx, cy] = b.sceneCenter;
      const pulse = 1 + 0.35 * Math.sin((frame?.tick ?? 0) / 14);
      out.push(new ScatterplotLayer({
        id: "rg-locator-halo",
        data: [{ pos: [cx, cy] }],
        getPosition: (d) => d.pos,
        getRadius: 26 * pulse, radiusUnits: "pixels",
        getFillColor: [...C.slick, 26],
        pickable: false,
        updateTriggers: { getRadius: frame?.tick },
      }));
      out.push(new ScatterplotLayer({
        id: "rg-locator",
        data: [{ pos: [cx, cy] }],
        getPosition: (d) => d.pos,
        getRadius: 6, radiusUnits: "pixels",
        stroked: true, filled: true,
        getFillColor: [...C.slick, 220],
        getLineColor: [255, 245, 230, 240],
        getLineWidth: 1.5, lineWidthUnits: "pixels",
        pickable: true,
        onHover: (i) => onHover?.(i.object ? {
          x: i.x, y: i.y, title: "Incident location",
          rows: [["scene", b.sceneMeta?.scene_id ?? "—"],
                 ["place", b.placeName ?? "—"],
                 ["acquired", String(b.sceneMeta?.acquired_utc || "").replace("T", " ").slice(0, 16)]],
        } : null),
      }));
      out.push(new TextLayer({
        id: "rg-locator-label",
        data: [{ pos: [cx, cy] }],
        getPosition: (d) => d.pos,
        getText: () => (b.placeName || "INCIDENT").toUpperCase(),
        getSize: 10, sizeUnits: "pixels",
        getColor: [...C.slick, 240],
        getPixelOffset: [0, -22],
        fontFamily: "JetBrains Mono, ui-monospace, monospace",
        getTextAnchor: "middle", getAlignmentBaseline: "bottom",
        characterSet: "auto", fontSettings: { sdf: true },
        outlineWidth: 2, outlineColor: [4, 10, 20, 230],
        pickable: false,
      }));
    }

    /* --- scene footprint ------------------------------------------------ */
    const bbox = b.sceneMeta?.bbox;
    if (bbox?.length === 4) {
      const [w, s, e, n] = bbox;
      out.push(new PathLayer({
        id: "rg-scene",
        data: [{ path: [[w, s], [e, s], [e, n], [w, n], [w, s]] }],
        getPath: (d) => d.path,
        getColor: [...C.scene, 150],
        getWidth: 1.2, widthUnits: "pixels",
        pickable: false,
      }));
    }

    /* --- forecast, furthest horizon faintest ---------------------------- */
    if (toggles?.forecast && b.forecast?.features?.length && effects.fcAlpha > 0) {
      const feats = [...b.forecast.features]
        .sort((x, y) => (x.properties.horizon_h ?? 0) - (y.properties.horizon_h ?? 0));
      feats.forEach((f, i) => {
        const reveal = clamp01(effects.fcAlpha * feats.length - i);
        if (reveal <= 0) return;
        out.push(new GeoJsonLayer({
          id: `rg-forecast-${i}`,
          data: { type: "FeatureCollection", features: [f] },
          filled: true, stroked: true,
          getFillColor: [...C.forecast, reveal * (34 - i * 8)],
          getLineColor: [...C.forecast, reveal * 190],
          getLineWidth: 1.4, lineWidthUnits: "pixels",
          pickable: true,
          onHover: (info) => onHover?.(info.object ? {
            x: info.x, y: info.y, title: `Forecast +${f.properties.horizon_h} h`,
            rows: [["valid", String(f.properties.valid_utc || "").replace("T", " ").slice(0, 16)],
                   ["area", `${f.properties.area_km2} km²`],
                   ["confidence", f.properties.confidence_level]],
          } : null),
        }));
      });
    }

    /* --- hindcast particle cloud at the current timestep ----------------- */
    if (toggles?.hindcast && b.trajectories?.length && effects.hindTime != null) {
      const k = Math.round(effects.hindTime);
      const heads = [];
      for (const t of b.trajectories) {
        const idx = t.steps.indexOf(k);
        if (idx !== -1) heads.push({ pos: t.path[idx], w: t.weight });
      }
      if (heads.length) {
        out.push(new ScatterplotLayer({
          id: "rg-hindcast",
          data: heads,
          getPosition: (d) => d.pos,
          getRadius: 2.2, radiusUnits: "pixels", radiusMinPixels: 1, radiusMaxPixels: 3,
          getFillColor: (d) => [...C.hindcast, 60 + d.w * 180],
          pickable: false,
          updateTriggers: { getPosition: k, getFillColor: k },
        }));
      }
      /* The engine's own confidence ellipse for this step -- the model's
       * uncertainty, not a drawn circle. */
      const ell = b.ellipses?.[Math.min(k, (b.ellipses?.length || 1) - 1)];
      if (ell && k > 0) {
        out.push(new GeoJsonLayer({
          id: "rg-hind-ellipse",
          data: { type: "FeatureCollection", features: [ell] },
          stroked: true, filled: false,
          getLineColor: [...C.hindcast, 200],
          getLineWidth: 1.6, lineWidthUnits: "pixels",
          pickable: false,
          updateTriggers: { getLineColor: k },
        }));
      }
    }

    /* --- probable origin ------------------------------------------------ */
    if (toggles?.origin && b.originCenter && effects.originAlpha > 0) {
      const [ox, oy] = b.originCenter;
      const a = effects.originAlpha;
      const baseKm = Math.max(b.uncertaintyKm ?? 1.2, 0.9);
      out.push(new PolygonLayer({
        id: "rg-origin-disc",
        data: [{ poly: circleRing(ox, oy, baseKm, 72) }],
        getPolygon: (d) => d.poly,
        filled: true, stroked: true,
        getFillColor: [...C.origin, 30 * a],
        getLineColor: [...C.origin, 200 * a],
        getLineWidth: 1.6, lineWidthUnits: "pixels",
        pickable: true,
        onHover: (info) => onHover?.(info.object ? {
          x: info.x, y: info.y, title: "Probable origin",
          rows: [["uncertainty", `± ${baseKm.toFixed(1)} km`],
                 ["confidence", b.originEllipse?.properties?.confidence_level ?? "—"]],
        } : null),
      }));
      out.push(new TextLayer({
        id: "rg-origin-label",
        data: [{ pos: destination(ox, oy, baseKm * 1.6, 0) }],
        getPosition: (d) => d.pos,
        getText: () => "PROBABLE ORIGIN",
        getSize: 10, sizeUnits: "pixels",
        getColor: [...C.origin, 235 * a],
        fontFamily: "JetBrains Mono, ui-monospace, monospace",
        getTextAnchor: "middle", getAlignmentBaseline: "bottom",
        characterSet: "auto", fontSettings: { sdf: true },
        outlineWidth: 2, outlineColor: [4, 10, 20, 220],
        pickable: false,
      }));
    }

    /* --- vessel tracks, growing with the clock -------------------------- */
    if (toggles?.vessels && b.tracks?.length) {
      const fade = (t) => {
        if (t.rank) return 1;
        if (!effects.filterFade) return t.filtered ? 0.55 : 0.85;
        return effects.filterFade(t);
      };
      const growing = effects.aisGrow;
      const data = b.tracks.map((t) => ({
        ...t, alpha: fade(t),
        visPath: growing ? trackPathUntil(t, simT) : t.path,
      })).filter((t) => t.visPath && t.alpha > 0.02);

      out.push(new PathLayer({
        id: "rg-tracks",
        data,
        getPath: (d) => d.visPath,
        getColor: (d) => (d.rank === 1 ? [...C.candidate, 245]
          : d.rank ? [...C.candidate, 150] : [...C.vessel, 110 * d.alpha]),
        getWidth: (d) => (d.rank === 1 ? 2.6 : d.rank ? 1.8 : 1),
        widthUnits: "pixels", widthMinPixels: 1,
        pickable: true,
        onClick: (i) => i.object && onSelect?.(i.object.mmsi),
        onHover: (i) => onHover?.(i.object ? vesselTip(i, simT) : null),
        updateTriggers: { getPath: [simT, growing], getColor: effects.filterTick },
      }));

      /* Ship glyphs at their exact interpolated position, rotated to the
       * heading the vessel actually transmitted. */
      const ships = data.map((t) => {
        const st = trackStateAt(t, simT);
        return st ? { ...t, pos: st.pos, heading: st.heading, sogNow: st.sog } : null;
      }).filter(Boolean);

      out.push(new IconLayer({
        id: "rg-ships",
        data: ships,
        getPosition: (d) => d.pos,
        getIcon: () => SHIP_ICON,
        getSize: (d) => (d.rank === 1 ? 17 : d.rank ? 14 : 10),
        sizeUnits: "pixels",
        getAngle: (d) => -(d.heading ?? 0),
        getColor: (d) => (d.rank === 1 ? [...C.candidate, 255]
          : d.rank ? [...C.candidate, 220] : [...C.vessel, 70 + 175 * d.alpha]),
        billboard: true,
        pickable: true,
        onClick: (i) => i.object && onSelect?.(i.object.mmsi),
        onHover: (i) => onHover?.(i.object ? vesselTip(i, simT) : null),
        updateTriggers: { getPosition: simT, getAngle: simT, getColor: effects.filterTick },
      }));

      const sel = ships.find((s) => s.mmsi === selectedMmsi);
      if (sel) {
        out.push(new ScatterplotLayer({
          id: "rg-selected",
          data: [sel],
          getPosition: (d) => d.pos,
          getRadius: 13, radiusUnits: "pixels",
          stroked: true, filled: false,
          getLineColor: [240, 244, 250, 220], getLineWidth: 1.6, lineWidthUnits: "pixels",
          pickable: false,
          updateTriggers: { getPosition: simT },
        }));
      }

      /* Attribution lock-on: closing arcs around the top candidate. */
      if (effects.lockOn > 0 && b.top) {
        const topTrack = b.tracks.find((t) => t.rank === 1);
        const st = topTrack && trackStateAt(topTrack, simT);
        if (st) {
          const lk = easeOut(effects.lockOn);
          const rKm = lerp(6, 1.2, lk);
          [0, 90, 180, 270].forEach((off) => {
            const arc = [];
            for (let d = 0; d <= 55; d += 5) {
              arc.push(destination(st.pos[0], st.pos[1], rKm, off + frame.tick * 0.8 + d));
            }
            out.push(new PathLayer({
              id: `rg-lock-${off}`,
              data: [{ path: arc }],
              getPath: (d) => d.path,
              getColor: [...C.candidate, 235 * lk],
              getWidth: 2, widthUnits: "pixels",
              pickable: false,
              updateTriggers: { getPath: frame.tick },
            }));
          });
        }
      }
    }

    /* --- the observed slick, on top ------------------------------------- */
    if (toggles?.slick && b.slick?.features?.length && effects.slickAlpha > 0) {
      const a = effects.slickAlpha;
      out.push(new GeoJsonLayer({
        id: "rg-slick",
        data: b.slick,
        filled: true, stroked: true,
        getFillColor: [...C.slick, 90 * a],
        getLineColor: [...C.slick, 255 * a],
        getLineWidth: 2.2, lineWidthUnits: "pixels", lineWidthMinPixels: 1,
        pickable: true,
        onHover: (i) => onHover?.(i.object ? {
          x: i.x, y: i.y,
          title: `${i.object.properties?.slick_id ?? "Detected slick"} · OBSERVED`,
          rows: [["area", `${i.object.properties?.area_km2} km²`],
                 ["confidence", `${(i.object.properties?.confidence * 100).toFixed(1)}%`],
                 ["orientation", `${i.object.properties?.orientation_deg}°`]],
        } : null),
      }));
    }

    return out;
  }, [b, frame, simT, effects, toggles, selectedMmsi, onSelect, onHover, showLocator]);

  return (
    <GlobeScene
      viewState={viewState}
      onViewStateChange={onViewStateChange}
      onPointerMove={onPointerMove}
      onPointerLeave={onPointerLeave}
      basemap="canvas"
      theme={theme}
      graticule
      layers={layers}
      testid="replay-globe"
    >
      {hover && <MapTip x={hover.x + 12} y={hover.y + 12} title={hover.title}
        rows={hover.rows} testid="replay-globe-tip" />}
    </GlobeScene>
  );
}

function vesselTip(info, simT) {
  const t = info.object;
  const st = simT != null ? trackStateAt(t, simT) : null;
  return {
    x: info.x, y: info.y,
    title: `${t.name ?? `MMSI ${t.mmsi}`}${t.rank ? ` · RANK #${t.rank}` : ""}`,
    rows: [
      ["mmsi", String(t.mmsi)],
      ["type", t.type ?? "—"],
      ...(st?.sog != null ? [["speed", `${st.sog.toFixed(1)} kn`]] : []),
      ...(t.rank ? [["score", `${(t.score * 100).toFixed(0)}%`]]
        : t.filtered ? [["eliminated", t.filterReason ?? ""]]
          : [["status", "under analysis"]]),
    ],
  };
}
