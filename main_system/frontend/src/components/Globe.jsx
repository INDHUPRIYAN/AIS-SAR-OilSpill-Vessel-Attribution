/* Import path kept for the pages and the tests: the implementation moved to
 * components/globe/Globe.jsx when the planet itself became the shared
 * GlobeScene. Everything exported before is exported here.
 *
 * Written as `export *` plus an explicit default rather than the shorter
 * `export { default, ... } from`. The short form names every binding in this
 * file, so a dev server holding a cached transform of THIS module from before
 * the underlying file grew an export serves a stale binding list and the app
 * dies on `does not provide an export named ...` -- a white screen that a
 * production build never reproduces, because Rollup resolves the re-export
 * statically. `export *` forwards whatever the source module currently has.
 */
import Globe from "./globe/Globe";

export * from "./globe/Globe";
export default Globe;
