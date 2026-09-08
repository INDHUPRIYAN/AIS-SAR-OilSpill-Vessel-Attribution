/* The measure tool's arithmetic.
 *
 * These are pinned against constants that exist outside this codebase — a
 * degree of latitude, the definition of the nautical mile, half the earth's
 * circumference — rather than against numbers this implementation happened to
 * produce. A test that records the current output only proves the code has
 * not changed; it cannot notice that the code was wrong from the start.
 *
 * The failure they exist to catch is the quiet one: a ruler measuring screen
 * distance on a web-mercator map reads correctly near the equator and is 13%
 * long in the Gulf of Mexico and 84% long in the Baltic — plausible numbers,
 * on the wrong scale, in exactly the two basins this project works in.
 */

import { describe, expect, it } from "vitest";

import {
  bearingDeg, formatMeasurement, haversineKm, kmToNm, pathLengthKm, segments,
} from "../src/lib/geodesy";

describe("distance", () => {
  it("measures one degree of latitude as 111.195 km", () => {
    expect(haversineKm([0, 0], [0, 1])).toBeCloseTo(111.1951, 3);
  });

  it("makes one degree of latitude 60 nautical miles, as the definition says", () => {
    // The nautical mile was defined as a minute of arc. If this drifts, the
    // km→nm conversion or the earth radius is wrong.
    expect(kmToNm(haversineKm([0, 0], [0, 1]))).toBeCloseTo(60.04, 2);
  });

  it("measures half the earth's circumference across the globe", () => {
    expect(haversineKm([0, 0], [180, 0])).toBeCloseTo(20015.11, 1);
  });

  it("returns zero for a point measured against itself", () => {
    // Guards the asin domain: floating point can push the argument above 1.
    expect(haversineKm([-92.34, 28.11], [-92.34, 28.11])).toBe(0);
  });

  it("crosses the antimeridian without measuring the long way round", () => {
    // 179.5°E to 179.5°W is one degree apart, not 359.
    expect(haversineKm([179.5, 0], [-179.5, 0])).toBeCloseTo(111.195, 2);
  });
});

describe("it is a distance on the globe, not on the projection", () => {
  it("shrinks a degree of longitude as latitude rises", () => {
    const gulf = haversineKm([0, 28], [1, 28]);
    const baltic = haversineKm([0, 57], [1, 57]);
    expect(gulf).toBeCloseTo(98.18, 1);
    expect(baltic).toBeCloseTo(60.56, 1);
    // A ruler that measured projected pixels would report these as equal.
    expect(baltic / gulf).toBeCloseTo(
      Math.cos((57 * Math.PI) / 180) / Math.cos((28 * Math.PI) / 180), 4);
  });
});

describe("bearing", () => {
  it("reads 0° due north and 90° due east", () => {
    expect(bearingDeg([0, 0], [0, 1])).toBeCloseTo(0, 6);
    expect(bearingDeg([0, 0], [1, 0])).toBeCloseTo(90, 6);
  });

  it("reads 180° due south and 270° due west, never a negative angle", () => {
    expect(bearingDeg([0, 1], [0, 0])).toBeCloseTo(180, 6);
    expect(bearingDeg([1, 0], [0, 0])).toBeCloseTo(270, 6);
  });
});

describe("segments", () => {
  const path = [[-92.0, 28.0], [-91.0, 28.5], [-90.5, 29.0]];

  it("returns one leg per pair of points", () => {
    expect(segments(path)).toHaveLength(2);
    expect(segments([[0, 0]])).toEqual([]);
    expect(segments([])).toEqual([]);
    expect(segments(undefined)).toEqual([]);
  });

  it("carries km, nm and bearing on every leg", () => {
    const [first] = segments(path);
    expect(first.km).toBeCloseTo(112.63, 1);
    expect(first.nm).toBeCloseTo(first.km / 1.852, 9);
    expect(first.bearingDeg).toBeGreaterThan(0);
    expect(first.bearingDeg).toBeLessThan(90);
  });

  it("is the same arithmetic the total uses", () => {
    // The map draws a label per leg and the panel prints a total. A reader who
    // adds the labels must land on the total, so the total is defined as this
    // sum rather than computed a second way.
    const sum = segments(path).reduce((t, leg) => t + leg.km, 0);
    expect(pathLengthKm(path)).toBe(sum);
  });
});

describe("formatMeasurement", () => {
  it("returns nothing for fewer than two points", () => {
    expect(formatMeasurement([])).toBeNull();
    expect(formatMeasurement([[0, 0]])).toBeNull();
    expect(formatMeasurement(null)).toBeNull();
  });

  it("reports both units and the bearing in one line", () => {
    const m = formatMeasurement([[0, 0], [0, 1]]);
    expect(m.text).toBe("111.20 km · 60.04 nm · 0°");
  });

  it("states its own geodesy, so a screenshot carries its method", () => {
    const m = formatMeasurement([[0, 0], [0, 1]]);
    expect(m.method).toContain("haversine");
    expect(m.method).toContain("6371.0088");
  });

  it("takes the bearing start-to-end, not leg by leg", () => {
    // A dog-leg east then north ends up north-east of where it started; the
    // headline bearing describes the whole measurement, and the panel says so.
    const m = formatMeasurement([[0, 0], [1, 0], [1, 1]]);
    expect(m.bearingDeg).toBeGreaterThan(0);
    expect(m.bearingDeg).toBeLessThan(90);
    expect(m.km).toBeCloseTo(pathLengthKm([[0, 0], [1, 0], [1, 1]]), 9);
  });
});
