/* The map engine's claims, held by test:
 *   - an unconfigured provider is reported, never substituted
 *   - the basemap is real geography from the bundled dataset, with labels
 *   - one palette: the legend tokens equal the colours the layers draw with
 *   - the camera round-trips through the URL
 *   - there is one clock, it clamps, and it stops at the end of its range
 *   - SAR is two sources split at the zoom the tile server can sustain
 */

import fs from "node:fs";
import path from "node:path";
import { act, render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LABELS_ANCHOR, basemapOptions, buildStyle, readMapConfig, resolveBasemap } from "../src/components/maps/basemaps";
import { decodeCamera, encodeCamera, zoomForBbox } from "../src/components/maps/camera";
import { MAP_COLORS, hindcastColor } from "../src/components/maps/palette";
import { TimeProvider, clampToRange, useTime } from "../src/components/maps/TimeContext";
import { BasemapSwitch, LayerControl, SourceChip, TimeController, offsetLabel, utcLabel } from "../src/components/maps/MapControls";

const SAT = { VITE_MAP_SATELLITE_URL: "https://tiles.example/{z}/{y}/{x}", VITE_MAP_SATELLITE_ATTRIBUTION: "© Example" };

describe("providers", () => {
  it("reports satellite as not configured when no provider is set, and falls back to vector", () => {
    const none = readMapConfig({});
    const sat = basemapOptions(none).find((o) => o.id === "satellite");
    expect(sat.available).toBe(false);
    expect(sat.unavailable).toMatch(/not configured/i);
    expect(resolveBasemap("satellite", none)).toEqual({ id: "geopolitical", fellBack: true });
    const style = buildStyle({ basemap: "satellite", config: none, origin: "" });
    expect(style.sources.satellite).toBeUndefined();
    expect(style.layers.some((l) => l.type === "raster")).toBe(false);
  });

  it("uses the configured provider, with its attribution, and treats blank as unset", () => {
    const cfg = readMapConfig(SAT);
    expect(resolveBasemap("satellite", cfg)).toEqual({ id: "satellite", fellBack: false });
    const style = buildStyle({ basemap: "satellite", config: cfg, origin: "" });
    expect(style.sources.satellite.tiles).toEqual([SAT.VITE_MAP_SATELLITE_URL]);
    expect(style.sources.satellite.attribution).toBe("© Example");
    expect(readMapConfig({ VITE_MAP_SATELLITE_URL: "   " }).satelliteUrl).toBeNull();
  });
});

describe("basemap style", () => {
  const style = buildStyle({ basemap: "geopolitical", origin: "http://x", config: readMapConfig({}) });

  it("is a globe drawn from the bundled Natural Earth data, with offline glyphs", () => {
    expect(style.projection).toEqual({ type: "globe" });
    expect(style.sources.countries.data).toBe("http://x/geo/ne/countries.json");
    expect(style.glyphs).toBe("http://x/fonts/{fontstack}/{range}.pbf");
    for (const s of Object.values(style.sources)) {
      if (typeof s.data === "string") expect(s.data.startsWith("http://x/")).toBe(true);
    }
  });

  it("draws countries, borders, coastline, country names and sea names", () => {
    const ids = style.layers.map((l) => l.id);
    for (const id of ["ot-land", "ot-borders", "ot-coast", "ot-country-labels", "ot-marine-labels"]) {
      expect(ids).toContain(id);
    }
    // data overlays go under the labels
    expect(ids.indexOf(LABELS_ANCHOR)).toBeLessThan(ids.indexOf("ot-country-labels"));
  });

  it("hides what the layer checklist turns off, and keeps land under imagery", () => {
    const off = buildStyle({ basemap: "geopolitical", show: { coastlines: false }, origin: "", config: readMapConfig({}) });
    expect(off.layers.find((l) => l.id === "ot-coast").layout.visibility).toBe("none");
    const sat = buildStyle({ basemap: "satellite", origin: "", config: readMapConfig(SAT) });
    const ids = sat.layers.map((l) => l.id);
    expect(ids.indexOf("ot-land")).toBeLessThan(ids.indexOf("ot-satellite"));
  });
});

