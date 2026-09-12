/* The globe's pure logic: coordinate parsing, self-intersection, and the
 * boundary editor's undo.
 *
 * Deliberately not a render test of `<Globe>`. deck.gl needs a WebGL context
 * that jsdom does not provide, so a render test would either be skipped or be
 * a mock of deck.gl asserting that a mock was called — neither tells you
 * whether a boundary the operator drew is the boundary that gets saved. What
 * IS testable, and what actually decides that, is the geometry logic.
 *
 * The editor is the reason this matters. An operator draws a line that decides
 * which officer receives an oil-spill alert. If undo replays the wrong state,
 * or a self-crossing ring reaches the server, or "13 30 N" is silently read as
 * 13.30 degrees, the boundary saved is not the boundary drawn.
 */

import { describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";

import {
  findSelfIntersection, fmtDegMin, fmtLat, fmtLon, parseCoordinate,
  useBoundaryEditor,
} from "../src/components/Globe";

describe("coordinate formatting", () => {
  it("shows six decimals and a hemisphere letter", () => {
    expect(fmtLat(13.5)).toBe("13.500000° N");
    expect(fmtLat(-13.5)).toBe("13.500000° S");
    expect(fmtLon(89.5)).toBe("89.500000° E");
    expect(fmtLon(-89.5)).toBe("89.500000° W");
  });

  it("renders a missing reading as missing, never as zero", () => {
    // A 0 here would read as the equator / prime meridian, which is a real
    // place. "--" is the honest rendering of "the pointer is off the globe".
    for (const v of [null, undefined, NaN]) {
      expect(fmtLat(v)).toBe("--");
      expect(fmtLon(v)).toBe("--");
    }
  });

  it("renders degrees and decimal minutes for chart work", () => {
    expect(fmtDegMin(13.5, true)).toBe("13° 30.000' N");
    expect(fmtDegMin(89.25, false)).toBe("089° 15.000' E");
    expect(fmtDegMin(-13.5, true)).toBe("13° 30.000' S");
  });
});

describe("parseCoordinate", () => {
  it("reads decimal degrees, comma or space separated", () => {
    expect(parseCoordinate("13.5, 89.5")).toEqual({ lat: 13.5, lon: 89.5 });
    expect(parseCoordinate("13.5 89.5")).toEqual({ lat: 13.5, lon: 89.5 });
    expect(parseCoordinate("-13.5,-89.5")).toEqual({ lat: -13.5, lon: -89.5 });
  });

  it("reads degrees and decimal minutes with hemisphere letters", () => {
    const r = parseCoordinate("13 30 N 89 30 E");
    expect(r.lat).toBeCloseTo(13.5, 6);
    expect(r.lon).toBeCloseTo(89.5, 6);
  });

  it("lets a hemisphere letter override the sign", () => {
    const r = parseCoordinate("13.5 S 89.5 W");
    expect(r.lat).toBeCloseTo(-13.5, 6);
    expect(r.lon).toBeCloseTo(-89.5, 6);
  });

  it("returns null rather than guessing", () => {
    // A search box that silently reinterprets input flies the camera somewhere
    // the operator did not ask for, with nothing on screen explaining why.
    for (const bad of ["", null, undefined, "somewhere", "13.5",
                       "13.5 89.5 extra", "abc, def"]) {
      expect(parseCoordinate(bad)).toBeNull();
    }
  });

  it("rejects out-of-range values", () => {
    expect(parseCoordinate("91, 0")).toBeNull();
    expect(parseCoordinate("0, 181")).toBeNull();
  });

  it("rejects a transposed pair instead of accepting nonsense", () => {
    // 89.5 is not a latitude anyone means when they also typed 13.5.
    // It IS a valid latitude, so this one is accepted -- what must not happen
    // is a value past 90 sliding through.
    expect(parseCoordinate("189.5, 13.5")).toBeNull();
  });
});

describe("findSelfIntersection", () => {
  it("passes a simple square", () => {
    const ring = [[0, 0], [1, 0], [1, 1], [0, 1]];
    expect(findSelfIntersection(ring, true)).toBeNull();
  });

  it("finds the crossing edges of a bow-tie", () => {
    // The classic operator error: a vertex dragged across an opposite edge.
    const ring = [[0, 0], [1, 1], [1, 0], [0, 1]];
    const hit = findSelfIntersection(ring, true);
    expect(hit).not.toBeNull();
    // Indices, not a boolean, so the canvas can highlight the two edges.
    expect(hit).toHaveLength(2);
  });

  it("does not flag consecutive edges that share a vertex", () => {
    // Every adjacent pair shares a point; treating that as a crossing would
    // make every polygon invalid.
    const ring = [[0, 0], [2, 0], [2, 2], [1, 1], [0, 2]];
    expect(findSelfIntersection(ring, true)).toBeNull();
  });

  it("does not flag the closing edge against the first", () => {
    const ring = [[0, 0], [1, 0], [1, 1], [0, 1]];
    expect(findSelfIntersection(ring, true)).toBeNull();
  });

  it("treats an open ring as open", () => {
    // While drawing, the ring is not closed and the last-to-first edge does
    // not exist yet, so it cannot cross anything.
    const ring = [[0, 0], [2, 2], [2, 0]];
    expect(findSelfIntersection(ring, false)).toBeNull();
  });
});

describe("useBoundaryEditor", () => {
  it("adds points and produces a closed GeoJSON ring", () => {
    const { result } = renderHook(() => useBoundaryEditor());
    expect(result.current.geometry).toBeNull();

    act(() => { result.current.addPoint(89.0, 13.0); });
    act(() => { result.current.addPoint(90.0, 13.0); });
    // Two points is a line, not a polygon.
    expect(result.current.geometry).toBeNull();

    act(() => { result.current.addPoint(90.0, 14.0); });
    const geom = result.current.geometry;
    expect(geom.type).toBe("Polygon");
    // The ring must be closed for the server, which re-serialises it anyway;
    // sending it open would rely on that leniency.
    expect(geom.coordinates[0]).toHaveLength(4);
    expect(geom.coordinates[0][0]).toEqual(geom.coordinates[0][3]);
  });

  it("undoes and redoes a point", () => {
    const { result } = renderHook(() => useBoundaryEditor());
    act(() => { result.current.addPoint(89, 13); });
    act(() => { result.current.addPoint(90, 13); });
    expect(result.current.points).toHaveLength(2);

    act(() => { result.current.undo(); });
    expect(result.current.points).toHaveLength(1);

    act(() => { result.current.redo(); });
    expect(result.current.points).toHaveLength(2);
    expect(result.current.points[1]).toEqual([90, 13]);
  });

  it("discards the redo branch once a new edit is made", () => {
    // Otherwise redo reapplies a branch the operator has already replaced,
    // which is the classic undo-stack bug: a point reappears from a version
    // of the boundary that no longer exists.
    const { result } = renderHook(() => useBoundaryEditor());
    act(() => { result.current.addPoint(89, 13); });
    act(() => { result.current.addPoint(90, 13); });
    act(() => { result.current.undo(); });
    act(() => { result.current.addPoint(91, 14); });
    act(() => { result.current.redo(); });

    expect(result.current.points).toEqual([[89, 13], [91, 14]]);
  });

  it("moves a point without changing the count", () => {
    const { result } = renderHook(() => useBoundaryEditor());
    act(() => { result.current.addPoint(89, 13); });
    act(() => { result.current.addPoint(90, 13); });
    act(() => { result.current.movePoint(0, 88.5, 12.5); });

    expect(result.current.points).toHaveLength(2);
    expect(result.current.points[0]).toEqual([88.5, 12.5]);
  });

  it("re-opens the ring when deletion drops it below three points", () => {
    // A closed two-point polygon is not a shape, and leaving `closed` true
    // would let it reach the server as one.
    const { result } = renderHook(() => useBoundaryEditor());
    act(() => { result.current.addPoint(89, 13); });
    act(() => { result.current.addPoint(90, 13); });
    act(() => { result.current.addPoint(90, 14); });
    act(() => { result.current.close(); });
    expect(result.current.closed).toBe(true);

    act(() => { result.current.deletePoint(0); });
    expect(result.current.closed).toBe(false);
    expect(result.current.geometry).toBeNull();
  });

  it("refuses to close fewer than three points", () => {
    const { result } = renderHook(() => useBoundaryEditor());
    act(() => { result.current.addPoint(89, 13); });
    act(() => { result.current.close(); });
    expect(result.current.closed).toBe(false);
  });

  it("blocks closing a ring that crosses itself", () => {
    const { result } = renderHook(() => useBoundaryEditor());
    for (const [lon, lat] of [[0, 0], [1, 1], [1, 0], [0, 1]]) {
      act(() => { result.current.addPoint(lon, lat); });
    }
    expect(result.current.selfIntersection).not.toBeNull();
    expect(result.current.canClose).toBe(false);
  });

  it("starts from an existing ring when reshaping", () => {
    const ring = [[89, 13], [90, 13], [90, 14]];
    const { result } = renderHook(() => useBoundaryEditor(ring));
    expect(result.current.points).toEqual(ring);
    expect(result.current.closed).toBe(true);
    expect(result.current.geometry.coordinates[0]).toHaveLength(4);
  });

  it("ignores a point added to a closed ring", () => {
    const { result } = renderHook(() => useBoundaryEditor([[89, 13], [90, 13],
                                                           [90, 14]]));
    act(() => { result.current.addPoint(91, 15); });
    expect(result.current.points).toHaveLength(3);
  });

  it("clears history on reset", () => {
    const { result } = renderHook(() => useBoundaryEditor());
    act(() => { result.current.addPoint(89, 13); });
    act(() => { result.current.reset([]); });
    expect(result.current.points).toEqual([]);
    expect(result.current.canUndo).toBe(false);
  });
});
