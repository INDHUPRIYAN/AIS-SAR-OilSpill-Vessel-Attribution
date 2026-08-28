/* Analytics — the credibility page.
 *
 * Every number here was measured, and each one says where it came from. Where
 * a figure does not exist it is shown as a stated gap rather than filled in.
 * The caveats are rendered as prominently as the headline numbers, because a
 * metrics page that buries them is doing the opposite of its job.
 */

import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart,
  PolarAngleAxis, PolarGrid, Radar, RadarChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import { AlertTriangle, BarChart3, Crosshair, Info, Radar as RadarIcon, Waves } from "lucide-react";

import { Card, Spinner, Stat, useThemeColors } from "../components/ui";
import { api, fmt, useApi } from "../lib/api";

const mkAxis = (c) => ({ fill: c.ink2, fontSize: 10 });
const mkTooltip = (c) => ({
  contentStyle: {
    background: c.bg1, border: `1px solid ${c.line}`,
    borderRadius: 8, fontSize: 12, color: c.ink0,
  },
});

export default function Analytics() {
  const { data, loading } = useApi(() => api.metrics(), []);
  const tc = useThemeColors();
  const AXIS = mkAxis(tc);
  const TOOLTIP = mkTooltip(tc);

  if (loading) return <div className="page"><Card><Spinner label="loading metrics…" /></Card></div>;

  const seg = data?.segmentation;
  const screen = data?.screening;
  const attr = data?.attribution;

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
        <BarChart3 size={19} color="var(--accent)" />
        <div>
          <div style={{ fontSize: 17, fontWeight: 700 }}>Analytics &amp; Metrics</div>
          <div className="tiny muted">
            Measured results only — every figure traces to a run on disk
          </div>
        </div>
      </div>

      {(data?.notes || []).map((n, i) => (
        <div key={i} className="card" style={{ marginBottom: 14, borderColor: "rgba(245,158,11,.4)" }}>
          <div className="card-body" style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: 12 }}>
            <AlertTriangle size={15} color="var(--warn)" style={{ flexShrink: 0, marginTop: 1 }} />
            <span className="tiny" style={{ color: "var(--warn)" }}>{n}</span>
          </div>
        </div>
      ))}

      {/* ------------------------------------------------- headline stats */}
      <div className="grid grid-4" style={{ marginBottom: 18 }}>
        <Card><Stat label="Attribution top-1" tone="ok"
          value={attr ? fmt.pct(attr.top1_rate, 0) : "—"}
          sub={attr ? `${attr.top1}/${attr.scenarios} scenarios` : "not measured"} /></Card>
        <Card><Stat label="Attribution top-3" tone="ok"
          value={attr ? fmt.pct(attr.top3_rate, 0) : "—"}
          sub={attr ? `${attr.top3}/${attr.scenarios} scenarios` : "not measured"} /></Card>
        <Card><Stat label="Segmentation IoU (oil tiles)"
          value={seg ? fmt.num(seg.oil_tile_iou, 3) : "—"}
          sub={seg ? `${seg.test_tiles} test tiles` : "not measured"} /></Card>
        <Card><Stat label="Look-alike false positives"
          tone={screen && screen.background_fp_rate < 0.1 ? "ok" : "warn"}
          value={screen ? fmt.pct(screen.background_fp_rate, 1) : "—"}
          sub={screen ? `${screen.background_false_positives}/${screen.background_images} patches` : "not measured"} /></Card>
      </div>

      {/* ------------------------------------------------------ attribution */}
      {attr && (
        <div className="grid grid-2" style={{ marginBottom: 18 }}>
          <Card title={<span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
            <Crosshair size={13} /> Attribution by difficulty tier
          </span>}>
            <div style={{ height: 210 }}>
              <ResponsiveContainer>
                <BarChart data={tierData(attr.by_tier)} margin={{ top: 6, right: 8, left: -20, bottom: 4 }}>
                  <CartesianGrid stroke={tc.line} vertical={false} />
                  <XAxis dataKey="tier" tick={AXIS} axisLine={{ stroke: tc.line }} tickLine={false} />
                  <YAxis tick={AXIS} axisLine={false} tickLine={false} domain={[0, 1]}
                    tickFormatter={(v) => `${v * 100}%`} />
                  <Tooltip {...TOOLTIP} formatter={(v, n) => [fmt.pct(v, 1), n]} />
                  <Bar dataKey="top1" name="top-1" radius={[4, 4, 0, 0]}>
                    {tierData(attr.by_tier).map((d, i) => (
                      <Cell key={i} fill={d.top1 > 0.9 ? "#10b981" : d.top1 > 0.7 ? "#f59e0b" : "#f43f5e"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="tiny muted" style={{ marginTop: 8, lineHeight: 1.5 }}>
              Accuracy falls with difficulty, as it should. Hard scenarios are the ones
              where the culprit leaves little behavioural evidence.
            </div>
          </Card>

          <Card title={<span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
            <RadarIcon size={13} /> Accuracy vs available evidence
          </span>}>
            <div style={{ height: 210 }}>
              <ResponsiveContainer>
                <RadarChart data={behaviourData(attr.by_behaviour)} outerRadius="72%">
                  <PolarGrid stroke={tc.line} />
                  <PolarAngleAxis dataKey="label" tick={{ fill: tc.ink2, fontSize: 9 }} />
                  <Radar dataKey="rate" stroke="#38bdf8" fill="#38bdf8" fillOpacity={0.28} />
                  <Tooltip {...TOOLTIP} formatter={(v) => [fmt.pct(v, 1), "top-1"]} />
                </RadarChart>
              </ResponsiveContainer>
            </div>
            <div className="tiny muted" style={{ marginTop: 8, lineHeight: 1.5 }}>
              The scoring depends on evidence, not luck: a vessel that slows and goes
              dark is found reliably; one that does neither often is not.
            </div>
          </Card>
        </div>
      )}

      {/* ------------------------------------------------ threshold sweep */}
      {seg?.sweep?.length > 1 && (
        <Card title="Detection threshold trade-off" style={{ marginBottom: 18 }}>
          <div style={{ height: 230 }}>
            <ResponsiveContainer>
              <LineChart data={seg.sweep} margin={{ top: 6, right: 12, left: -20, bottom: 4 }}>
                <CartesianGrid stroke={tc.line} vertical={false} />
                <XAxis dataKey="threshold" tick={AXIS} axisLine={{ stroke: tc.line }} tickLine={false} />
                <YAxis tick={AXIS} axisLine={false} tickLine={false} domain={[0, 1]} />
                <Tooltip {...TOOLTIP} formatter={(v, n) => [fmt.num(v, 3), n]} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line type="monotone" dataKey="oil_iou" name="IoU on oil tiles"
                  stroke="#f59e0b" strokeWidth={2} dot={{ r: 3 }} />
                <Line type="monotone" dataKey="overall_iou" name="IoU overall"
                  stroke="#38bdf8" strokeWidth={2} dot={{ r: 3 }} />
                <Line type="monotone" dataKey="no_oil_firing_rate" name="no-oil tiles firing"
                  stroke="#f43f5e" strokeWidth={2} strokeDasharray="4 3" dot={{ r: 3 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="tiny muted" style={{ marginTop: 8, lineHeight: 1.55 }}>
            The operating point is configured, not hardcoded. Raising the threshold
            cuts false positives but collapses IoU on real oil — a missed slick ends
            the investigation, whereas a false positive is caught downstream by the
            screening stage and the attribution gates.
          </div>
        </Card>
      )}

      {/* ---------------------------------------------------- model cards */}
      <div className="grid grid-2" style={{ marginBottom: 18 }}>
        {seg && (
          <Card title="Stage 2 · Segmenter">
            <div className="tiny muted" style={{ marginBottom: 10 }}>{seg.model}</div>
            <MetricRow label="IoU · oil tiles" value={fmt.num(seg.oil_tile_iou, 3)} good />
            <MetricRow label="Precision · oil tiles" value={fmt.num(seg.oil_tile_precision, 3)} />
            <MetricRow label="Recall · oil tiles" value={fmt.num(seg.oil_tile_recall, 3)} />
            <MetricRow label="IoU · whole test split" value={fmt.num(seg.overall_iou, 3)} />
            <MetricRow label="No-oil tiles firing"
              value={`${seg.no_oil_firing}/${seg.no_oil_tiles} (${fmt.pct(seg.no_oil_firing_rate, 1)})`} />
            <MetricRow label="dB range" value={`${seg.db_range?.[0]} … ${seg.db_range?.[1]} dB`} />
            <MetricRow label="Test split"
              value={`${seg.scenes ?? "—"} scenes · ${fmt.int(seg.test_tiles)} tiles`} />
            <MetricRow label="Config fingerprint" value={seg.config_fingerprint || "—"} />
            {seg.poc_holdout && (
              <div style={{
                marginTop: 10, padding: "8px 10px", borderRadius: 7,
                background: "rgba(245,158,11,.09)",
                border: "1px solid rgba(245,158,11,.32)",
                display: "flex", gap: 8, alignItems: "flex-start",
              }}>
                <AlertTriangle size={13} color="var(--warn)" style={{ flexShrink: 0, marginTop: 1 }} />
                <span className="tiny" style={{ color: "var(--warn)", lineHeight: 1.5 }}>
                  POC holdout — carved out of Part III itself, so this split is not
                  untouched. Treat these as provisional figures.
                </span>
              </div>
            )}
            <Caveat text={seg.pixel_accuracy_note} />
          </Card>
        )}

        {screen && (
          <Card title="Stage 1 · Screening detector">
            <div className="tiny muted" style={{ marginBottom: 10 }}>{screen.model}</div>
            <MetricRow label="mAP@0.5" value={fmt.num(screen.map50, 3)} good />
            <MetricRow label="mAP@0.5:0.95" value={fmt.num(screen.map50_95, 3)} />
            <MetricRow label="Precision" value={fmt.num(screen.precision, 3)} />
            <MetricRow label="Recall" value={fmt.num(screen.recall, 3)} />
            <MetricRow label="Background FP rate"
              value={`${screen.background_false_positives}/${screen.background_images} (${fmt.pct(screen.background_fp_rate, 1)})`}
              good={screen.background_fp_rate < 0.1} />
            <Caveat text={screen.note} />
          </Card>
        )}
      </div>

      {/* --------------------------------------------------------- caveats */}
      <Card title={<span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
        <Info size={13} /> What these numbers do and do not say
      </span>}>
        {attr?.note && <Caveat text={attr.note} />}
        {data?.drift?.note && (
          <Caveat icon={<Waves size={12} />} text={data.drift.note} />
        )}
        <Caveat text="Metrics are regenerated by ml.evaluate and the 50-scenario
          benchmark; nothing on this page is entered by hand." />
      </Card>
    </div>
  );
}

/* --------------------------------------------------------------- helpers */

function MetricRow({ label, value, good }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 10,
      padding: "6px 0", borderBottom: "1px solid var(--line)",
    }}>
      <span className="tiny muted" style={{ flex: 1 }}>{label}</span>
      <span className="mono" style={{
        fontSize: 13, fontWeight: 600,
        color: good ? "var(--ok)" : "var(--ink-0)",
      }}>{value}</span>
    </div>
  );
}

function Caveat({ text, icon }) {
  return (
    <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "flex-start" }}>
      <span style={{ color: "var(--ink-3)", flexShrink: 0, marginTop: 2 }}>
        {icon || <Info size={12} />}
      </span>
      <span className="tiny muted" style={{ lineHeight: 1.55 }}>{text}</span>
    </div>
  );
}

const tierData = (byTier) =>
  Object.entries(byTier || {}).map(([tier, v]) => ({
    tier, top1: v.top1_rate, scenarios: v.scenarios,
  }));

const behaviourData = (byBehaviour) =>
  Object.entries(byBehaviour || {}).map(([k, v]) => ({
    label: k.replace(/_/g, " ").replace("with ", "+").replace("without ", "−"),
    rate: v.top1_rate,
  }));
