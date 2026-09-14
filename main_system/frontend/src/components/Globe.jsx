/* Import path kept for the pages and the tests: the implementation moved to
 * components/globe/Globe.jsx when the planet itself became the shared
 * GlobeScene. Everything exported before is exported here. */
export {
  default, GLOBE_INITIAL_VIEW, GLOBE_COLORS, fmtLat, fmtLon, fmtDegMin, parseCoordinate,
  findSelfIntersection, CoordinateReadout, useBoundaryEditor,
} from "./globe/Globe";