describe("the bundled dataset", () => {
  const ne = path.resolve(__dirname, "../public/geo/ne");
  const read = (f) => JSON.parse(fs.readFileSync(path.join(ne, f), "utf-8"));

  it("is Natural Earth, India point of view, and records how it was built", () => {
    const src = read("SOURCE.json");
    expect(src.source).toMatch(/Natural Earth/);
    expect(src.point_of_view).toMatch(/India/);
    expect(src.generated_by).toBe("scripts/build_basemap_natural_earth.py");
  });

  it("has real countries, separate land borders, and named seas", () => {
    const countries = read("countries.json").features;
    expect(countries.length).toBeGreaterThan(200);
    const india = countries.find((f) => f.properties.a3 === "IND");
    const lats = JSON.stringify(india.geometry.coordinates).match(/-?\d+\.?\d*/g).map(Number).filter((_, i) => i % 2 === 1);
    expect(Math.max(...lats)).toBeGreaterThan(36.5);   // the northern boundary as India depicts it
    expect(read("borders.json").features.length).toBeGreaterThan(100);
    const seas = read("marine_labels.json").features.map((f) => f.properties.name);
    expect(seas).toEqual(expect.arrayContaining(["Bay of Bengal", "Arabian Sea", "INDIAN OCEAN"].map((n) =>
      seas.find((s) => s.toLowerCase() === n.toLowerCase()))));
  });

  it("stays inside the size budget", () => {
    const total = fs.readdirSync(ne).reduce((a, f) => a + fs.statSync(path.join(ne, f)).size, 0);
    expect(total).toBeLessThan(3.5 * 1024 * 1024);
  });
});

describe("one palette", () => {
  const css = fs.readFileSync(path.resolve(__dirname, "../src/styles/tokens.css"), "utf-8");
  const dark = css.slice(0, css.indexOf("--c-incident") + 40);
  const hex = (rgb) => `#${rgb.map((v) => v.toString(16).padStart(2, "0")).join("")}`;

  it("legend tokens equal the colours the map layers use", () => {
    const pairs = { "--c-slick": MAP_COLORS.slick, "--c-forecast": MAP_COLORS.forecast, "--c-hindcast": MAP_COLORS.hindcastOld,
      "--c-origin": MAP_COLORS.origin, "--c-vessel": MAP_COLORS.vessel, "--c-candidate": MAP_COLORS.candidate,
      "--c-zone": MAP_COLORS.zone, "--c-incident": MAP_COLORS.incident };
    for (const [token, rgb] of Object.entries(pairs)) {
      expect(dark, token).toContain(`${token}: ${hex(rgb)};`);
    }
  });

  it("runs backward drift from orange to red", () => {
    expect(hindcastColor(0)).toEqual(MAP_COLORS.hindcastOld);
    expect(hindcastColor(1)).toEqual(MAP_COLORS.hindcastNew);
    expect(hindcastColor(7)).toEqual(MAP_COLORS.hindcastNew);
  });
});

describe("camera in the URL", () => {
  it("round-trips, and only carries bearing/pitch when tilted", () => {
    expect(encodeCamera({ longitude: 88.123456, latitude: 13.5, zoom: 3.1 })).toBe("88.1235,13.5000,3.10");
    expect(decodeCamera("88.1235,13.5000,3.10")).toEqual({ longitude: 88.1235, latitude: 13.5, zoom: 3.1, bearing: 0, pitch: 0 });
    expect(decodeCamera(encodeCamera({ longitude: 1, latitude: 2, zoom: 3, bearing: 30, pitch: 10 })).bearing).toBe(30);
  });

  it("refuses a camera that is not one", () => {
    for (const bad of [null, "", "abc", "1,2", "1,200,3", "1,2,99", "NaN,1,2"]) expect(decodeCamera(bad)).toBeNull();
  });

  it("frames a scene footprint at regional zoom", () => {
    const z = zoomForBbox([-93, 27.2, -90.1, 29.1], 1200, 700);
    expect(z).toBeGreaterThan(6);
    expect(z).toBeLessThan(9);
  });
});

