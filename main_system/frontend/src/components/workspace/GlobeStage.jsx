/* The 3D surface of the workspace: the shared GlobeScene planet with zones,
 * incidents, live AIS and the footprint of the scene being chosen.
 *
 * Used for orientation during scene acquisition and as the "3D" side of the
 * surface switch. When the page loads a scene it flies here first, then
 * crossfades to the 2D map at the same place -- the transition the spec asks
 * for, on the engine the product already has. Every layer comes from
 * useGlobeData (API responses); the footprint is the selected product's own
 * bbox. */

import { useEffect, useMemo, useState } from "react";

import Globe, { GLOBE_INITIAL_VIEW } from "../Globe";

/* Layer families are added over successive frames rather than all at once.
 * Each family compiles its own shader programs the first time it is drawn;
 * on a software GPU that is seconds of main-thread time, and one block of
 * that length would freeze the workspace's own updates (stage status, the
 * chips) behind the planet. Spread across frames, the page keeps rendering
 * between them. On a real GPU the difference is invisible. */
const FAMILIES = ["scene", "zones", "vessels", "incidents", "zoneLabels"];
import { useGlobeCamera } from "../globe/GlobeScene";
import { useGlobeData } from "../../lib/useGlobeData";
import { useTheme } from "../../lib/theme";

/** `hidden`: mounted to keep the WebGL context and its shaders warm, but
 *  not on screen -- no polling, no parallax. `resetNonce`: snap the camera
 *  back to orbit (the presentation starts every replay from the planet). */
export default function GlobeStage({ focus, footprint, onCursor, onFlown, onPick, layersOn, hidden = false, resetNonce = null }) {
  const { theme } = useTheme();
  const cam = useGlobeCamera(GLOBE_INITIAL_VIEW, { parallax: !hidden });
  const data = useGlobeData({ withRun: false, paused: hidden });
  const [families, setFamilies] = useState(1);
  useEffect(() => {
    if (families >= FAMILIES.length) return undefined;
    const id = setTimeout(() => setFamilies((n) => n + 1), 120);
    return () => clearTimeout(id);
  }, [families]);
  const staged = useMemo(() => Object.fromEntries(FAMILIES.map((f, i) => [f, i < families])), [families]);

  useEffect(() => {
    if (resetNonce == null) return;
    cam.setBase({ ...GLOBE_INITIAL_VIEW, transitionDuration: 0, transitionInterpolator: undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetNonce]);

  /* Fly whenever the focus changes; report when the flight has landed so the
   * page can crossfade into the 2D map at that spot. `nonce` lets the same
   * place be flown to again (a replayed presentation starts from orbit). */
  useEffect(() => {
    if (!focus) return undefined;
    const ms = focus.ms ?? 1400;
    cam.flyTo({ longitude: focus.lon, latitude: focus.lat, zoom: focus.zoom ?? 5.2 }, ms);
    const id = setTimeout(() => onFlown?.(focus), ms + 80);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.lon, focus?.lat, focus?.zoom, focus?.nonce]);

  const runLayers = useMemo(
    () => (footprint ? { sceneMeta: { bbox: footprint } } : null), [footprint]);

  return (
    <div className="globe-root ws-globe" data-testid="workspace-globe">
      <Globe
        viewState={cam.viewState}
        onViewStateChange={cam.onViewStateChange}
        onPointerMove={cam.onPointerMove}
        onPointerLeave={cam.onPointerLeave}
        basemap="canvas"
        theme={theme}
        animate={!hidden}
        layersOn={{ graticule: true, ...staged, tracks: false, slick: false, origin: false, forecast: false, ...layersOn }}
        zones={data.zones}
        incidents={data.incidents}
        vessels={data.vessels}
        runLayers={runLayers}
        onMapClick={(ll) => onPick?.(ll)}
        onCursorMove={onCursor}
        onSelect={() => {}}
      />
    </div>
  );
}
