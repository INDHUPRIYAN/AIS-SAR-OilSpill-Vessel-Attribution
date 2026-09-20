/* Which analytical context the right panel shows.
 *
 * A stage can hold several questions (the drift stage answers "what is the
 * current?", "where did it come from?" and "where is it going?"). CONTEXTS
 * lists them per stage; the first is the stage's default. The analyst picks
 * one from the strip; the presentation picks one per beat (lib/cinematic's
 * `sub`). Either way the panel is the same component reading the same run.
 */

import IntelPanel from "./IntelPanels";
import {
  AisFilterPanel, AisTrafficPanel, ForcingPanel, ForecastPanel, HindcastPanel, RankingPanel, SegmentationPanel,
} from "./AnalysisPanels";

export const CONTEXTS = {
  detection: [["detection", "Detection"], ["segmentation", "Segmentation"]],
  validation: [["validation", "Validation"], ["wind", "Wind"]],
  drift: [["overview", "Overview"], ["currents", "Current"], ["hindcast", "Hindcast"], ["forecast", "Forecast"]],
  ais: [["correlation", "Correlation"], ["traffic", "Traffic"], ["filter", "Filtering"], ["ranking", "Ranking"]],
};

export const defaultContext = (stage) => CONTEXTS[stage]?.[0]?.[0] ?? null;

export default function RightPanel({ ctx }) {
  const options = CONTEXTS[ctx.stage];
  const sub = options?.some(([id]) => id === ctx.sub) ? ctx.sub : defaultContext(ctx.stage);
  let body;
  switch (sub) {
    case "segmentation": body = <SegmentationPanel ctx={ctx} />; break;
    case "wind": body = <ForcingPanel ctx={ctx} kind="wind" />; break;
    case "currents": body = <ForcingPanel ctx={ctx} kind="currents" />; break;
    case "hindcast": body = <HindcastPanel ctx={ctx} />; break;
    case "forecast": body = <ForecastPanel ctx={ctx} />; break;
    case "traffic": body = <AisTrafficPanel ctx={ctx} />; break;
    case "filter": body = <AisFilterPanel ctx={ctx} />; break;
    case "ranking": body = <RankingPanel ctx={ctx} />; break;
    default: body = <IntelPanel ctx={ctx} />;
  }
  return (
    <div className="rp" data-testid="right-panel" data-context={sub || ctx.panel}>
      {options && (
        <div className="rp-strip" role="tablist" data-testid="context-strip">
          {options.map(([id, label]) => (
            <button key={id} role="tab" aria-selected={sub === id} className={`rp-tab ${sub === id ? "on" : ""}`}
              onClick={() => ctx.actions.sub(ctx.stage, id)} data-testid={`context-${id}`}>{label}</button>
          ))}
        </div>
      )}
      <div className="rp-body" key={sub || ctx.panel}>{body}</div>
    </div>
  );
}
