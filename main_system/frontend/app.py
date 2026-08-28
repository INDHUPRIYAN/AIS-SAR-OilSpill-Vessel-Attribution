"""OceanTrace — GIS investigation interface (Streamlit + Folium).

Reads a completed pipeline run from `data/runs/<run_id>/` and renders every
contract file as a toggleable map layer, plus the ranked suspect panel.

Provenance is the point. Every layer carries a badge derived from the run
manifest -- REAL, FALLBACK, MOCK or SYNTHETIC -- so nobody watching a demo can
mistake a mock layer for a live one. A pretty map that quietly shows mock data
as if it were real is the failure mode this whole design exists to prevent.

Run:
    cd main_system
    ../.venv/Scripts/python -m streamlit run frontend/app.py
"""
from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[2]
for p in (REPO_ROOT, REPO_ROOT / "main_system"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

RUNS = REPO_ROOT / "data" / "runs"
MOCKS = REPO_ROOT / "contracts" / "mocks"

st.set_page_config(page_title="OceanTrace — Investigation", layout="wide",
                   initial_sidebar_state="expanded")

BADGE = {
    "ok":       ("REAL",     "#1a7f37", "Produced and contract-validated by the real component"),
    "fallback": ("FALLBACK", "#bf8700", "Degraded path produced this — see the note"),
    "mock":     ("MOCK",     "#8250df", "Component not wired in yet; mock file served"),
    "failed":   ("FAILED",   "#cf222e", "Stage failed; layer unavailable"),
}

LAYER_COLOR = {
    "slick": "#ff3b30",
    "origin": "#ffb020",
    "forecast": "#3b82f6",
    "vessel": "#8e8e93",
    "suspect": "#ff3b30",
}


# --------------------------------------------------------------------------
# data loading
# --------------------------------------------------------------------------


def list_runs():
    if not RUNS.exists():
        return []
    return sorted([d for d in RUNS.iterdir() if (d / "manifest.json").exists()],
                  key=lambda d: d.stat().st_mtime, reverse=True)


@st.cache_data(show_spinner=False)
def load_json(path: str, mtime: float):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_run(run_dir: Path) -> dict:
    out = {"dir": run_dir}
    for key, name in [("manifest", "manifest.json"), ("scene_meta", "scene_meta.json"),
                      ("detect", "detect_response.json"), ("slick", "slick.geojson"),
                      ("origin", "origin_cloud.geojson"), ("forecast", "forecast.geojson"),
                      ("suspects", "suspects.json")]:
        f = run_dir / name
        out[key] = load_json(str(f), f.stat().st_mtime) if f.exists() else None
    vp = run_dir / "vessels.parquet"
    out["vessels_path"] = vp if vp.exists() else None
    return out


@st.cache_data(show_spinner=False)
def load_vessels(path: str, mtime: float):
    import pandas as pd
    return pd.read_parquet(path)


@st.cache_data(show_spinner=False)
def scene_overlay(scene_path: str, mtime: float, db_min: float, db_max: float):
    """Render the Sigma0 dB raster as a base64 PNG for a Folium ImageOverlay."""
    import rasterio
    from PIL import Image
    from rasterio.warp import transform_bounds

    with rasterio.open(scene_path) as src:
        db = src.read(1).astype(np.float32)
        bounds = src.bounds
        crs = src.crs
    if crs is not None and rasterio.crs.CRS.from_user_input(crs).to_epsg() != 4326:
        bounds = transform_bounds(crs, "EPSG:4326", *bounds)
    else:
        bounds = (bounds.left, bounds.bottom, bounds.right, bounds.top)

    norm = np.clip((db - db_min) / max(db_max - db_min, 1e-6), 0, 1)
    img = Image.fromarray((norm * 255).astype(np.uint8), mode="L").convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    # folium wants [[south, west], [north, east]]
    return f"data:image/png;base64,{b64}", [[bounds[1], bounds[0]], [bounds[3], bounds[2]]]


def stage_status(manifest, name) -> dict:
    for s in (manifest or {}).get("stages", []):
        if s["stage"] == name:
            return s
    return {}


def badge_html(status: str, label: str = "") -> str:
    text, colour, _ = BADGE.get(status, ("?", "#6e7781", ""))
    return (f"<span style='background:{colour};color:#fff;padding:2px 7px;"
            f"border-radius:10px;font-size:0.70rem;font-weight:600;"
            f"letter-spacing:.03em'>{label or text}</span>")


# --------------------------------------------------------------------------
# map
# --------------------------------------------------------------------------


def build_map(run: dict, show: dict):
    import folium
    from folium.plugins import HeatMap

    slick, origin = run.get("slick"), run.get("origin")
    meta = run.get("scene_meta") or {}
    bbox = meta.get("bbox")
    centre = [(bbox[1] + bbox[3]) / 2, (bbox[0] + bbox[2]) / 2] if bbox else [13.05, 80.3]

    m = folium.Map(location=centre, zoom_start=11, tiles=None, control_scale=True)
    folium.TileLayer(
        tiles="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr="© OpenStreetMap contributors",
        name="Basemap (light)"
    ).add_to(m)
    folium.TileLayer(
        tiles="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr="© OpenStreetMap contributors",
        name="Basemap (dark)",
        show=False,
        className="dark-tiles"
    ).add_to(m)

    # Invert the dark tiles layer to dark mode using Leaflet CSS filter
    dark_mode_style = """
    <style>
        .dark-tiles img {
            filter: invert(100%) hue-rotate(180deg) brightness(95%) contrast(90%) !important;
        }
    </style>
    """
    m.get_root().html.add_child(folium.Element(dark_mode_style))

    # --- SAR scene --------------------------------------------------------
    scene_path = meta.get("file_path")
    if show["scene"] and scene_path:
        p = (REPO_ROOT / scene_path) if not Path(scene_path).is_absolute() else Path(scene_path)
        if p.exists():
            try:
                db_range = meta.get("db_range", [-35.0, 0.0])
                url, img_bounds = scene_overlay(str(p), p.stat().st_mtime,
                                                float(db_range[0]), float(db_range[1]))
                folium.raster_layers.ImageOverlay(
                    image=url, bounds=img_bounds, opacity=0.85,
                    name="SAR scene (Sigma0 dB)", z_index=1).add_to(m)
            except Exception as exc:
                st.sidebar.warning(f"SAR overlay unavailable: {exc}")

    # --- origin probability cloud ----------------------------------------
    if show["origin"] and origin:
        pts, ellipses = [], []
        for f in origin["features"]:
            props = f.get("properties", {})
            if props.get("feature_type") == "particle":
                lon, lat = f["geometry"]["coordinates"][:2]
                pts.append([lat, lon, float(props.get("weight", 0.5))])
            elif props.get("feature_type") == "ellipse":
                ellipses.append(f)
        if pts:
            fg = folium.FeatureGroup(name=f"Origin cloud ({len(pts)} particles)")
            HeatMap(pts, radius=13, blur=18, min_opacity=0.25).add_to(fg)
            fg.add_to(m)
        if ellipses:
            fg = folium.FeatureGroup(name=f"Confidence ellipses ({len(ellipses)})")
            for f in ellipses:
                lvl = f["properties"].get("confidence_level", 0.9)
                folium.GeoJson(
                    f, style_function=lambda _x, l=lvl: {
                        "color": LAYER_COLOR["origin"], "weight": 1.2,
                        "fillOpacity": 0.04 + 0.06 * (1 - l), "dashArray": "4,4"},
                    tooltip=f"{int(lvl*100)}% at step {f['properties'].get('step_index')}"
                ).add_to(fg)
            fg.add_to(m)

    # --- forecast ---------------------------------------------------------
    if show["forecast"] and run.get("forecast"):
        fg = folium.FeatureGroup(name="Forecast spread")
        for f in run["forecast"]["features"]:
            h = f["properties"].get("horizon_h")
            lvl = f["properties"].get("confidence_level", 0.5)
            folium.GeoJson(
                f, style_function=lambda _x, hh=h: {
                    "color": LAYER_COLOR["forecast"], "weight": 1.5,
                    "fillOpacity": 0.05, "dashArray": "6,3"},
                tooltip=f"+{h} h · {int(lvl*100)}% · {f['properties'].get('area_km2')} km²"
            ).add_to(fg)
        fg.add_to(m)

    # --- vessels ----------------------------------------------------------
    suspect_mmsi = {}
    if run.get("suspects"):
        suspect_mmsi = {s["mmsi"]: s for s in run["suspects"].get("suspects", [])}

    if show["vessels"] and run.get("vessels_path") is not None:
        df = load_vessels(str(run["vessels_path"]), run["vessels_path"].stat().st_mtime)
        fg = folium.FeatureGroup(name=f"AIS tracks ({df.mmsi.nunique()} vessels)")
        for mmsi, grp in df.sort_values("timestamp_utc").groupby("mmsi"):
            sus = suspect_mmsi.get(int(mmsi))
            is_top = sus and sus.get("rank") == 1
            coords = list(zip(grp.lat, grp.lon))
            folium.PolyLine(
                coords,
                color=LAYER_COLOR["suspect"] if sus else LAYER_COLOR["vessel"],
                weight=3.5 if is_top else (2.2 if sus else 1.0),
                opacity=0.95 if sus else 0.35,
                tooltip=(f"MMSI {mmsi}" + (f" · rank {sus['rank']} · "
                         f"score {sus['total_score']:.2f}" if sus else " · not a suspect"))
            ).add_to(fg)
            if is_top:
                folium.CircleMarker(
                    coords[len(coords) // 2], radius=7, color=LAYER_COLOR["suspect"],
                    fill=True, fillOpacity=0.9,
                    tooltip=f"PRIME SUSPECT · MMSI {mmsi}").add_to(fg)
        fg.add_to(m)

    # --- slick (drawn last so it sits on top) -----------------------------
    if show["slick"] and slick:
        fg = folium.FeatureGroup(name="Detected slick")
        for f in slick["features"]:
            p = f["properties"]
            tip = (f"{p['slick_id']}<br>{p['area_km2']} km² · "
                   f"{p.get('major_axis_m', 0)/1000:.1f} km long<br>"
                   f"bearing {p.get('orientation_deg')}° · "
                   f"damping {p.get('damping_ratio')} dB")
            folium.GeoJson(f, style_function=lambda _x: {
                "color": LAYER_COLOR["slick"], "weight": 2.5,
                "fillColor": LAYER_COLOR["slick"], "fillOpacity": 0.35},
                tooltip=tip).add_to(fg)
            folium.CircleMarker(
                [p["centroid"][1], p["centroid"][0]], radius=4,
                color=LAYER_COLOR["slick"], fill=True, tooltip="slick centroid").add_to(fg)
        fg.add_to(m)

    if bbox:
        folium.Rectangle([[bbox[1], bbox[0]], [bbox[3], bbox[2]]],
                         color="#6e7781", weight=1, fill=False, dashArray="3,5",
                         tooltip="scene extent").add_to(m)
        m.fit_bounds([[bbox[1], bbox[0]], [bbox[3], bbox[2]]])

    folium.LayerControl(collapsed=False).add_to(m)
    return m


# --------------------------------------------------------------------------
# app
# --------------------------------------------------------------------------

st.markdown("""<style>
.block-container{padding-top:2.2rem;padding-bottom:1rem;}
.factor-row{display:flex;align-items:center;gap:8px;margin:2px 0;font-size:0.78rem;}
.factor-name{width:88px;color:#57606a;}
.factor-bar{flex:1;background:#eaeef2;border-radius:3px;height:9px;overflow:hidden;}
.factor-fill{background:#ff3b30;height:100%;}
.factor-val{width:34px;text-align:right;color:#57606a;font-variant-numeric:tabular-nums;}
</style>""", unsafe_allow_html=True)

st.title("OceanTrace")
st.caption("SAR oil-spill detection → drift hindcast → explainable vessel attribution · "
           "SIH 2026 · PS26143")

runs = list_runs()

with st.sidebar:
    st.header("Investigation")

    if not runs:
        st.warning("No pipeline runs found.")
    run_names = [d.name for d in runs]
    selected = st.selectbox("Run", run_names, index=0) if runs else None

    st.divider()
    st.subheader("Run the pipeline")
    scene_choice = st.text_input("Scene GeoTIFF",
                                 value=str((MOCKS / "scene_sigma0_db.tif").relative_to(REPO_ROOT)))
    engine = st.radio("Detection engine", ["auto", "ml", "threshold_fallback"],
                      horizontal=False,
                      help="auto: use the ML model if available, else the "
                           "dependency-free threshold detector")
    if st.button("Run pipeline", type="primary", use_container_width=True):
        run_id = f"inv-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        with st.spinner(f"Running {run_id}…"):
            proc = subprocess.run(
                [sys.executable, "-m", "backend.services.pipeline.run",
                 "--scene", str(REPO_ROOT / scene_choice),
                 "--scene-meta", str(MOCKS / "scene_meta.json"),
                 "--run-id", run_id, "--engine", engine],
                cwd=str(REPO_ROOT / "main_system"),
                capture_output=True, text=True)
        if proc.returncode == 0:
            st.success(f"{run_id} complete")
            st.code(proc.stdout[-1200:], language=None)
            st.cache_data.clear()
            st.rerun()
        else:
            st.error("Pipeline failed")
            st.code((proc.stderr or proc.stdout)[-1500:], language=None)

    st.divider()
    st.subheader("Layers")
    show = {
        "scene":    st.checkbox("SAR scene", True),
        "slick":    st.checkbox("Detected slick", True),
        "origin":   st.checkbox("Origin cloud + ellipses", True),
        "forecast": st.checkbox("Forecast spread", False),
        "vessels":  st.checkbox("AIS tracks", True),
    }

if not runs:
    st.info("Run the pipeline from the sidebar to create an investigation.")
    st.stop()

run_dir = RUNS / selected
run = read_run(run_dir)
manifest = run["manifest"] or {}

# --- provenance strip -----------------------------------------------------
st.subheader("Pipeline provenance")
cols = st.columns(len(manifest.get("stages", [])) or 1)
for col, s in zip(cols, manifest.get("stages", [])):
    text, colour, _ = BADGE.get(s["status"], ("?", "#6e7781", ""))
    col.markdown(
        f"**{s['stage'].replace('_', ' ')}**<br>{badge_html(s['status'])}"
        f"<br><span style='font-size:0.68rem;color:#57606a'>{s['owner']} · {s['seconds']}s</span>",
        unsafe_allow_html=True)
    if s.get("detail"):
        col.caption(s["detail"][:90])

real = sum(s["status"] in ("ok", "fallback") for s in manifest.get("stages", []))
total = len(manifest.get("stages", []))
st.caption(f"{real}/{total} stages ran for real · {manifest.get('total_seconds', '?')}s · "
           f"scene `{manifest.get('scene_id','?')}` · generated {manifest.get('generated_utc','?')}")

st.divider()
map_col, panel_col = st.columns([2.05, 1])

with map_col:
    st.subheader("Investigation map")
    try:
        from streamlit_folium import st_folium
        st_folium(build_map(run, show), height=620, use_container_width=True,
                  returned_objects=[])
    except Exception as exc:
        st.error(f"Map failed to render: {exc}")

with panel_col:
    st.subheader("Ranked suspects")
    att = stage_status(manifest, "attribution")
    st.markdown(badge_html(att.get("status", "failed")) +
                f" <span style='font-size:0.74rem;color:#57606a'>{att.get('detail','')}</span>",
                unsafe_allow_html=True)

    suspects = (run.get("suspects") or {}).get("suspects", [])
    if not suspects:
        st.info("No suspects in this run.")
    for s in suspects[:6]:
        top = s["rank"] == 1
        with st.container(border=True):
            st.markdown(
                f"**#{s['rank']} · {s.get('vessel_name') or s['mmsi']}**"
                f"{' 🎯' if top else ''}<br>"
                f"<span style='font-size:0.74rem;color:#57606a'>"
                f"MMSI {s['mmsi']} · {s['vessel_type']}</span>",
                unsafe_allow_html=True)
            st.progress(min(float(s["total_score"]), 1.0),
                        text=f"score {s['total_score']:.2f}")
            bars = ""
            for k, v in s.get("sub_scores", {}).items():
                bars += (f"<div class='factor-row'><span class='factor-name'>{k}</span>"
                         f"<span class='factor-bar'><span class='factor-fill' "
                         f"style='width:{float(v)*100:.0f}%'></span></span>"
                         f"<span class='factor-val'>{float(v):.2f}</span></div>")
            st.markdown(bars, unsafe_allow_html=True)
            if s.get("reason"):
                st.caption(s["reason"])

    filtered = (run.get("suspects") or {}).get("filtered_out", [])
    if filtered:
        with st.expander(f"Filtered out ({len(filtered)})"):
            for f in filtered:
                st.caption(f"MMSI {f['mmsi']} — {f['reason']}")

# --- detection detail -----------------------------------------------------
st.divider()
c1, c2 = st.columns([1, 1])
with c1:
    st.subheader("Detection")
    d = run.get("detect") or {}
    ds = stage_status(manifest, "detect")
    st.markdown(badge_html(ds.get("status", "failed")), unsafe_allow_html=True)
    if d:
        m1, m2, m3 = st.columns(3)
        m1.metric("Engine", d.get("engine", "?"))
        m2.metric("Confidence", f"{d.get('confidence', 0):.2f}")
        m3.metric("Runtime", f"{d.get('runtime_ms', 0)} ms")
        st.caption(f"model `{d.get('model_version','?')}` · "
                   f"{len(d.get('candidates', []))} candidate(s)")
        for c in d.get("candidates", [])[:5]:
            st.caption(f"· {c.get('class')} score {c.get('score')} bbox {c.get('bbox')}")
with c2:
    st.subheader("Slick geometry")
    sl = run.get("slick")
    cs = stage_status(manifest, "characterise")
    st.markdown(badge_html(cs.get("status", "failed")), unsafe_allow_html=True)
    if sl and sl.get("features"):
        p = sl["features"][0]["properties"]
        m1, m2, m3 = st.columns(3)
        m1.metric("Area", f"{p['area_km2']:.2f} km²")
        m2.metric("Length", f"{p.get('major_axis_m', 0)/1000:.1f} km")
        m3.metric("Bearing", f"{p.get('orientation_deg', 0):.0f}°")
        st.caption(f"perimeter {p['perimeter_km']:.1f} km · "
                   f"damping {p.get('damping_ratio')} dB · "
                   f"age {p.get('age_hours_estimate') or 'not estimated'}")

with st.expander("Stage detail / warnings"):
    for s in manifest.get("stages", []):
        st.markdown(f"**{s['stage']}** {badge_html(s['status'])} — {s.get('detail','')}",
                    unsafe_allow_html=True)
        for w in s.get("warnings", []):
            st.caption(f"· {w}")

# --------------------------------------------------------------------------
# model metrics -- reported exactly as measured, or not at all
# --------------------------------------------------------------------------

st.divider()
st.subheader("Model metrics")
st.caption("Measured numbers only. Pixel accuracy is deliberately absent — "
           "sea dominates every SAR tile, so an all-sea prediction scores >99% "
           "and means nothing.")

TRAIN_RUNS = REPO_ROOT / "data" / "runs" / "training"
m1, m2 = st.columns(2)

with m1:
    st.markdown("**Stage 1 · Screening detector** (DARTIS / YOLO)")
    sm = TRAIN_RUNS / "screen_metrics.json"
    if sm.exists():
        d = load_json(str(sm), sm.stat().st_mtime)
        a, b, c = st.columns(3)
        a.metric("mAP@0.5", f"{d.get('map50', 0):.3f}")
        b.metric("Precision", f"{d.get('precision', 0):.3f}")
        c.metric("Recall", f"{d.get('recall', 0):.3f}")
        if d.get("background_fp_rate") is not None:
            st.metric(
                "Background false-positive rate",
                f"{d['background_fp_rate']*100:.1f}%",
                help="Share of look-alike / no-oil patches where the model "
                     "still claims a slick. This is the number that answers "
                     "'how do you know that dark patch is oil?'")
            st.caption(f"{d['background_false_positives']} of "
                       f"{d['background_images']} no-oil patches fired "
                       f"(conf > {d.get('conf_threshold', 0.25)})")
    else:
        prog = TRAIN_RUNS / "screen" / "results.csv"
        if prog.exists():
            rows = prog.read_text(encoding="utf-8").strip().splitlines()
            st.info(f"Training in progress — {len(rows)-1} epochs complete. "
                    f"Metrics appear when the run finishes.")
        else:
            st.info("Not trained yet.")

with m2:
    st.markdown("**Stage 2 · Segmenter** (Trujillo / U-Net)")
    seg = TRAIN_RUNS / "metrics.json"
    if seg.exists():
        d = load_json(str(seg), seg.stat().st_mtime)
        half = (d.get("results", {}) or {}).get("0.5", {})
        o = half.get("overall", {})
        if o:
            a, b, c = st.columns(3)
            a.metric("Binary IoU", f"{o.get('iou', 0):.3f}")
            b.metric("Precision", f"{o.get('precision', 0):.3f}")
            c.metric("Recall", f"{o.get('recall', 0):.3f}")
        if half.get("scene_level_false_positive_rate") is not None:
            st.metric("No-oil tiles firing",
                      f"{half['scene_level_false_positive_rate']*100:.1f}%")
        st.caption(f"Trujillo Part III · {d.get('test_tiles','?')} test tiles · "
                   f"dB {d.get('db_range')}")
    else:
        st.info("Not trained yet — Trujillo Part III is still downloading. "
                "Detection currently runs on the threshold fallback.")

st.caption(
    "Attribution top-1/top-3 hit rate comes from Krishnan's 50-scenario "
    "benchmark once Engine C is wired; it is not shown until it is measured.")
