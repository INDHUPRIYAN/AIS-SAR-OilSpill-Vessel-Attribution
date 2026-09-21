/* Upload a SAR raster, supply whatever metadata the file does not carry, and
 * hand the registered scene on. One component, mounted on the Dashboard (the
 * front door) and in the SAR Image Database. It assumes nothing: a missing
 * acquisition time, footprint or polarisation is asked for, never guessed. */
import { useState } from "react";
import { CheckCircle2, Loader2, ScanSearch, Upload } from "lucide-react";

import { Notice } from "../ui";
import { api } from "../../lib/api";
import "../../sardb.css";

function Fact({ k, v, title, children }) {
  return <div className="sd-fact" title={title}><span>{k}</span><b className="mono">{children ?? (v == null || v === "" ? "—" : v)}</b></div>;
}

export default function UploadPanel({ canUpload, onReady, onAnalyse, analysing = false }) {
  const [file, setFile] = useState(null);
  const [f, setF] = useState({ scene_id: "", acquired_utc: "", bbox: "", polarisation: "", product_type: "", orbit_direction: "", notes: "" });
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));

  async function send() {
    if (!file) return;
    setBusy(true); setError(null);
    try {
      const form = new FormData();
      form.append("raster", file);
      for (const [k, v] of Object.entries(f)) if (v) form.append(k, v);
      const s = await api.sarUpload(form);
      setState(s); if (s.status === "ready") onReady?.(s);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function complete() {
    setBusy(true); setError(null);
    try {
      const body = Object.fromEntries(Object.entries(f).filter(([k, v]) => v && ["acquired_utc", "bbox", "polarisation", "product_type", "orbit_direction", "notes"].includes(k)));
      const s = await api.sarCompleteUpload(state.upload_id, body);
      setState(s); if (s.status === "ready") onReady?.(s);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  const missing = state?.missing || [];
  const need = (k) => missing.includes(k);
  return (
    <div className="sd-upload" data-testid="sar-upload">
      <div className="sd-h">Upload SAR data</div>
      <p className="sd-p">A calibrated sigma0 GeoTIFF (dB). A georeferenced raster supplies its own footprint; anything the file does not carry must be entered. Nothing is assumed.</p>
      {!canUpload && <Notice tone="info">Uploading needs the investigator or analyst role.</Notice>}
      <label className="sd-file">
        <input type="file" accept=".tif,.tiff,.png,.jpg,.jpeg" disabled={!canUpload} data-testid="sar-upload-file"
          onChange={(e) => { setFile(e.target.files?.[0] || null); setState(null); setError(null); }} />
        <Upload size={14} /> {file ? file.name : "Choose a raster…"}
      </label>

      {state?.status === "unsupported" && (
        <Notice tone="warn" testid="sar-upload-unsupported"><b>NOT ANALYSABLE.</b> {state.caveats?.[0]}</Notice>
      )}
      {state?.status === "metadata_required" && (
        <Notice tone="warn" testid="sar-metadata-required"><b>METADATA REQUIRED:</b> {missing.join(", ")}. The file does not carry {missing.length === 1 ? "it" : "them"} and OceanTrace will not guess.</Notice>
      )}
      {state?.raster_facts?.width && (
        <div className="sd-facts">
          <Fact k="Raster" v={`${state.raster_facts.width} × ${state.raster_facts.height} px · ${state.raster_facts.bands} band · ${state.raster_facts.dtype}`} />
          <Fact k="Georeferenced" v={state.raster_facts.georeferenced_raster ? `yes (${state.raster_facts.raster_crs})` : "no"} />
          <Fact k="Values p1–p99" v={state.raster_facts.value_p01_p99?.join(" … ")} />
        </div>
      )}
      {state?.caveats?.filter((_, i) => state.status !== "unsupported" || i > 0).map((c) => <Notice key={c} tone="warn">{c}</Notice>)}

      <div className="sd-form">
        <label>Scene ID<input value={f.scene_id} onChange={set("scene_id")} placeholder="defaults to the file name" disabled={Boolean(state)} /></label>
        <label className={need("acquired_utc") ? "need" : ""}>Acquisition time (UTC){need("acquired_utc") && <em>required</em>}
          <input value={f.acquired_utc} onChange={set("acquired_utc")} placeholder="2023-01-08T00:10:08Z" data-testid="sar-up-time" /></label>
        <label className={need("bbox") ? "need" : ""}>Footprint bbox{need("bbox") && <em>required</em>}
          <input value={f.bbox} onChange={set("bbox")} placeholder="min_lon, min_lat, max_lon, max_lat" data-testid="sar-up-bbox"
            disabled={state?.metadata_basis?.bbox === "raster"} title={state?.metadata_basis?.bbox === "raster" ? "Read from the raster; not overridable" : ""} /></label>
        <label className={need("polarisation") ? "need" : ""}>Polarisation{need("polarisation") && <em>required</em>}
          <select value={f.polarisation} onChange={set("polarisation")} data-testid="sar-up-pol"><option value="">—</option>{["VV", "VH", "HH", "HV"].map((p) => <option key={p}>{p}</option>)}</select></label>
        <label>Product type<input value={f.product_type} onChange={set("product_type")} placeholder="GRD" /></label>
        <label>Orbit direction<select value={f.orbit_direction} onChange={set("orbit_direction")}><option value="">—</option><option>ASCENDING</option><option>DESCENDING</option></select></label>
      </div>
      {error && <Notice tone="danger" testid="sar-upload-error">{error}</Notice>}
      {state?.status === "ready" ? (
        <><Notice tone="ok" testid="sar-upload-ready"><CheckCircle2 size={13} /> Scene registered. Footprint {state.metadata_basis?.bbox === "raster" ? "read from the raster" : "as entered"}; time {state.metadata_basis?.acquired_utc === "product_identifier" ? "read from the product identifier" : "as entered"}.</Notice>
          {onAnalyse && (
            <button className="sd-btn primary" onClick={() => onAnalyse(state)} disabled={analysing} data-testid="sar-upload-analyse">
              {analysing ? <Loader2 size={14} className="ws-spin" /> : <ScanSearch size={14} />} Analyse this scene
            </button>
          )}
        </>
      ) : (
        <button className="sd-btn primary" disabled={!canUpload || !file || busy || state?.status === "unsupported"} onClick={state?.status === "metadata_required" ? complete : send} data-testid="sar-upload-send">
          {busy ? <Loader2 size={14} className="ws-spin" /> : <Upload size={14} />} {state?.status === "metadata_required" ? "Validate metadata" : "Upload and validate"}
        </button>
      )}
    </div>
  );
}
