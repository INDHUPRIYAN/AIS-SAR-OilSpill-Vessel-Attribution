/* A slick is drawn where the run measured it, or not at all.
 *
 * Pins the workspace's geolocation rules: an invalid geometry is refused
 * (never relocated), a slick that meets the coastline is flagged (never
 * hidden), and no investigation coordinate, score or vessel is baked into the
 * workspace's source. */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { landShare, pointOnLand, validateSlick } from "../src/lib/geovalidate";

const poly = (ring, props = {}) => ({ type: "FeatureCollection", features: [{ type: "Feature", properties: props, geometry: { type: "Polygon", coordinates: [ring] } }] });
const box = (w, s, e, n) => [[w, s], [e, s], [e, n], [w, n], [w, s]];
/* a square island with a lake in it */
const LAND = { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [box(10, 10, 20, 20), box(14, 14, 16, 16)] } }] };

describe("validateSlick", () => {
  const scene = [-93, 27.2, -90, 29.2];
  it("accepts a slick inside the scene it was segmented from", () => {
    expect(validateSlick(poly(box(-90.2, 27.78, -90.17, 27.86)), scene)).toEqual({ ok: true, reason: null });
  });
  it("treats 'nothing segmented' as a result, not an error", () => {
    expect(validateSlick({ type: "FeatureCollection", features: [] }, scene).ok).toBe(true);
    expect(validateSlick(null, scene).ok).toBe(true);
  });
  it("refuses a slick far outside its own scene rather than drawing it", () => {
    const r = validateSlick(poly(box(80.2, 13.0, 80.4, 13.1)), scene);
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/outside the footprint/);
  });
  it("refuses pixel or projected units, NaN and empty geometry", () => {
    expect(validateSlick(poly(box(512, 300, 900, 640)), null).reason).toMatch(/WGS84/);
    expect(validateSlick(poly([[NaN, 1], [2, 2], [3, 3]]), null).reason).toMatch(/non-numeric/);
    expect(validateSlick({ features: [{ geometry: { type: "Point", coordinates: [1, 1] }, properties: {} }] }, null).reason).toMatch(/no polygon/);
  });
  it("still validates when the scene footprint is unknown", () => {
    expect(validateSlick(poly(box(1, 1, 2, 2)), null).ok).toBe(true);
  });
});

describe("land", () => {
  it("knows land from sea, and a lake from land", () => {
    expect(pointOnLand(LAND, 12, 12)).toBe(true);
    expect(pointOnLand(LAND, 15, 15)).toBe(false);     // the hole
    expect(pointOnLand(LAND, 30, 30)).toBe(false);
  });
  it("reports how much of a slick meets the coastline, and its centroid", () => {
    const sea = landShare(LAND, poly(box(30, 30, 31, 31), { centroid: [30.5, 30.5] }));
    expect(sea).toEqual({ share: 0, centroidOnLand: false });
    const ashore = landShare(LAND, poly(box(11, 11, 12, 12), { centroid: [11.5, 11.5] }));
    expect(ashore.share).toBe(1);
    expect(ashore.centroidOnLand).toBe(true);
    const straddle = landShare(LAND, poly(box(19, 11, 21, 12), { centroid: [20.5, 11.5] }));
    expect(straddle.share).toBeGreaterThan(0);
    expect(straddle.share).toBeLessThan(1);
    expect(straddle.centroidOnLand).toBe(false);
  });
  it("says nothing when there is no coastline to test against", () => {
    expect(landShare(null, poly(box(1, 1, 2, 2)))).toBeNull();
  });
});

/* The workspace may hold UI constants; it may not hold an investigation. */
describe("no investigation values in the workspace source", () => {
  const roots = ["src/components/workspace", "src/lib/cinematic.js", "src/lib/useCinematic.js", "src/lib/geovalidate.js", "src/pages/SarDatabase.jsx"];
  const files = [];
  const walk = (p) => (statSync(p).isDirectory() ? readdirSync(p).forEach((f) => walk(join(p, f))) : /\.(jsx?|mjs)$/.test(p) && files.push(p));
  roots.forEach(walk);

  it("finds the files it is meant to police", () => expect(files.length).toBeGreaterThan(10));

  it.each([
    ["an MMSI", /\b[2-7]\d{8}\b/],
    ["an IMO number", /\bIMO\s*\d{7}\b/i],
    ["a slick area", /\b\d+(\.\d+)?\s*km²(?!`)/],
    ["a Sentinel-1 product id", /S1[AB]_IW_GRDH_1S[DS][VH]_\d{8}T\d{6}/],
    ["a latitude/longitude pair", /\b\d{1,2}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}\b/],
  ])("contains no hardcoded %s", (_name, pattern) => {
    const hits = [];
    for (const f of files) {
      readFileSync(f, "utf-8").split("\n").forEach((line, i) => {
        const code = line.replace(/\/\*.*?\*\/|\/\/.*$|^\s*\*.*$/g, "").replace(/placeholder="[^"]*"/g, "");
        if (pattern.test(code)) hits.push(`${f}:${i + 1}  ${line.trim().slice(0, 90)}`);
      });
    }
    expect(hits, hits.join("\n")).toEqual([]);
  });
});
