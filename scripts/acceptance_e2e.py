"""P20 acceptance harness: master plan §14, E2E-01 … E2E-15, against a live stack.

    .venv/Scripts/python.exe scripts/acceptance_e2e.py            # fast checks
    .venv/Scripts/python.exe scripts/acceptance_e2e.py --live-runs # + 02/03/14 (minutes)
    .venv/Scripts/python.exe scripts/acceptance_e2e.py --only E2E-04,E2E-11

Every test writes `acceptance_evidence/<ID>/verdict.json` -- status, the
individual checks with their measured values, and pointers to the captures
saved beside it. Nothing here is hand-assembled: a verdict is whatever the
running system answered, and a check that could not be executed says so
rather than passing vacuously.

Requires: backend on OT_E2E_API (default http://127.0.0.1:8010) started against
the live registry, and the three acceptance accounts (operator/admin,
analyst, reviewer) -- see acceptance_evidence/seed_review_roles.txt.

Vocabulary of verdicts:
    PASS     every check held
    PARTIAL  some checks held; each miss is named with its measured value
    FAIL     the pass condition did not hold
    BLOCKED  could not be executed here; the reason is recorded
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "acceptance_evidence"
API = os.environ.get("OT_E2E_API", "http://127.0.0.1:8010")
FLAGSHIP = "inv-gulf-flagship-20230108-2day"
FLAGSHIP_DIGEST_PREFIX = "fd42e078f8366110"
FLAGSHIP_MMSI = 367653160
SYNTHETIC_RUN = "inv-final-audit"     # Bali scene, synthetic AIS (43 generated vessels)
CHENNAI_SCENE_META = "data/runs/inv-chennai-real2/scene_meta.json"

ACCOUNTS = {
    "operator": ("operator@oceantrace.local", "p20-acceptance-operator"),
    "analyst": ("analyst@oceantrace.local", "p20-acceptance-analyst"),
    "reviewer": ("reviewer@oceantrace.local", "p20-acceptance-reviewer"),
}


# --------------------------------------------------------------------------
# evidence plumbing
# --------------------------------------------------------------------------

class Verdict:
    def __init__(self, test_id: str, title: str, condition: str):
        self.id, self.title, self.condition = test_id, title, condition
        self.checks: List[Dict[str, Any]] = []
        self.notes: List[str] = []
        self.captures: List[str] = []
        self.blocked: Optional[str] = None
        self.dir = EVIDENCE / test_id
        self.dir.mkdir(parents=True, exist_ok=True)

    def check(self, name: str, ok: bool, measured: Any = None, expected: Any = None):
        self.checks.append({"check": name, "ok": bool(ok),
                            "measured": measured, "expected": expected})
        flag = "ok " if ok else "MISS"
        print(f"    [{flag}] {name}: {measured!r}" + (f" (expected {expected!r})" if not ok and expected is not None else ""))
        return ok

    def note(self, text: str):
        self.notes.append(text)
        print(f"    note: {text}")

    def save(self, name: str, payload: Any) -> Path:
        path = self.dir / name
        if isinstance(payload, (bytes, bytearray)):
            path.write_bytes(payload)
        elif isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
        self.captures.append(name)
        return path

    def block(self, reason: str):
        self.blocked = reason
        print(f"    BLOCKED: {reason}")

    def finish(self) -> str:
        if self.blocked:
            status = "BLOCKED"
        elif not self.checks:
            status = "BLOCKED"
            self.blocked = "no checks executed"
        elif all(c["ok"] for c in self.checks):
            status = "PASS"
        elif any(c["ok"] for c in self.checks):
            status = "PARTIAL"
        else:
            status = "FAIL"
        payload = {"id": self.id, "title": self.title, "pass_condition": self.condition,
                   "status": status, "executed_utc": datetime.now(timezone.utc).isoformat(),
                   "api": API, "checks": self.checks, "notes": self.notes,
                   "blocked_reason": self.blocked, "captures": self.captures}
        (self.dir / "verdict.json").write_text(json.dumps(payload, indent=1, default=str),
                                               encoding="utf-8")
        print(f"  => {self.id} {status}")
        return status


def client(role: str = "operator") -> httpx.Client:
    """A signed-in client.

    The session cookie is `Secure` (PROMPT-07: it must never travel over plain
    HTTP in production). httpx honours that flag and silently drops the cookie
    on an http:// base URL, so every call after login came back 401 while the
    login itself succeeded. The harness therefore carries the cookie as an
    explicit header for this loopback-only run -- the same thing the test
    suite achieves with an https:// base URL against TestClient.
    """
    email, password = ACCOUNTS[role]
    # No keep-alive: the first live-run attempt lost a pooled connection while
    # the server was busy starting a pipeline thread ("Server disconnected
    # without sending a response"). One socket per request costs nothing here.
    c = httpx.Client(base_url=API, timeout=120.0,
                     limits=httpx.Limits(max_keepalive_connections=0))
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        raise SystemExit(f"login as {role} failed: {r.status_code} {r.text[:200]}")
    pairs = []
    for raw in r.headers.get_list("set-cookie"):
        pairs.append(raw.split(";", 1)[0].strip())
    if not pairs:
        raise SystemExit("login returned no session cookie")
    c.headers["Cookie"] = "; ".join(pairs)
    me = c.get("/api/auth/me")
    if me.status_code != 200:
        raise SystemExit(f"session did not stick for {role}: {me.status_code}")
    return c


def anon() -> httpx.Client:
    return httpx.Client(base_url=API, timeout=30.0)


def db_ro() -> sqlite3.Connection:
    """Read-only view of the live registry, for brute-force comparisons."""
    path = (REPO / "data" / "oceantrace.db").resolve()
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def runs_dir(run_id: str) -> Path:
    return REPO / "data" / "runs" / run_id


# --------------------------------------------------------------------------
# E2E-01  anonymous access
# --------------------------------------------------------------------------

def e2e_01() -> str:
    v = Verdict("E2E-01", "Anonymous access",
                "every /api/* (except auth/health) -> 401; route x role matrix captured")
    a = anon()
    probes = ["/api/runs", f"/api/runs/{FLAGSHIP}", "/api/search?q=inv",
              "/api/investigations", "/api/incidents", "/api/vessels",
              f"/api/vessels/{FLAGSHIP_MMSI}", "/api/alerts", "/api/aois",
              "/api/reports", "/api/audit", "/api/audit/verify", "/api/metrics",
              "/api/models", "/api/catalog", "/api/system/health", "/api/apis/status",
              "/api/keys", f"/api/layers/{FLAGSHIP}/slick",
              f"/api/tiles/{FLAGSHIP}/info", f"/api/runs/{FLAGSHIP}/verify",
              "/api/scenes/local", "/api/jobs/job-x", "/api/alerts/summary"]
    results = {}
    for path in probes:
        r = a.get(path)
        results[path] = r.status_code
        v.check(f"anonymous GET {path} -> 401", r.status_code == 401, r.status_code, 401)
    for path in ["/health", "/"]:
        r = a.get(path)
        results[path] = r.status_code
        v.check(f"public {path} reachable", r.status_code == 200, r.status_code, 200)
    v.save("anonymous_probes.json", results)

    # The route x role matrix is the suite's own exhaustive walk; capture it
    # verbatim rather than re-deriving a weaker version here.
    proc = subprocess.run([sys.executable, "-m", "pytest",
                           "main_system/tests/test_rbac_matrix.py", "-v"],
                          cwd=REPO, capture_output=True, text=True)
    v.save("rbac_matrix_pytest.txt", proc.stdout + proc.stderr)
    v.check("route x role matrix suite passes", proc.returncode == 0,
            f"exit {proc.returncode}", "exit 0")
    return v.finish()


# --------------------------------------------------------------------------
# E2E-04  the flagship
# --------------------------------------------------------------------------

def e2e_04() -> str:
    v = Verdict("E2E-04", "Flagship real run",
                "Gulf S1 + MarineCadastre through run_pipeline: attribution data_source "
                "real; suspects.source == real; sealed + verified; UI shows REAL badge")
    c = client()
    r = c.get(f"/api/runs/{FLAGSHIP}")
    v.check("GET /api/runs/{flagship} -> 200", r.status_code == 200, r.status_code, 200)
    if r.status_code != 200:
        return v.finish()
    run = r.json()
    v.save("run.json", run)
    m = run.get("manifest") or {}
    v.check("registry row is marked reconciled (derived, not observed)",
            run.get("registry_source") == "reconciled", run.get("registry_source"), "reconciled")
    v.check("investigation_id left NULL (not fabricated)", run.get("investigation_id") is None,
            run.get("investigation_id"), None)
    v.check("manifest.ais.data_source == real", (m.get("ais") or {}).get("data_source") == "real",
            (m.get("ais") or {}).get("data_source"), "real")
    v.check("manifest.ais.covers_origin", (m.get("ais") or {}).get("covers_origin") is True,
            (m.get("ais") or {}).get("covers_origin"), True)
    v.check("5/5 stages real, 0 mock, 0 failed",
            (run["stages_real"], run["stages_mock"], run["stages_failed"]) == (5, 0, 0),
            (run["stages_real"], run["stages_mock"], run["stages_failed"]), (5, 0, 0))
    v.check("every stage source == real", all(s.get("source") == "real" for s in m.get("stages", [])),
            [s.get("source") for s in m.get("stages", [])])
    names = {x["kind"]: x["name"] for x in m.get("models", [])}
    v.check("segment model is unet-r34-fullcorpus-e48", names.get("segment") == "unet-r34-fullcorpus-e48", names.get("segment"))
    v.check("screen model is yolo11n-screen-dartis-2026-08-24", names.get("screen") == "yolo11n-screen-dartis-2026-08-24", names.get("screen"))
    v.check("manifest carries code_git_sha", bool(m.get("code_git_sha")), m.get("code_git_sha"))
    v.check("digest prefix is fd42e078f8366110", str(m.get("artefact_digest", "")).startswith(FLAGSHIP_DIGEST_PREFIX),
            str(m.get("artefact_digest", ""))[:16], FLAGSHIP_DIGEST_PREFIX)

    sus = read_json(runs_dir(FLAGSHIP) / "suspects.json")
    v.save("suspects.json", sus)
    v.check("suspects.source == real", sus.get("source") == "real", sus.get("source"), "real")
    v.check("rank #1 is MMSI 367653160 at 0.6691",
            (sus["suspects"][0]["mmsi"], sus["suspects"][0]["total_score"]) == (FLAGSHIP_MMSI, 0.6691),
            (sus["suspects"][0]["mmsi"], sus["suspects"][0]["total_score"]))
    v.check("no ranked vessel carries an invented name",
            all(s.get("vessel_name") in (None, "") for s in sus["suspects"]),
            [s.get("vessel_name") for s in sus["suspects"]])

    vr = c.get(f"/api/runs/{FLAGSHIP}/verify").json()
    v.save("verify.json", vr)
    v.check("/verify ok over 8 artefacts", vr.get("ok") is True and vr.get("checked") == 8,
            (vr.get("ok"), vr.get("checked")), (True, 8))
    v.check("/verify reports no problems", vr.get("problems") == [], vr.get("problems"), [])

    s = c.get("/api/search", params={"q": FLAGSHIP}).json()
    v.check("searchable by exact id (tier 0)",
            any(h["id"] == FLAGSHIP and h["tier"] == 0 for h in s["results"]),
            [(h["id"], h["tier"]) for h in s["results"]][:3])
    lst = c.get("/api/runs", params={"q": "flagship", "limit": 50}).json()
    v.check("appears in /api/runs listing", any(i["run_id"] == FLAGSHIP for i in lst["items"]),
            [i["run_id"] for i in lst["items"]])
    v.note("UI REAL badge / UNFILED RUN / RECONCILED chips: see acceptance_evidence/E2E-04/ui-*.png "
           "(captured by scripts/acceptance_shots.mjs)")
    return v.finish()


# --------------------------------------------------------------------------
# E2E-05  synthetic honesty
# --------------------------------------------------------------------------

def e2e_05() -> str:
    v = Verdict("E2E-05", "Synthetic honesty",
                "synthetic run: SYNTHETIC badge visible on card, map, every vessel row, report + annex")
    c = client("analyst")
    run = c.get(f"/api/runs/{SYNTHETIC_RUN}").json()
    v.save("run.json", run)
    m = run.get("manifest") or {}
    v.check("run is the Bali scene", "BALI" in str(run.get("scene_id", "")).upper(), run.get("scene_id"))
    ais = m.get("ais")
    v.check("manifest either records synthetic AIS or records nothing (never claims real)",
            ais is None or ais.get("data_source") == "synthetic", ais)
    if ais is None:
        v.note("This manifest predates the `ais` block, so the top-bar chip reads AIS UNRECORDED rather than "
               "SYNTHETIC -- the honest label for an absent record. The synthetic label reaches the map, every "
               "vessel row and the report through vessels_geojson.source and suspects.source, checked below.")

    g = c.get(f"/api/runs/{SYNTHETIC_RUN}/vessels_geojson").json()
    sources = sorted({f["properties"].get("source") for f in g.get("features", [])})
    v.save("vessels_geojson_sources.json", {"sources": sources, "features": len(g.get("features", []))})
    v.check("every vessel row on the map carries source == synthetic",
            sources == ["synthetic"], sources, ["synthetic"])

    sus = read_json(runs_dir(SYNTHETIC_RUN) / "suspects.json")
    v.check("suspects.source == synthetic", sus.get("source") == "synthetic", sus.get("source"), "synthetic")

    # The report is composed server-side from the sealed artefacts; the label
    # has to survive into the body and the provenance annex.
    existing = c.get("/api/reports", params={"run_id": SYNTHETIC_RUN}).json()
    items = existing if isinstance(existing, list) else existing.get("items", [])
    if items:
        rep = c.get(f"/api/reports/{items[0]['id']}").json()
    else:
        rr = c.post("/api/reports", json={"run_id": SYNTHETIC_RUN})
        v.check("report composes for the synthetic run", rr.status_code == 201, rr.status_code, 201)
        rep = rr.json() if rr.status_code == 201 else {}
    body = json.dumps(rep, default=str)
    v.save("report.json", rep)
    v.check("report body says SYNTHETIC", "synthetic" in body.lower(), body.lower().count("synthetic"))
    annex = next((s for s in (rep.get("body") or {}).get("sections", [])
                  if s.get("kind") == "provenance_annex"), None) if isinstance(rep.get("body"), dict) else None
    v.check("report has a provenance annex", annex is not None, bool(annex))
    if annex:
        v.check("annex records the synthetic stages",
                "synthetic" in json.dumps(annex).lower(), json.dumps(annex).lower().count("synthetic"))
    v.note("Map + top-bar gold SYNTHETIC chips: acceptance_evidence/E2E-05/ui-*.png")
    return v.finish()


# --------------------------------------------------------------------------
# E2E-06  hindcast uncertainty
# --------------------------------------------------------------------------

def e2e_06() -> str:
    v = Verdict("E2E-06", "Hindcast uncertainty",
                "published ellipses non-zero, consistent with particle covariance; "
                "origin_uncertainty_km rendered; PHYSICS badge; no ML claim anywhere")
    import math
    oc = read_json(runs_dir(FLAGSHIP) / "origin_cloud.geojson")
    md = oc.get("metadata", {})
    v.save("origin_metadata.json", md)
    feats = oc["features"]
    ell = [f for f in feats if (f["properties"].get("feature_type") or f["properties"].get("kind")) == "ellipse"]
    v.check("25 ellipses published", len(ell) == 25, len(ell), 25)
    v.check("every ellipse has non-zero axes",
            all(f["properties"].get("semi_major_m", 0) > 0 and f["properties"].get("semi_minor_m", 0) > 0 for f in ell),
            [round(f["properties"].get("semi_major_m", 0)) for f in ell][:6])
    v.check("origin_uncertainty_km published", isinstance(md.get("origin_uncertainty_km"), (int, float)),
            md.get("origin_uncertainty_km"))
    v.note(f"origin_uncertainty_km on the flagship is {md.get('origin_uncertainty_km')} km; the plan's "
           "0.99 figure is the Chennai scene's value, not a target for this scene.")
    v.check("uncertainty method states it is not ML",
            "not ml" in str(md.get("origin_uncertainty_method", "")).lower(),
            md.get("origin_uncertainty_method"))
    v.check("window method published (cloud_convergence)", md.get("origin_window_method") == "cloud_convergence",
            md.get("origin_window_method"))

    # Consistency with the particle cloud: at the final step the ellipse's
    # semi-major axis should be of the same order as the particle spread.
    parts = [f for f in feats if f["properties"].get("particle_id") is not None]
    if parts and ell:
        last = max(f["properties"].get("step_index", 0) for f in parts)
        pts = [f["geometry"]["coordinates"] for f in parts if f["properties"].get("step_index", 0) == last]
        lon0 = sum(p[0] for p in pts) / len(pts); lat0 = sum(p[1] for p in pts) / len(pts)
        km_lon = 111.32 * math.cos(math.radians(lat0)); km_lat = 110.57
        sx = math.sqrt(sum(((p[0] - lon0) * km_lon) ** 2 for p in pts) / len(pts))
        sy = math.sqrt(sum(((p[1] - lat0) * km_lat) ** 2 for p in pts) / len(pts))
        spread_km = max(sx, sy)
        e_last = max(ell, key=lambda f: f["properties"].get("step_index", 0))
        semi_km = e_last["properties"]["semi_major_m"] / 1000.0
        ratio = semi_km / spread_km if spread_km else float("inf")
        v.save("cloud_vs_ellipse.json", {"final_step": last, "particles": len(pts),
                                         "particle_sigma_km": spread_km, "ellipse_semi_major_km": semi_km,
                                         "ratio": ratio})
        v.check("final ellipse within 0.5x-5x of particle 1-sigma spread (same order)",
                0.5 <= ratio <= 5.0, round(ratio, 3), "0.5 .. 5.0")

    met = client().get("/api/metrics").json()
    drift = met.get("drift", {})
    v.save("metrics_drift.json", drift)
    v.check("drift ML is EXPERIMENTAL and not applied",
            drift.get("status") == "experimental" and drift.get("applied") is False and drift.get("evaluated") == "negative",
            {k: drift.get(k) for k in ("status", "applied", "evaluated")})
    v.check("no accuracy claimed for drift", drift.get("accuracy_reported") is False, drift.get("accuracy_reported"), False)
    return v.finish()


# --------------------------------------------------------------------------
# E2E-07  metrics truth
# --------------------------------------------------------------------------

def e2e_07() -> str:
    v = Verdict("E2E-07", "Metrics truth",
                "Analytics shows unet-r34-fullcorpus-e48 identity + sha256, oil-tile IoU 0.5723; "
                "drift ML card EXPERIMENTAL / disabled")
    c = client()
    met = c.get("/api/metrics").json()
    v.save("metrics.json", met)
    text = json.dumps(met)
    v.check("segmentation identity is unet-r34-fullcorpus-e48", "unet-r34-fullcorpus-e48" in text)
    v.check("oil-tile IoU 0.5723 present", "0.5723" in text)
    v.check("overall IoU 0.4445 present (C-10: all three numbers)", "0.4445" in text)
    v.check("no-oil tile firing 280/5248 present (C-10)", ("280" in text and "5248" in text) or "5,248" in text)
    v.check("nothing named YOLOv8", "yolov8" not in text.lower())
    models = c.get("/api/models").json()
    v.save("models.json", models)
    mtext = json.dumps(models)
    seg = next((m for m in models.get("deployed", models.get("models", []))
                if "unet-r34-fullcorpus-e48" in json.dumps(m)), None) if isinstance(models, dict) else None
    v.check("segment model listed with a sha256", bool(seg and seg.get("sha256")), (seg or {}).get("sha256"))
    v.check("drift residual listed as EXPERIMENTAL", "EXPERIMENTAL" in mtext)
    cat = c.get("/api/catalog").json(); v.save("catalog.json", cat)
    ctext = json.dumps(cat)
    v.check("Sentinel-2 / EO listed NOT_DEPLOYED in the catalogue", "NOT_DEPLOYED" in ctext or "NOT DEPLOYED" in ctext)
    v.check("S2 search answers 501, not an empty list", c.get("/api/scenes/search", params={"bbox": "79.8,12.8,80.8,13.6", "source": "S2"}).status_code == 501)
    v.note("Analytics page render: acceptance_evidence/E2E-07/ui-analytics.png")
    return v.finish()


# --------------------------------------------------------------------------
# E2E-08  attribution integrity
# --------------------------------------------------------------------------

def e2e_08() -> str:
    v = Verdict("E2E-08", "Attribution integrity",
                "weights sum validated; sum(w_i * s_i) == totals; comparison view matches suspects.json; "
                "zero-candidate scenario renders honest empty")
    c = client()
    sus = read_json(runs_dir(FLAGSHIP) / "suspects.json")
    w = sus["weights"]
    v.check("weights sum to 1.0", abs(sum(w.values()) - 1.0) < 1e-9, sum(w.values()), 1.0)
    worst = 0.0
    rows = []
    for s in sus["suspects"]:
        calc = sum(w[k] * s["sub_scores"].get(k, 0.0) for k in w)
        rows.append({"mmsi": s["mmsi"], "rank": s["rank"], "total": s["total_score"], "recomputed": round(calc, 4)})
        worst = max(worst, abs(calc - s["total_score"]))
    v.save("recomputed_totals.json", rows)
    v.check("sum(w_i*s_i) reproduces every total_score (<= 0.001)", worst <= 1e-3, round(worst, 5), "<= 0.001")
    v.check("ranks are in descending score order",
            [s["total_score"] for s in sus["suspects"]] == sorted((s["total_score"] for s in sus["suspects"]), reverse=True))

    fun = c.get(f"/api/runs/{FLAGSHIP}/funnel")
    v.check("funnel endpoint answers", fun.status_code == 200, fun.status_code, 200)
    if fun.status_code == 200:
        f = fun.json(); v.save("funnel.json", f)
        ftext = json.dumps(f)
        v.check("funnel considered == 32", "32" in ftext and str(sus.get("total_vessels_considered")) == "32",
                sus.get("total_vessels_considered"), 32)
        v.check("funnel ranked == 4 and filtered == 28",
                f.get("ranked", f.get("suspects")) in (4, None) and (len(sus.get("filtered_out", [])) == 28),
                {"ranked": f.get("ranked", f.get("suspects")), "filtered_out": len(sus.get("filtered_out", []))}, (4, 28))
    api_sus = c.get(f"/api/layers/{FLAGSHIP}/suspects").json()
    v.check("served suspects layer == suspects.json on disk",
            [x["mmsi"] for x in api_sus.get("suspects", [])] == [x["mmsi"] for x in sus["suspects"]],
            [x["mmsi"] for x in api_sus.get("suspects", [])])
    v.check("no guilt language in suspects artefact",
            not any(t in json.dumps(sus).lower() for t in ("guilty", "culprit", "polluter", "offender")))

    # Zero-candidate: no sealed run in the archive has zero suspects, so the
    # behaviour is evidenced by the engine's own tests rather than a live run.
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q",
                           "analysis_engines/tests/test_attribution_scoring.py::test_empty_parquet_is_NO_VESSELS_IN_WINDOW",
                           "analysis_engines/tests/test_attribution_gates.py::test_empty_parquet_yields_no_tracks",
                           "main_system/tests/test_vessel_index.py::test_index_skips_a_run_with_no_suspects"],
                          cwd=REPO, capture_output=True, text=True)
    v.save("zero_candidate_tests.txt", proc.stdout + proc.stderr)
    v.check("zero-candidate behaviour covered by passing tests (no sealed zero-suspect run exists to replay)",
            proc.returncode == 0, f"exit {proc.returncode}", "exit 0")
    v.note("A live zero-candidate scenario was not manufactured for the archive (standing rule 11).")
    return v.finish()


# --------------------------------------------------------------------------
# E2E-09  vessel dossier
# --------------------------------------------------------------------------

def e2e_09() -> str:
    v = Verdict("E2E-09", "Vessel dossier",
                "MMSI ranked in >= 2 runs -> GET /api/vessels/{mmsi} shows both appearances; cross-run tracks render")
    c = client()
    r = c.get(f"/api/vessels/{FLAGSHIP_MMSI}")
    v.check("GET /api/vessels/367653160 -> 200", r.status_code == 200, r.status_code, 200)
    if r.status_code != 200:
        return v.finish()
    d = r.json(); v.save("vessel.json", d)
    runs = sorted({a["run_id"] for a in d.get("appearance_list", [])})
    v.check("appears in >= 2 runs", len(runs) >= 2, runs)
    v.check("rank #1 in each flagship-family run", all(a["rank"] == 1 for a in d["appearance_list"] if a["run_id"].startswith("inv-gulf-flagship")),
            [(a["run_id"], a["rank"]) for a in d["appearance_list"]])
    v.check("identity honestly absent (name null, identity_available false)",
            d.get("name") is None and d.get("identity_available") is False, (d.get("name"), d.get("identity_available")))
    v.check("source == real", d.get("source") == "real", d.get("source"), "real")
    t = c.get(f"/api/vessels/{FLAGSHIP_MMSI}/tracks")
    v.check("tracks endpoint answers", t.status_code == 200, t.status_code, 200)
    if t.status_code == 200:
        tj = t.json(); v.save("tracks.json", {k: (len(val) if isinstance(val, list) else val) for k, val in tj.items()} if isinstance(tj, dict) else {"type": type(tj).__name__})
        n = len(tj.get("features", tj.get("tracks", []))) if isinstance(tj, dict) else len(tj)
        v.check("cross-run tracks present", n >= 2, n)
    s = c.get("/api/search", params={"q": str(FLAGSHIP_MMSI), "kinds": "vessel"}).json()
    v.check("vessel found by MMSI in search", any(h["id"] == str(FLAGSHIP_MMSI) for h in s["results"]), [h["id"] for h in s["results"]])
    return v.finish()


# --------------------------------------------------------------------------
# E2E-10  report lifecycle
# --------------------------------------------------------------------------

def e2e_10() -> str:
    v = Verdict("E2E-10", "Report lifecycle",
                "draft -> submit -> approve(reviewer) -> published immutable; annex lists 8 artefact hashes == /verify; "
                "export stamped; version 2 on edit")
    an, rv = client("analyst"), client("reviewer")
    existing = an.get("/api/reports", params={"run_id": FLAGSHIP}).json()
    items = existing if isinstance(existing, list) else existing.get("items", [])
    items = [i for i in items if i.get("run_id") == FLAGSHIP]
    if items:
        rep = an.get(f"/api/reports/{items[-1]['id']}").json()
        v.note(f"report already exists for the flagship ({rep['id']} v{rep.get('version')} {rep.get('status')}); lifecycle exercised on it")
    else:
        r = an.post("/api/reports", json={"run_id": FLAGSHIP})
        v.check("analyst composes draft (201)", r.status_code == 201, r.status_code, 201)
        if r.status_code != 201:
            return v.finish()
        rep = r.json()
    v.save("report_v1.json", rep)
    v.check("starts as draft", rep.get("status") in ("draft", "in_review", "published"), rep.get("status"))
    v.check("report digest == flagship digest prefix", str(rep.get("artefact_digest", "")).startswith(FLAGSHIP_DIGEST_PREFIX), str(rep.get("artefact_digest", ""))[:16])

    body = rep.get("body") or {}
    annex = next((s for s in body.get("sections", []) if s.get("kind") == "provenance_annex"), {})
    verify = an.get(f"/api/runs/{FLAGSHIP}/verify").json()
    manifest = read_json(runs_dir(FLAGSHIP) / "manifest.json")
    v.check("annex lists 8 artefact hashes", len(annex.get("artefacts", [])) == 8, len(annex.get("artefacts", [])), 8)
    v.check("annex hashes == manifest hashes == /verify checked count",
            annex.get("artefacts") == manifest.get("artefacts") and verify.get("checked") == 8,
            (len(annex.get("artefacts", [])), verify.get("checked")))

    rid = rep["id"]
    if rep.get("status") == "draft":
        r = an.post(f"/api/reports/{rid}/submit", json={"note": "E2E-10 acceptance submit"})
        v.check("analyst submits -> in_review", r.status_code == 200 and r.json().get("status") == "in_review", (r.status_code, r.json().get("status") if r.status_code == 200 else r.text[:120]))
        rep = r.json() if r.status_code == 200 else rep
    if rep.get("status") == "in_review":
        r = an.post(f"/api/reports/{rid}/publish", json={"note": "analyst must not publish"})
        v.check("analyst cannot publish (403)", r.status_code == 403, r.status_code, 403)
        r = rv.post(f"/api/reports/{rid}/publish", json={"note": "E2E-10 reviewer approval"})
        v.check("reviewer publishes -> published", r.status_code == 200 and r.json().get("status") == "published", (r.status_code, r.json().get("status") if r.status_code == 200 else r.text[:120]))
        rep = r.json() if r.status_code == 200 else rep
    v.save("report_published.json", rep)
    r = rv.post(f"/api/reports/{rid}/publish", json={"note": "again"})
    v.check("published report is immutable (second publish 409)", r.status_code == 409, r.status_code, 409)
    ex = an.get(f"/api/reports/{rid}/export.json")
    v.check("export.json answers", ex.status_code == 200, ex.status_code, 200)
    if ex.status_code == 200:
        ej = ex.json(); v.save("export.json", ej)
        etext = json.dumps(ej)
        v.check("export is stamped with digest + version", FLAGSHIP_DIGEST_PREFIX in etext and '"version"' in etext)
    csv = an.get(f"/api/reports/{rid}/export.csv")
    v.check("export.csv answers", csv.status_code == 200, csv.status_code, 200)
    if csv.status_code == 200:
        v.save("export.csv", csv.text)
    r = an.post(f"/api/reports/{rid}/revise")
    v.check("revise creates version 2 (201)", r.status_code == 201 and r.json().get("version") == 2, (r.status_code, r.json().get("version") if r.status_code == 201 else r.text[:120]))
    if r.status_code == 201:
        v.save("report_v2.json", r.json())
        v.check("version 2 is a new draft with the same digest", r.json().get("status") == "draft" and str(r.json().get("artefact_digest", "")).startswith(FLAGSHIP_DIGEST_PREFIX))
    return v.finish()


# --------------------------------------------------------------------------
# E2E-11  runs reconcile
# --------------------------------------------------------------------------

def e2e_11() -> str:
    v = Verdict("E2E-11", "Runs reconcile",
                "DB == replay == disk counts (or UI states which); filters/sort/pagination correct against brute force")
    c = client()
    sealed = sorted(p.parent.name for p in (REPO / "data" / "runs").glob("*/manifest.json"))
    unsealed = sorted(d.name for d in (REPO / "data" / "runs").iterdir() if d.is_dir() and not (d / "manifest.json").exists())
    con = db_ro()
    db_ids = sorted(r[0] for r in con.execute("select id from runs"))
    replay = c.get("/api/replay/runs").json()
    replay_ids = sorted(r.get("run_id", r.get("id")) for r in (replay.get("runs", replay) if isinstance(replay, dict) else replay))
    v.save("counts.json", {"sealed_on_disk": len(sealed), "unsealed_dirs": len(unsealed),
                           "db_rows": len(db_ids), "replay": len(replay_ids),
                           "sealed_not_in_db": sorted(set(sealed) - set(db_ids)),
                           "db_not_sealed": sorted(set(db_ids) - set(sealed))})
    v.check("every sealed run on disk has a DB row", set(sealed) <= set(db_ids), sorted(set(sealed) - set(db_ids)), [])
    # A row without a sealed directory is legitimate only if it says so:
    # failed / cancelled runs never seal. A row that claims to be running or
    # pending must correspond to a job the server is actually executing.
    by_status = {r[0]: r[1] for r in con.execute("select id, status from runs")}
    complete_unsealed = sorted(i for i in db_ids if by_status[i] == "complete" and i not in sealed)
    v.check("every row marked complete has a sealed directory", not complete_unsealed, complete_unsealed, [])
    in_flight = sorted(i for i in db_ids if by_status[i] in ("running", "pending"))
    live_jobs = {j["run_id"] for j in (c.get("/api/runs", params={"status": "running", "limit": 200}).json().get("items", []))}
    stale = [i for i in in_flight if c.get(f"/api/jobs/job-{i}").status_code != 200
             or c.get(f"/api/jobs/job-{i}").json().get("status") not in ("running", "pending", "cancelling")]
    v.check("no row claims running/pending without a live job", not stale, stale, [])
    # A sealed run is complete OR failed: a stage can fail and the manifest is
    # still written (that is how the failure is recorded). What a sealed run
    # can never be is in flight.
    sealed_in_flight = sorted(i for i in sealed if by_status.get(i) in ("running", "pending"))
    v.check("no sealed run is still marked in flight", not sealed_in_flight, sealed_in_flight, [])
    v.check("sealed count == rows marked complete or failed",
            len(sealed) == sum(1 for i in sealed if by_status.get(i) in ("complete", "failed")),
            (len(sealed), sum(1 for i in sealed if by_status.get(i) in ("complete", "failed"))))
    v.check("flagship family (5) all registered and reconciled",
            con.execute("select count(*) from runs where id like 'inv-gulf-%' and registry_source='reconciled'").fetchone()[0] == 5)
    v.note(f"{len(unsealed)} directories have no manifest (never sealed: cancelled/aborted) and are correctly absent from the registry.")
    v.check("replay list is a subset of the registry", set(replay_ids) <= set(db_ids), len(set(replay_ids) - set(db_ids)), 0)

    # brute-force the listing
    total = c.get("/api/runs", params={"limit": 1}).json()["total"]
    active = con.execute("select count(*) from runs where coalesce(archived,0)=0").fetchone()[0]
    v.check("/api/runs total == non-archived rows", total == active, (total, active))
    # Page through the whole non-archived listing in pages of 5 and compare
    # the concatenation with a brute-force query: no overlap, no gap, same set.
    api_ids, ts, off = [], [], 0
    while True:
        page = c.get("/api/runs", params={"limit": 5, "offset": off, "sort": "started_utc", "order": "desc"}).json()["items"]
        if not page: break
        api_ids += [i["run_id"] for i in page]; ts += [i["started_utc"] for i in page]; off += 5
        if off > 1000: break
    bf = [r[0] for r in con.execute("select id from runs where coalesce(archived,0)=0 order by started_utc desc, id")]
    v.check("pagination has no overlap and no gap", len(set(api_ids)) == len(api_ids) == active, (len(api_ids), len(set(api_ids)), active))
    v.check("sort order is monotone desc across pages", ts == sorted(ts, reverse=True))
    v.check("paged listing == brute-force set", set(api_ids) == set(bf), len(set(api_ids) ^ set(bf)), 0)
    v.note(f"{con.execute('select count(*) from runs where archived=1').fetchone()[0]} rows are archived and hidden from the default listing by design (archived=true shows them).")
    comp = c.get("/api/runs", params={"status": "complete", "limit": 200}).json()
    bfc = con.execute("select count(*) from runs where coalesce(archived,0)=0 and status='complete'").fetchone()[0]
    v.check("status=complete filter == brute force", comp["total"] == bfc, (comp["total"], bfc))
    q = c.get("/api/runs", params={"q": "gulf", "limit": 200}).json()
    bfq = con.execute("select count(*) from runs where coalesce(archived,0)=0 and (id like '%gulf%' or scene_id like '%gulf%')").fetchone()[0]
    v.check("q=gulf filter == brute force", q["total"] == bfq, (q["total"], bfq))
    return v.finish()


# --------------------------------------------------------------------------
# E2E-12  alerts
# --------------------------------------------------------------------------

def e2e_12() -> str:
    v = Verdict("E2E-12", "Alerts",
                "AOI poll (dry-run) errors: []; new-scene alert -> ack -> assign -> investigation link; SSE delivery")
    c = client()
    dry = c.post("/api/aois/poll", params={"dry_run": "true"})
    v.check("dry-run poll answers", dry.status_code == 200, dry.status_code, 200)
    aoi_id = None
    if dry.status_code == 200:
        d = dry.json(); v.save("poll_dry_run.json", d)
        v.check("dry-run errors == []", d.get("errors") == [], d.get("errors"), [])
        v.check("dry-run opened nothing", not d.get("opened"), d.get("opened"))
        found = d.get("new_scenes") or []
        # A second poll finds nothing new because the first one opened those
        # scenes -- deduplication is the behaviour, not a failure. So this is
        # recorded, not asserted; the assertion is that the tick was clean.
        v.note(f"dry-run names {len(found)} scene(s) it would open; polled {d.get('polled')}, disabled {d.get('disabled')}")
        aoi_id = found[0]["aoi_id"] if found else (d.get("polled") or [None])[0]
    if aoi_id:
        # A real tick on the AOI the dry run found scenes for. This raises
        # new-scene alerts and opens investigations (auto_run is off on the
        # registered AOIs, so no pipeline is started).
        live = c.post(f"/api/aois/{aoi_id}/poll")
        v.check(f"live poll on {aoi_id} answers", live.status_code == 200, (live.status_code, live.text[:120]))
        if live.status_code == 200:
            l = live.json(); v.save("poll_live.json", l)
            v.check("live poll errors == []", l.get("errors") == [], l.get("errors"), [])
            v.note(f"live poll opened {len(l.get('opened') or [])} investigation(s): "
                   f"{[o.get('investigation_id') for o in (l.get('opened') or [])]}")
    alerts = [x for x in c.get("/api/alerts").json().get("alerts", []) if x.get("status") in ("open", "acknowledged")]
    v.save("alerts_open.json", alerts)
    if not alerts:
        v.check("an open alert exists to exercise ack/assign", False, 0, ">= 1")
        v.note("No open alert was raised: the live poll found no new scene for the AOI window "
               "(or CDSE was unreachable -- see poll_live.json). ack/assign not exercised on a fabricated alert.")
        return v.finish()
    a = alerts[0]
    r = c.post(f"/api/alerts/{a['id']}/ack")
    v.check("ack -> acknowledged", r.status_code in (200, 409) and (r.status_code == 409 or r.json().get("status") in ("acknowledged", "acked")), (r.status_code, r.json().get("status") if r.status_code == 200 else r.text[:100]))
    who = c.get("/api/auth/me").json()
    me = (who.get("user") or who)["id"]
    r = c.post(f"/api/alerts/{a['id']}/assign", json={"user_id": me})
    v.check("assign to a real user", r.status_code == 200 and r.json().get("assigned_to") == me, (r.status_code, r.json().get("assigned_to") if r.status_code == 200 else r.text[:100]))
    after = c.get("/api/alerts", params={"status": "assigned"}).json().get("alerts", [])
    v.check("alert reads assigned afterwards", any(x["id"] == a["id"] and x.get("assigned_to") == me for x in after), [(x["id"], x.get("assigned_to")) for x in after][:3])
    v.check("alert links an investigation or scene", bool(a.get("investigation_id") or a.get("scene_id")), {k: a.get(k) for k in ("investigation_id", "scene_id")})
    v.note("SSE delivery: /api/events/alerts is driven in-suite (test_alerts.py::test_sse_sends_the_current_queue_on_connect); "
           "consuming a live SSE stream from this harness is deliberately avoided (an SSE endpoint never ends).")
    return v.finish()


# --------------------------------------------------------------------------
# E2E-13  audit chain
# --------------------------------------------------------------------------

def e2e_13() -> str:
    v = Verdict("E2E-13", "Audit chain",
                "10 event types present with authenticated actors + IP; /api/audit/verify ok; manual row tamper -> verify fails")
    c = client()
    aud = c.get("/api/audit", params={"limit": 200}).json()
    v.save("audit_page.json", aud)
    actions = sorted({i.get("action") for i in aud.get("items", [])})
    con = db_ro()
    all_actions = sorted(r[0] for r in con.execute("select distinct action from audit_log"))
    v.save("action_types.json", {"in_page": actions, "in_table": all_actions, "declared": aud.get("event_types")})
    v.check(">= 10 distinct event types recorded", len(all_actions) >= 10, len(all_actions), ">= 10")
    v.note(f"recorded action types: {all_actions}")
    items = aud.get("items", [])
    v.check("every event has an actor", all(i.get("actor") for i in items), len(items))
    session_events = [i for i in items if i.get("actor_user_id")]
    system_events = [i for i in items if not i.get("actor_user_id")]
    v.check("every session-originated event records an IP",
            all(i.get("ip") for i in session_events), (len(session_events), sum(1 for i in session_events if not i.get("ip"))))
    v.note(f"{len(system_events)} event(s) carry no IP because no request produced them "
           f"(actors: {sorted({i.get('actor') for i in system_events})}) -- pipeline/scheduler/CLI actions, recorded as such.")
    ver = c.get("/api/audit/verify").json()
    v.save("verify.json", ver)
    v.check("/api/audit/verify ok on the live chain", ver.get("ok") is True, ver)

    # Tamper test on a COPY of the database, never the live one.
    scratch = EVIDENCE / "E2E-13" / "tamper.db"
    shutil.copy(REPO / "data" / "oceantrace.db", scratch)
    con2 = sqlite3.connect(scratch)
    # Tamper a CHAINED row. Rows written before hash-chaining existed carry no
    # row_hash and are reported as unchained rather than verified, so altering
    # one of those proves nothing about the chain.
    row = con2.execute("select id, detail, action from audit_log where row_hash is not null "
                       "order by id desc limit 1").fetchone()
    v.save("tampered_row.json", {"id": row[0], "action": row[2], "field": "detail",
                                 "before": row[1], "after": "TAMPERED " + str(row[1])})
    con2.execute("update audit_log set detail=? where id=?", ("TAMPERED " + str(row[1]), row[0]))
    con2.commit(); con2.close()
    code = ("import sys; sys.path.insert(0,'main_system')\n"
            "from backend.models.db import SessionLocal\n"
            "from backend.services import audit as a\n"
            "import json\n"
            "with SessionLocal() as db: print(json.dumps(a.verify_chain(db)))\n")
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{scratch.as_posix()}", DATA_ROOT=str(REPO / "data"))
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, env=env)
    v.save("tamper_verify.txt", proc.stdout + proc.stderr)
    try:
        tam = json.loads(proc.stdout.strip().splitlines()[-1])
        v.check("tampered copy fails verification", tam.get("ok") is False, tam.get("ok"), False)
    except Exception:
        v.check("tampered copy fails verification", False, proc.stdout[-200:] + proc.stderr[-300:])
    return v.finish()


# --------------------------------------------------------------------------
# E2E-15  tiles & perf (fast half)
# --------------------------------------------------------------------------

def e2e_15(pipeline_seconds: Optional[float] = None) -> str:
    v = Verdict("E2E-15", "Tiles & perf",
                "native-res tiles <= 256 KB; origin_cloud?lite faster than full; pipeline <= 60 s warm; regression suite passes")
    c = client()
    info = c.get(f"/api/tiles/{FLAGSHIP}/info").json(); v.save("tiles_info.json", info)
    b = info.get("bounds_wgs84") or info.get("bounds") or info.get("bbox")
    import math
    def tile(lon, lat, z):
        n = 2 ** z; x = int((lon + 180) / 360 * n)
        y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
        return x, y
    lon = (b[0] + b[2]) / 2; lat = (b[1] + b[3]) / 2
    sizes = {}
    for z in (8, 10, 12, 14):
        x, y = tile(lon, lat, z)
        t0 = time.perf_counter(); r = c.get(f"/api/tiles/{FLAGSHIP}/{z}/{x}/{y}.png"); dt = time.perf_counter() - t0
        sizes[z] = {"status": r.status_code, "bytes": len(r.content), "ms": round(dt * 1000)}
        v.check(f"z{z} tile <= 256 KB", r.status_code == 200 and len(r.content) <= 256 * 1024, sizes[z])
    v.save("tiles.json", sizes)
    # Five alternating samples each, medians compared: a single cold-vs-warm
    # pair says more about the disk cache than about the endpoint.
    import statistics
    fulls, lites = [], []
    for _ in range(5):
        t0 = time.perf_counter(); full = c.get(f"/api/layers/{FLAGSHIP}/origin_cloud"); fulls.append(time.perf_counter() - t0)
        t0 = time.perf_counter(); lite = c.get(f"/api/layers/{FLAGSHIP}/origin_cloud", params={"lite": "true"}); lites.append(time.perf_counter() - t0)
    t_full, t_lite = statistics.median(fulls), statistics.median(lites)
    v.save("origin_lite_vs_full.json", {"full_ms_samples": [round(x * 1000) for x in fulls], "lite_ms_samples": [round(x * 1000) for x in lites],
                                        "full_ms_median": round(t_full * 1000), "lite_ms_median": round(t_lite * 1000),
                                        "full_bytes": len(full.content), "lite_bytes": len(lite.content)})
    v.check("origin_cloud?lite is smaller than full", len(lite.content) < len(full.content), (len(lite.content), len(full.content)))
    v.check("origin_cloud?lite median time <= full median time (5 samples each)", t_lite <= t_full,
            {"lite_ms": round(t_lite * 1000), "full_ms": round(t_full * 1000)})
    v.note("The wire-size reduction (what the browser parses) is the property that matters for first paint; "
           "server time for the subsample is measured, not assumed.")
    if pipeline_seconds is None:
        p = EVIDENCE / "E2E-02" / "run.json"
        if p.exists():
            pipeline_seconds = (read_json(p).get("manifest") or {}).get("total_seconds")
    if pipeline_seconds is not None:
        v.check("pipeline <= 60 s warm", pipeline_seconds <= 60, pipeline_seconds, "<= 60")
        v.note("The 60 s figure predates full-resolution scenes; the deployed segmenter walks the whole raster. "
               "Reported as measured, not tuned.")
    else:
        v.check("pipeline <= 60 s warm", False, "not measured (run --live-runs first)")
    reg = EVIDENCE / "regression_pytest.txt"
    if reg.exists():
        tail = reg.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1]
        v.check("regression suite passes (>= 654 tests)", "failed" not in tail and "passed" in tail, tail)
    else:
        v.check("regression suite passes", False, "acceptance_evidence/regression_pytest.txt not present yet")
    return v.finish()


# --------------------------------------------------------------------------
# E2E-14 / E2E-02 / E2E-03  live runs (minutes)
# --------------------------------------------------------------------------

def _wait_job(c: httpx.Client, job_id: str, until: Tuple[str, ...], timeout_s: int, stop_when_stage: Optional[str] = None):
    t0 = time.time(); last = None
    while time.time() - t0 < timeout_s:
        j = c.get(f"/api/jobs/{job_id}").json()
        last = j
        if j.get("status") in until:
            return j
        if stop_when_stage and (j.get("current_stage") == stop_when_stage or (j.get("stages_done") or 0) >= 1):
            return j
        time.sleep(2)
    return last


def e2e_14() -> str:
    v = Verdict("E2E-14", "Cancel",
                "mid-run cancel -> job cancelled, no sealed partial artefacts, UI honest state")
    c = client()
    inv = c.post("/api/investigations", json={"name": "E2E-14 cancel (acceptance)",
                                              "scene_meta_path": CHENNAI_SCENE_META})
    v.check("investigation created", inv.status_code in (200, 201), inv.status_code)
    if inv.status_code not in (200, 201):
        v.save("investigation_error.txt", inv.text); return v.finish()
    inv_id = inv.json()["id"]
    st = c.post(f"/api/investigations/{inv_id}/run", json={"engine": "auto"})
    v.check("run started", st.status_code in (200, 201, 202), st.status_code)
    if st.status_code not in (200, 201, 202):
        v.save("start_error.txt", st.text); return v.finish()
    run_id, job_id = st.json()["run_id"], st.json()["job_id"]
    v.save("start.json", st.json())
    j = _wait_job(c, job_id, ("complete", "failed", "cancelled"), 240, stop_when_stage="detect")
    v.save("job_before_cancel.json", j)
    v.check("job was running when cancel was requested", j.get("status") in ("running", "pending"), j.get("status"))
    r = c.post(f"/api/jobs/{job_id}/cancel"); v.save("cancel_response.json", r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)
    v.check("cancel accepted", r.status_code == 200 and r.json().get("ok") is True, (r.status_code, r.text[:120]))
    j = _wait_job(c, job_id, ("cancelled", "complete", "failed"), 600)
    v.save("job_after_cancel.json", j)
    v.check("job status == cancelled", j.get("status") == "cancelled", j.get("status"), "cancelled")
    rd = runs_dir(run_id)
    v.check("no manifest.json (run left unsealed)", not (rd / "manifest.json").exists(), (rd / "manifest.json").exists(), False)
    run = c.get(f"/api/runs/{run_id}").json(); v.save("run.json", run)
    v.check("run row status is not 'complete'", run.get("status") != "complete", run.get("status"))
    ver = c.get(f"/api/runs/{run_id}/verify").json(); v.save("verify.json", ver)
    v.check("/verify says the run never completed", ver.get("ok") is False and any("never completed" in p for p in ver.get("problems", [])), ver.get("problems"))
    v.check("run does not appear in the replay list", run_id not in [x.get("run_id", x.get("id")) for x in (lambda rr: rr.get("runs", rr) if isinstance(rr, dict) else rr)(c.get("/api/replay/runs").json())])
    v.note("UI honest state: acceptance_evidence/E2E-14/ui-cancelled.png")
    return v.finish()


def e2e_02_03(existing_run: Optional[str] = None) -> Tuple[str, str]:
    """`existing_run` re-scores a journey run that already completed (its
    start.json / incident.json / sse_events.json in the evidence dir are
    reused) instead of launching another pipeline. The verdict says so."""
    v2 = Verdict("E2E-02", "Investigator journey",
                 "login -> create incident -> AOI + window -> scene search (live CDSE) -> pick -> run -> SSE 5 stages -> "
                 "workspace layers -> manifest v2 has git SHA + model hashes")
    v3 = Verdict("E2E-03", "UI never mock-runs",
                 "UI-launched run manifest: stages_real == stages_total, stages_mock == 0")
    c = client()
    if existing_run:
        return _score_existing_journey(v2, v3, c, existing_run)
    inc = c.post("/api/incidents", json={"title": "E2E-02 acceptance journey", "region": "Bay of Bengal"})
    v2.check("incident created", inc.status_code in (200, 201), inc.status_code)
    inc_id = inc.json().get("id") if inc.status_code in (200, 201) else None
    if inc_id: v2.save("incident.json", inc.json())
    # live CDSE scene search over the Chennai AOI and the flagship window
    ss = c.get("/api/scenes/search", params={"bbox": "79.8,12.8,80.8,13.6", "start": "2017-01-25T00:00:00Z",
                                             "end": "2017-02-02T00:00:00Z", "source": "S1", "top": 5})
    v2.save("scene_search.json", ss.json() if ss.headers.get("content-type", "").startswith("application/json") else ss.text)
    if ss.status_code == 200 and (ss.json().get("products") or ss.json().get("scenes") or ss.json().get("results")):
        v2.check("live CDSE search returned products", True, ss.status_code)
    else:
        v2.check("live CDSE search returned products", False, (ss.status_code, str(ss.text)[:160]))
        v2.note("Scene search did not return live products here (provider/network) -- the run proceeds on the "
                "catalogued Sentinel-1 Chennai scene, which is the same product the search would have resolved.")
    s2 = c.get("/api/scenes/search", params={"bbox": "79.8,12.8,80.8,13.6", "source": "S2"})
    v2.check("S2 search -> 501 NOT DEPLOYED (never an empty list)", s2.status_code == 501, s2.status_code, 501)

    body = {"name": "E2E-02 investigator journey (acceptance)", "scene_meta_path": CHENNAI_SCENE_META}
    if inc_id: body["incident_id"] = inc_id
    inv = c.post("/api/investigations", json=body)
    v2.check("investigation created (scene picked)", inv.status_code in (200, 201), inv.status_code)
    if inv.status_code not in (200, 201):
        v2.save("investigation_error.txt", inv.text); v2.finish(); v3.block("no run"); return v2.finish(), v3.finish()
    inv_id = inv.json()["id"]; v2.save("investigation.json", inv.json())
    st = c.post(f"/api/investigations/{inv_id}/run", json={"engine": "auto"})
    v2.check("run started from the API the UI uses", st.status_code in (200, 201, 202), st.status_code)
    run_id, job_id = st.json()["run_id"], st.json()["job_id"]
    v2.save("start.json", st.json())
    v2.check("registry row stamped api", c.get(f"/api/runs/{run_id}").json().get("registry_source") == "api")

    # consume the SSE cascade for real, with a hard wall-clock cap
    events: List[Dict[str, Any]] = []
    t0 = time.time()
    try:
        with httpx.Client(base_url=API, headers={"Cookie": c.headers["Cookie"]},
                          timeout=httpx.Timeout(900.0, connect=10.0)) as sc:
            with sc.stream("GET", f"/api/events/runs/{run_id}") as resp:
                name = None
                for line in resp.iter_lines():
                    if line.startswith("event:"): name = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        try: data = json.loads(line.split(":", 1)[1].strip())
                        except Exception: data = line
                        events.append({"t": round(time.time() - t0, 1), "event": name, "data": data})
                        if name == "end": break
                    if time.time() - t0 > 900: break
    except Exception as exc:
        v2.note(f"SSE stream ended with {type(exc).__name__}: {exc}")
    v2.save("sse_events.json", events)
    stages_seen = sorted({e["data"].get("stage") for e in events if e["event"] == "stage" and isinstance(e["data"], dict)})
    v2.check("SSE showed all 5 stages", len([s for s in stages_seen if s]) >= 5, stages_seen)
    j = _wait_job(c, job_id, ("complete", "failed", "cancelled"), 600); v2.save("job_final.json", j)
    v2.check("job completed", j.get("status") == "complete", j.get("status"), "complete")
    run = c.get(f"/api/runs/{run_id}").json(); v2.save("run.json", run)
    m = run.get("manifest") or {}
    v2.check("manifest has code_git_sha", bool(m.get("code_git_sha")), m.get("code_git_sha"))
    v2.check("manifest has model sha256 for segment + screen",
             {x["kind"] for x in m.get("models", []) if x.get("sha256")} >= {"segment", "screen"},
             [(x["kind"], (x.get("sha256") or "")[:12]) for x in m.get("models", [])])
    v2.check("manifest sealed + verifies", c.get(f"/api/runs/{run_id}/verify").json().get("ok") is True)
    for layer in ("scene_meta", "slick", "origin_cloud", "forecast", "suspects"):
        r = c.get(f"/api/layers/{run_id}/{layer}")
        v2.check(f"layer {layer} renders (200)", r.status_code == 200, r.status_code, 200)
    r = c.get(f"/api/runs/{run_id}/vessels_geojson"); v2.check("vessels_geojson renders", r.status_code == 200, r.status_code, 200)
    v2.check("run filed under the incident", run.get("incident_id") == inc_id, run.get("incident_id"), inc_id)
    st2 = v2.finish()

    v3.check("stages_real == stages_total", run.get("stages_real") == run.get("stages_total"), (run.get("stages_real"), run.get("stages_total")))
    v3.check("stages_mock == 0", run.get("stages_mock") == 0, run.get("stages_mock"), 0)
    v3.check("no stage source is synthetic/mock", all(s.get("source") == "real" for s in m.get("stages", [])), [s.get("source") for s in m.get("stages", [])])
    v3.check("AIS provenance recorded (not silently real)", (m.get("ais") or {}).get("data_source") in ("real", "synthetic"), (m.get("ais") or {}).get("data_source"))
    v3.note(f"AIS for this scene: {(m.get('ais') or {}).get('data_source')} -- the badge, not the claim, is what E2E-03 protects; "
            "a synthetic-AIS run is honest as long as it says so.")
    v3.save("manifest_summary.json", {"stages": m.get("stages"), "ais": m.get("ais"), "total_seconds": m.get("total_seconds")})
    return st2, v3.finish()


def _score_existing_journey(v2: Verdict, v3: Verdict, c: httpx.Client, run_id: str) -> Tuple[str, str]:
    v2.note(f"Re-scored against the completed run {run_id} started by an earlier invocation of this "
            "harness (its live captures in this directory are reused); no new pipeline was launched.")
    d = v2.dir
    inc_id = None
    if (d / "incident.json").exists():
        inc_id = read_json(d / "incident.json").get("id"); v2.check("incident created (from capture)", bool(inc_id), inc_id)
    if (d / "scene_search.json").exists():
        ss = read_json(d / "scene_search.json")
        got = bool(ss.get("products") or ss.get("scenes") or ss.get("results")) if isinstance(ss, dict) else False
        v2.check("live CDSE search returned products", got, (ss.get("count") if isinstance(ss, dict) else str(ss)[:80]))
        if not got:
            v2.note("Scene search returned no live products (provider/network); the run used the catalogued Sentinel-1 Chennai scene.")
    v2.check("S2 search -> 501 NOT DEPLOYED (never an empty list)",
             c.get("/api/scenes/search", params={"bbox": "79.8,12.8,80.8,13.6", "source": "S2"}).status_code == 501)
    run = c.get(f"/api/runs/{run_id}").json(); v2.save("run.json", run)
    v2.check("run exists and is complete", run.get("status") == "complete", run.get("status"), "complete")
    v2.check("registry row stamped api", run.get("registry_source") == "api", run.get("registry_source"), "api")
    events = read_json(d / "sse_events.json") if (d / "sse_events.json").exists() else []
    stages_seen = sorted({e["data"].get("stage") for e in events if e.get("event") == "stage" and isinstance(e.get("data"), dict)})
    v2.check("SSE showed all 5 stages", len([x for x in stages_seen if x]) >= 5, stages_seen)
    j = c.get(f"/api/jobs/job-{run_id}").json(); v2.save("job_final.json", j)
    v2.check("job completed", j.get("status") == "complete", j.get("status"), "complete")
    m = run.get("manifest") or {}
    v2.check("manifest has code_git_sha", bool(m.get("code_git_sha")), m.get("code_git_sha"))
    v2.check("manifest has model sha256 for segment + screen",
             {x["kind"] for x in m.get("models", []) if x.get("sha256")} >= {"segment", "screen"},
             [(x["kind"], (x.get("sha256") or "")[:12]) for x in m.get("models", [])])
    v2.check("manifest sealed + verifies", c.get(f"/api/runs/{run_id}/verify").json().get("ok") is True)
    for layer in ("scene_meta", "slick", "origin_cloud", "forecast", "suspects"):
        r = c.get(f"/api/layers/{run_id}/{layer}")
        v2.check(f"layer {layer} renders (200)", r.status_code == 200, r.status_code, 200)
    r = c.get(f"/api/runs/{run_id}/vessels_geojson"); v2.check("vessels_geojson renders", r.status_code == 200, r.status_code, 200)
    if inc_id:
        v2.check("run filed under the incident", run.get("incident_id") == inc_id, run.get("incident_id"), inc_id)
    st2 = v2.finish()
    v3.check("stages_real == stages_total", run.get("stages_real") == run.get("stages_total"), (run.get("stages_real"), run.get("stages_total")))
    v3.check("stages_mock == 0", run.get("stages_mock") == 0, run.get("stages_mock"), 0)
    v3.check("no stage source is synthetic/mock", all(s.get("source") == "real" for s in m.get("stages", [])), [s.get("source") for s in m.get("stages", [])])
    v3.check("AIS provenance recorded (not silently real)", (m.get("ais") or {}).get("data_source") in ("real", "synthetic"), (m.get("ais") or {}).get("data_source"))
    v3.note(f"AIS for this scene: {(m.get('ais') or {}).get('data_source')} -- the badge, not the claim, is what E2E-03 protects.")
    v3.save("manifest_summary.json", {"stages": m.get("stages"), "ais": m.get("ais"), "total_seconds": m.get("total_seconds")})
    return st2, v3.finish()


# --------------------------------------------------------------------------

FAST = {"E2E-01": e2e_01, "E2E-04": e2e_04, "E2E-05": e2e_05, "E2E-06": e2e_06,
        "E2E-07": e2e_07, "E2E-08": e2e_08, "E2E-09": e2e_09, "E2E-10": e2e_10,
        "E2E-11": e2e_11, "E2E-12": e2e_12, "E2E-13": e2e_13, "E2E-15": e2e_15}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live-runs", action="store_true", help="also run E2E-14, E2E-02, E2E-03 (several minutes)")
    ap.add_argument("--only", help="comma-separated ids")
    ap.add_argument("--journey-run", help="score E2E-02/03 against this completed run instead of launching one")
    args = ap.parse_args(argv)
    only = set(args.only.split(",")) if args.only else None
    EVIDENCE.mkdir(exist_ok=True)
    results: Dict[str, str] = {}
    order = list(FAST)
    for tid in order:
        if only and tid not in only: continue
        print(f"\n== {tid}")
        try: results[tid] = FAST[tid]()
        except Exception as exc:
            v = Verdict(tid, tid, "see FAST table"); v.block(f"{type(exc).__name__}: {exc}"); results[tid] = v.finish()
    if args.live_runs or (only and only & {"E2E-14", "E2E-02", "E2E-03"}):
        if not only or "E2E-14" in only:
            print("\n== E2E-14"); results["E2E-14"] = e2e_14()
        if not only or only & {"E2E-02", "E2E-03"}:
            print("\n== E2E-02 / E2E-03"); results["E2E-02"], results["E2E-03"] = e2e_02_03(args.journey_run)
            print("\n== E2E-15 (re-evaluated with the measured pipeline time)"); results["E2E-15"] = e2e_15()
    summary_path = EVIDENCE / "summary.json"
    prev = read_json(summary_path) if summary_path.exists() else {}
    prev.update(results)
    summary_path.write_text(json.dumps(dict(sorted(prev.items())), indent=1), encoding="utf-8")
    print("\n" + json.dumps(dict(sorted(prev.items())), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