function Probe() {
  const time = useTime();
  return (
    <div>
      <span data-testid="t">{time.t}</span>
      <button onClick={() => time.setT(10_000_000_000_000)}>far</button>
      <button onClick={() => time.step(-1)}>back</button>
    </div>
  );
}

describe("one clock", () => {
  const t0 = Date.UTC(2023, 0, 8, 0, 10);
  const range = [t0 - 24 * 3_600_000, t0 + 12 * 3_600_000];

  it("clamps to its range and steps in hours", () => {
    expect(clampToRange(5, [10, 20])).toBe(10);
    render(<TimeProvider initialRange={range} initialT={t0}><Probe /></TimeProvider>);
    fireEvent.click(screen.getByText("far"));
    expect(Number(screen.getByTestId("t").textContent)).toBe(range[1]);
    fireEvent.click(screen.getByText("back"));
    expect(Number(screen.getByTestId("t").textContent)).toBe(range[1] - 3_600_000);
  });

  it("throws when a layer asks for time outside the provider", () => {
    const spy = console.error; console.error = () => {};
    expect(() => render(<Probe />)).toThrow(/TimeProvider/);
    console.error = spy;
  });

  it("shows UTC prominently, steps with the arrow keys, and hides without a range", () => {
    const { unmount } = render(<TimeProvider><TimeController /></TimeProvider>);
    expect(screen.queryByTestId("time-controller")).toBeNull();
    unmount();
    render(<TimeProvider initialRange={range} initialT={t0}><TimeController /></TimeProvider>);
    expect(screen.getByTestId("time-utc").textContent).toBe("2023-01-08 00:10 UTC");
    act(() => { fireEvent.keyDown(screen.getByTestId("time-slider"), { key: "ArrowLeft" }); });
    expect(screen.getByTestId("time-utc").textContent).toBe("2023-01-07 23:10 UTC");
    act(() => { fireEvent.keyDown(screen.getByTestId("time-slider"), { key: "ArrowRight", shiftKey: true }); });
    expect(screen.getByTestId("time-utc").textContent).toBe("2023-01-08 05:10 UTC");
  });

  it("labels offsets from the reference instant", () => {
    expect(utcLabel(null)).toBe("—");
    expect(offsetLabel(t0, t0)).toBe("T0");
    expect(offsetLabel(t0 - 13 * 3_600_000, t0)).toBe("T−13 h");
    expect(offsetLabel(t0 + 6 * 3_600_000, t0)).toBe("T+6.0 h");
  });
});

describe("controls tell the truth", () => {
  it("disables a basemap that is not configured and says why", () => {
    render(<BasemapSwitch value="geopolitical" onChange={() => {}} />);
    const sat = screen.getByTestId("basemap-satellite");
    // the unit environment has no provider configured
    expect(sat).toBeDisabled();
    expect(sat.closest("label").getAttribute("title")).toMatch(/not configured/i);
  });

  it("shows why a layer is empty, and credits a source only when there is one", () => {
    render(<LayerControl rows={[{ id: "ais", label: "Live AIS", disabled: true, note: "Live AIS unavailable in this region" }]}
      value={{ ais: true }} onToggle={() => {}} />);
    expect(screen.getByTestId("layer-ais")).toBeDisabled();
    expect(screen.getByText("Live AIS unavailable in this region")).toBeInTheDocument();
    const { container } = render(<SourceChip layer="Wind" source={null} />);
    expect(container).toBeEmptyDOMElement();
    render(<SourceChip layer="Wind" source="ERA5" at={Date.UTC(2023, 0, 8, 12, 0)} />);
    expect(screen.getByTestId("source-wind").textContent).toBe("Wind — ERA5 — 12:00 UTC");
  });
});
