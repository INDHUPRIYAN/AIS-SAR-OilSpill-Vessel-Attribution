/* About / Pipeline — architecture, provenance, and the honest limitations.
 *
 * The limitations section is not an afterthought. A judge who asks "what would
 * you do next" should find that we already know, in writing.
 */

import { BookOpen, Cpu, Database, GitBranch, ShieldAlert } from "lucide-react";
import { Card } from "../components/ui";

const STAGES = [
  ["Scene", "Sentinel-1 GRD from CDSE, ASF fallback, local cache", "Pavitra"],
  ["Detect", "YOLO screen → U-Net segment → threshold fallback", "Indhu"],
  ["Characterise", "area, perimeter, ellipse, orientation, damping, age", "Nandha"],
  ["Met-ocean", "CMEMS currents · ERA5 / Open-Meteo wind", "Keerthana"],
  ["Hindcast", "OpenDrift backward run → origin cloud + time window", "Nandha"],
  ["Forecast", "forward run → +6 / +12 / +24 h spread", "Nandha"],
  ["AIS", "DMA / MarineCadastre archives, synthetic generator", "Krishnan"],
  ["Attribute", "spatial / temporal / trajectory gates → weighted scoring", "Nandha"],
];

const LIMITS = [
  [
    "Segmentation metrics are POC-grade",
    "Trained on a scene-level holdout of Trujillo Part III rather than on Parts I–II, so the test split is not untouched. Parts I–II are 80 GB and still downloading; the figure must be re-measured before it is quoted as a test result.",
  ],
  [
    "No real-world attribution ground truth",
    "No public dataset links a SAR slick to a confirmed culprit vessel. Accuracy is measured on a 50-scenario synthetic benchmark with a planted culprit, which is precisely why that harness exists.",
  ],
  [
    "Drift accuracy is not claimed",
    "Output is a weighted particle cloud with an uncertainty ellipse. Without ground-truth drift tracks, any single accuracy number would be invented.",
  ],
  [
    "Real AIS does not exist for Indian waters",
    "The synthetic generator is mandatory infrastructure, not a shortcut. Synthetic layers are labelled as such throughout the interface.",
  ],
  [
    "Look-alikes are background negatives, not a class",
    "DARTIS annotates oil objects but not look-alikes, so the screening model learns to stay silent on them rather than to name them. The meaningful metric is how often it fires on one.",
  ],
];

export default function About() {
  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
        <BookOpen size={19} color="var(--accent)" />
        <div>
          <div style={{ fontSize: 17, fontWeight: 700 }}>About OceanTrace</div>
          <div className="tiny muted">
            SIH 2026 · Problem Statement 26143 · NTRO · Space Technology
          </div>
        </div>
      </div>

      <Card title="What the system does" style={{ marginBottom: 16 }}>
        <p className="tiny" style={{ margin: 0, lineHeight: 1.65, color: "var(--ink-1)" }}>
          OceanTrace takes a Sentinel-1 SAR scene, detects and characterises any oil slick
          in it, uses ocean-current and wind fields to trace the slick backwards to a
          probable origin point and time, projects its future spread, reconstructs the AIS
          traffic present around that origin, filters vessels that could not have been
          responsible, and ranks the remainder with an explainable score. Every stage has a
          fallback, and the last fallback in each chain needs no network at all.
        </p>
      </Card>

      <Card
        title={<span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
          <GitBranch size={13} /> Pipeline
        </span>}
        style={{ marginBottom: 16 }}
        bodyStyle={{ padding: 0 }}
      >
        <table>
          <thead>
            <tr>
              <th style={{ width: 30 }}>#</th><th>Stage</th>
              <th>Implementation</th><th>Owner</th>
            </tr>
          </thead>
          <tbody>
            {STAGES.map(([name, impl, owner], i) => (
              <tr key={name}>
                <td className="mono tiny muted">{i + 1}</td>
                <td style={{ fontWeight: 600 }}>{name}</td>
                <td className="tiny muted">{impl}</td>
                <td className="tiny">{owner}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <div className="grid grid-2" style={{ marginBottom: 16 }}>
        <Card title={<span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
          <Database size={13} /> Datasets
        </span>}>
          <Entry
            name="DARTIS"
            who="Yang & Singha, DLR — Eastern Mediterranean, 2019"
            doi="10.1594/PANGAEA.980773"
            licence="CC-BY-4.0"
            use="Screening model: 3,225 annotated oil objects across 1,365 patches, plus 2,290 look-alike patches used as background negatives."
          />
          <Entry
            name="Trujillo"
            who="Zenodo, three parts"
            doi="10.5281/zenodo.13761290"
            licence="open"
            use="Segmentation: 2048×2048 Sigma0 dB scenes with binary masks. Part III (150 oil / 150 look-alike / 150 no-oil) is the test harness."
          />
        </Card>

        <Card title={<span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
          <Cpu size={13} /> Models
        </span>}>
          <Entry
            name="Stage 1 · screen"
            who="YOLO11n, single class"
            use="Answers whether a dark patch is oil at all. Trained with look-alike patches as background, so it learns to stay quiet on calm water and internal waves."
          />
          <Entry
            name="Stage 2 · segment"
            who="U-Net, ResNet-34 encoder, ImageNet init"
            use="Delineates the slick pixel by pixel. Dice+BCE, AMP fp16, split by scene rather than by tile so near-duplicate tiles cannot leak across the boundary."
          />
          <Entry
            name="Guaranteed path"
            who="Adaptive threshold + morphology"
            use="No model, no GPU, no dataset. Serves /detect whenever the ML path is unavailable, reporting engine=threshold_fallback."
          />
        </Card>
      </div>

      <Card title={<span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
        <ShieldAlert size={13} color="var(--warn)" /> Honest limitations
      </span>}>
        {LIMITS.map(([title, body]) => (
          <div key={title} style={{ marginBottom: 13 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--warn)" }}>{title}</div>
            <div className="tiny muted" style={{ marginTop: 3, lineHeight: 1.6 }}>{body}</div>
          </div>
        ))}
      </Card>
    </div>
  );
}

function Entry({ name, who, doi, licence, use }) {
  return (
    <div style={{
      marginBottom: 13, paddingBottom: 13,
      borderBottom: "1px solid var(--line)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontWeight: 600 }}>{name}</span>
        {licence && <span className="badge badge-neutral">{licence}</span>}
      </div>
      <div className="tiny muted" style={{ marginTop: 2 }}>{who}</div>
      {doi && (
        <div className="tiny mono" style={{ color: "var(--accent)", marginTop: 2 }}>
          doi:{doi}
        </div>
      )}
      <div className="tiny muted" style={{ marginTop: 5, lineHeight: 1.55 }}>{use}</div>
    </div>
  );
}
