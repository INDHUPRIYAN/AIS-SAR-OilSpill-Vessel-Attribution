/* Which analytical context the right panel shows.
 *
 * A stage can hold several questions (the drift stage answers "what is the
 * current?", "where did it come from?" and "where is it going?"). CONTEXTS
 * lists them per stage; the first is the stage's default. The analyst picks
 * one from the strip; the presentation picks one per beat (lib/cinematic's
 * `sub`). Either way the panel is the same component reading the same run.
 */

import CaseBrief from "./CaseBrief";
import IntelPanel from "./IntelPanels";
import {
  AisFilterPanel, AisTrafficPanel, ForcingPanel, ForecastPanel, HindcastPanel, RankingPanel, SegmentationPanel,
} from "./AnalysisPanels";

const PER_STAGE = {
  detection: [["detection", "Detection"], ["segmentation", "Segmentation"]],
  validation: [["validation", "Validation"], ["wind", "Wind"]],
  drift: [["overview", "Overview"], ["currents", "Current"], ["hindcast", "Hindcast"], ["forecast", "Forecast"]],
  ais: [["correlation", "Correlation"], ["traffic", "Traffic"], ["filter", "Filtering"], ["ranking", "Ranking"]],
};

/* "Brief" is offered on every stage: the case summary is the one thing an
 * analyst wants from any point in the workflow, and it was written, tested
 * and then mounted nowhere. */
const BRIEF = ["brief", "Brief"];

export const CONTEXTS = new Proxy(PER_STAGE, {
  get: (target, stage) => (typeof stage === "string"
    ? [...(target[stage] || []), BRIEF] : target[stage]),
});

export const defaultContext = (stage) => PER_STAGE[stage]?.[0]?.[0] ?? null;

export default function RightPanel({ ctx }) {
  const options = CONTEXTS[ctx.stage];
  const sub = options?.some(([id]) => id === ctx.sub) ? ctx.sub : defaultContext(ctx.stage);
  let body;
  switch (sub) {
    case "brief": body = (
      <div className="rp-brief">
        <CaseBrief sceneMeta={ctx.layers?.scene_meta} slick={ctx.layers?.slick} detect={ctx.layers?.detect}
          origin={ctx.layers?.origin_cloud} forecast={ctx.layers?.forecast} suspects={ctx.layers?.suspects}
          runRow={ctx.runRow} errors={ctx.errors} loaded={ctx.runState !== "running"}
          onShowTab={(stage, s) => ctx.actions.sub(stage, s)} />
      </div>
    ); break;
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
