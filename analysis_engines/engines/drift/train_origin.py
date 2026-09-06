"""Train the origin-recovery correction (the ML component of the hindcast, v2).

Closed-loop ground truth
------------------------
For each scenario, on a REAL forcing field:

    1. seed a known release point O at time T0
    2. integrate FORWARD H hours with diffusion on   -> the cloud a satellite would see
    3. run the ordinary backward hindcast on that cloud -> estimated origin O'
    4. label = (O - O') in kilometres

The label is a genuine recovery error, not an invented trajectory: O is known because we
placed it. What the model can learn is the *systematic* part of that error - diffusion is
irreversible, the origin-window rule is a heuristic, and backward integration through a
time-varying field is not the exact inverse of the forward pass.

Honest limit: the label comes from a forward run using the SAME forcing, so this corrects
hindcast-process bias only, never error in the forcing itself.

Split
-----
By FORCING FIELD (scenario-level holdout), which is stricter than the geographic-block
split used by the failed v1: an entire sea is held out, so no tile, time or dynamical
regime from a test field appears in training.

Usage
-----
    python -m engines.drift.train_origin --scenarios 900 --epochs 600
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engines.drift.euler_fallback import BACKWARD, FORWARD, run_euler  # noqa: E402
from engines.drift.grids import load_metocean                          # noqa: E402
from engines.drift.ml_origin import (                                  # noqa: E402
    FEATURE_NAMES, N_FEATURES, build_features,
)

REPO = Path(__file__).resolve().parents[3]

# Entire fields held out from training. Chosen before any result was seen: three distinct
# regimes (Indonesian archipelago, US west coast upwelling, Nile delta).
HOLDOUT_FIELDS = ("S1A_IW_GRDH_BALI", "S1A_IW_GRDH_OREGON", "S1A_IW_GRDH_NILE")

PARTICLES = 300
DT = 600.0
DIFFUSION = 5.0


def discover_fields(include_holdout: bool = False) -> list[dict]:
    root = REPO / "data" / "metocean"
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        cur, wind = d / "currents.nc", d / "wind.nc"
        if not (cur.exists() and wind.exists()):
            continue
        if not include_holdout and d.name in HOLDOUT_FIELDS:
            continue
        out.append({"name": d.name, "currents": cur, "wind": wind})
    return out


def field_domain(spec) -> dict:
    import xarray as xr

    with xr.open_dataset(spec["currents"]) as ds:
        lat = np.asarray(ds["lat"].values, dtype=float)
        lon = np.asarray(ds["lon"].values, dtype=float)
        t = ds["time"].values
    mlat = 0.15 * (lat.max() - lat.min())
    mlon = 0.15 * (lon.max() - lon.min())
    t0 = float(np.datetime64(t[0], "s").astype("int64"))
    t1 = float(np.datetime64(t[-1], "s").astype("int64"))
    return {"bbox": (lon.min() + mlon, lat.min() + mlat, lon.max() - mlon, lat.max() - mlat),
            "t0": t0, "t1": t1}


def _mean_forcing(mo, t_s, lons, lats) -> tuple[tuple[float, float], tuple[float, float]]:
    """Mean current and wind actually sampled at the cloud."""
    cu = cv = wu = wv = 0.0
    if mo.current is not None:
        a, b = mo.current.sample(t_s, lons, lats)
        cu, cv = float(np.nanmean(a)), float(np.nanmean(b))
    if mo.wind is not None:
        a, b = mo.wind.sample(t_s, lons, lats)
        wu, wv = float(np.nanmean(a)), float(np.nanmean(b))
    return (cu, cv), (wu, wv)


def one_scenario(mo, dom, rng) -> tuple[np.ndarray, np.ndarray, dict] | None:
    """Forward from a known origin, hindcast back, return (features, label_km, info)."""
    lo0, la0, lo1, la1 = dom["bbox"]
    if not (lo1 > lo0 and la1 > la0):
        return None

    true_lon = float(rng.uniform(lo0, lo1))
    true_lat = float(rng.uniform(la0, la1))
    hours = float(rng.choice([12.0, 18.0, 24.0]))

    span = dom["t1"] - dom["t0"]
    if span < hours * 3600 + 3600:
        return None
    t_release = float(dom["t0"] + rng.uniform(0, span - hours * 3600))
    t_detect = t_release + hours * 3600

    # 1-2: forward from the known origin, with diffusion (a real slick spreads)
    seed_lon = true_lon + rng.normal(0, 0.004, PARTICLES)
    seed_lat = true_lat + rng.normal(0, 0.004, PARTICLES)
    fwd = run_euler(seed_lon, seed_lat, mo, t_release, hours=hours, dt_seconds=DT,
                    direction=FORWARD, diffusion_m2_s=DIFFUSION,
                    rng=np.random.default_rng(int(rng.integers(1 << 30))))
    det_lon, det_lat = fwd.lons[-1], fwd.lats[-1]
    if not (np.all(np.isfinite(det_lon)) and np.all(np.isfinite(det_lat))):
        return None

    # 3: the ordinary hindcast on the observed cloud
    back = run_euler(det_lon, det_lat, mo, t_detect, hours=hours, dt_seconds=DT,
                     direction=BACKWARD, diffusion_m2_s=DIFFUSION,
                     rng=np.random.default_rng(int(rng.integers(1 << 30))))
    org_lon, org_lat = back.lons[-1], back.lats[-1]
    if not (np.all(np.isfinite(org_lon)) and np.all(np.isfinite(org_lat))):
        return None

    est_lon, est_lat = float(np.mean(org_lon)), float(np.mean(org_lat))

    # 4: label = true - estimated, in km
    dx = (true_lon - est_lon) * 111.320 * np.cos(np.radians(true_lat))
    dy = (true_lat - est_lat) * 110.574
    if not np.isfinite(dx) or not np.isfinite(dy):
        return None

    cur_uv, wind_uv = _mean_forcing(mo, t_detect, det_lon, det_lat)
    feats = build_features(
        backtrack_hours=hours,
        detect_lons=det_lon, detect_lats=det_lat,
        origin_lons=org_lon, origin_lats=org_lat,
        mean_current_uv=cur_uv, mean_wind_uv=wind_uv,
    )
    if not np.all(np.isfinite(feats)):
        return None

    return feats[0], np.array([dx, dy]), {
        "true": [true_lon, true_lat], "est": [est_lon, est_lat],
        "hours": hours, "recovery_error_km": float(np.hypot(dx, dy)),
    }


def generate(n_scenarios: int, seed: int, fields) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    X, Y, F = [], [], []
    per = max(n_scenarios // max(len(fields), 1), 1)
    for spec in fields:
        try:
            dom = field_domain(spec)
            mo, _ = load_metocean(str(spec["currents"]), str(spec["wind"]))
        except Exception as exc:                       # noqa: BLE001
            print(f"  skip {spec['name']}: {exc}")
            continue
        kept = 0
        errs = []
        for _ in range(per):
            r = one_scenario(mo, dom, rng)
            if r is None:
                continue
            f, y, info = r
            X.append(f)
            Y.append(y)
            F.append(spec["name"])
            errs.append(info["recovery_error_km"])
            kept += 1
        if kept:
            print(f"  {spec['name']:52s} {kept:4d} scenarios  "
                  f"mean recovery error {np.mean(errs):6.3f} km")
    if not X:
        raise SystemExit("no scenarios generated")
    return np.array(X), np.array(Y), np.array(F)


def train_mlp(X, Y, hidden=32, epochs=600, lr=3e-3, seed=26143, l2=1e-4):
    """One hidden layer, weight-decayed. Small on purpose: a few hundred scenarios do not
    support a large model, and the v1 failure was an over-confident extrapolation."""
    rng = np.random.default_rng(seed)
    n, d = X.shape
    w1 = rng.normal(0, np.sqrt(2.0 / d), (d, hidden)); b1 = np.zeros(hidden)
    w2 = rng.normal(0, np.sqrt(2.0 / hidden), (hidden, 2)); b2 = np.zeros(2)
    ps = [w1, b1, w2, b2]
    ms = [np.zeros_like(p) for p in ps]
    vs = [np.zeros_like(p) for p in ps]
    b1a, b2a, eps = 0.9, 0.999, 1e-8
    batch = min(128, n)
    for ep in range(1, epochs + 1):
        idx = rng.permutation(n)
        tot = 0.0
        for s in range(0, n, batch):
            bi = idx[s:s + batch]
            x, y = X[bi], Y[bi]
            z1 = x @ w1 + b1; a1 = np.tanh(z1)
            out = a1 @ w2 + b2
            diff = out - y
            tot += float((diff ** 2).mean()) * len(bi)
            g = 2.0 * diff / len(bi)
            gw2 = a1.T @ g + l2 * w2; gb2 = g.sum(0)
            ga1 = g @ w2.T; gz1 = ga1 * (1 - a1 ** 2)
            gw1 = x.T @ gz1 + l2 * w1; gb1 = gz1.sum(0)
            for i, (p, gr) in enumerate(zip(ps, [gw1, gb1, gw2, gb2])):
                ms[i] = b1a * ms[i] + (1 - b1a) * gr
                vs[i] = b2a * vs[i] + (1 - b2a) * gr ** 2
                p -= lr * (ms[i] / (1 - b1a ** ep)) / (np.sqrt(vs[i] / (1 - b2a ** ep)) + eps)
        if ep % 150 == 0 or ep == 1:
            print(f"    epoch {ep:4d}  train mse {tot / n:.5f}")
    return ps


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenarios", type=int, default=900)
    ap.add_argument("--epochs", type=int, default=600)
    ap.add_argument("--seed", type=int, default=26143)
    ap.add_argument("--hidden", type=int, default=32)
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent / "weights" / "origin_correction.npz")
    args = ap.parse_args(argv)

    t_start = time.time()
    fields = discover_fields()
    print(f"closed-loop scenario generation over {len(fields)} real forcing fields")
    print(f"  held out entirely: {', '.join(HOLDOUT_FIELDS)}")
    X, Y, F = generate(args.scenarios, args.seed, fields)

    err = np.hypot(Y[:, 0], Y[:, 1])
    print(f"\n  scenarios {len(X)}  features {X.shape[1]}")
    print(f"  hindcast recovery error: mean {err.mean():.3f} km  median "
          f"{np.median(err):.3f} km  p95 {np.percentile(err, 95):.3f} km")
    print(f"  label bias (mean signed): dx {Y[:, 0].mean():+.3f} km  dy {Y[:, 1].mean():+.3f} km")

    # validation split: whole fields again, inside the training pool
    uniq = sorted(set(F.tolist()))
    rng = np.random.default_rng(args.seed)
    rng.shuffle(uniq)
    n_val = max(len(uniq) // 5, 1)
    val_fields = set(uniq[:n_val])
    va = np.array([f in val_fields for f in F])
    tr = ~va
    print(f"  train {tr.sum()} scenarios / {len(uniq) - n_val} fields | "
          f"val {va.sum()} scenarios / {n_val} fields ({', '.join(sorted(val_fields))})")

    x_mean, x_std = X[tr].mean(0), X[tr].std(0)
    x_std[x_std < 1e-9] = 1.0
    y_mean, y_std = Y[tr].mean(0), Y[tr].std(0)
    y_std[y_std < 1e-9] = 1.0

    Xn = (X - x_mean) / x_std
    Yn = (Y - y_mean) / y_std

    print("\n  training ...")
    w1, b1, w2, b2 = train_mlp(Xn[tr], Yn[tr], hidden=args.hidden,
                               epochs=args.epochs, seed=args.seed)

    def predict(xs):
        h = np.tanh(((xs - x_mean) / x_std) @ w1 + b1)
        return (h @ w2 + b2) * y_std + y_mean

    def report(mask, label):
        p = predict(X[mask])
        base = np.hypot(Y[mask, 0], Y[mask, 1])                 # physics-only error
        corr = np.hypot(Y[mask, 0] - p[:, 0], Y[mask, 1] - p[:, 1])
        imp = 100.0 * (1 - corr.mean() / base.mean()) if base.mean() else 0.0
        print(f"  {label:12s} physics {base.mean():6.3f} km -> ML {corr.mean():6.3f} km "
              f"({imp:+5.1f}%)  improved {100*np.mean(corr < base):.0f}% of scenarios")
        return {"n": int(mask.sum()),
                "physics_mean_km": float(base.mean()), "ml_mean_km": float(corr.mean()),
                "physics_median_km": float(np.median(base)), "ml_median_km": float(np.median(corr)),
                "physics_p95_km": float(np.percentile(base, 95)),
                "ml_p95_km": float(np.percentile(corr, 95)),
                "improvement_pct": float(imp),
                "scenarios_improved_pct": float(100 * np.mean(corr < base))}

    print()
    m_tr = report(tr, "train")
    m_va = report(va, "validation")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "model_version": f"origin-correction-mlp-{datetime.now(timezone.utc):%Y%m%d}",
        "trained_utc": datetime.now(timezone.utc).isoformat(),
        "task": "correct the systematic origin-recovery bias of the physics hindcast",
        "ground_truth": "closed loop: known release point -> forward drift (diffusion on) "
                        "-> ordinary hindcast -> label = true origin minus estimated origin",
        "ground_truth_limit": "labels come from a forward run on the SAME forcing, so this "
                              "corrects hindcast-process bias only, never forcing error",
        "architecture": f"MLP {N_FEATURES}-{args.hidden}-2, tanh, Adam, L2 1e-4",
        "features": list(FEATURE_NAMES),
        "particles_per_scenario": PARTICLES,
        "dt_seconds": DT, "diffusion_m2_s": DIFFUSION,
        "scenarios_total": int(len(X)),
        "split": "by forcing FIELD (whole seas held out), never by sample",
        "train_fields": sorted(set(F[tr].tolist())),
        "validation_fields": sorted(val_fields),
        "holdout_fields_never_seen": list(HOLDOUT_FIELDS),
        "seed": args.seed, "epochs": args.epochs,
        "forcing_data_source": "real (CMEMS GLORYS currents + ECMWF ERA5 wind)",
        "train_metrics": m_tr, "validation_metrics": m_va,
        "train_seconds": round(time.time() - t_start, 1),
    }
    np.savez(args.out, w1=w1, b1=b1, w2=w2, b2=b2,
             x_mean=x_mean, x_std=x_std, y_mean=y_mean, y_std=y_std,
             metadata_json=json.dumps(meta))
    sha = hashlib.sha256(args.out.read_bytes()).hexdigest()

    # re-embed the hash so the artefact carries its own identity
    meta["checkpoint_sha256"] = sha
    np.savez(args.out, w1=w1, b1=b1, w2=w2, b2=b2,
             x_mean=x_mean, x_std=x_std, y_mean=y_mean, y_std=y_std,
             metadata_json=json.dumps(meta))
    meta["checkpoint_sha256"] = hashlib.sha256(args.out.read_bytes()).hexdigest()
    (args.out.parent / "origin_correction_metadata.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")

    print(f"\n  checkpoint -> {args.out}")
    print(f"  sha256     -> {meta['checkpoint_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
