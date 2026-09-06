"""Train the drift residual-correction model (Phase 8: the ML in the hindcast).

Ground truth
------------
There is no observed drifter data for these scenes, so the model is NOT trained against
an invented "true trajectory". It is trained against the one quantity we can generate
honestly: the difference between the operational integration and a much finer
integration of the SAME real forcing.

    reference : forward Euler, dt = 60 s, ten substeps
    operational: forward Euler, dt = 600 s, one step
    target     : reference_displacement - operational_displacement, in metres

Both arms are diffusion-free so the target is the deterministic truncation error, not
random walk. Training data is sampled from the REAL CMEMS + ERA5 fields already in the
repository (Chennai 2017 and Gulf of Mexico 2023), at random positions and times inside
each field's own coverage.

Split
-----
By GEOGRAPHIC BLOCK, not at random: random splitting would put near-identical
neighbouring samples in both train and test and inflate the score. Blocks are 0.25 deg
tiles; whole tiles go to train or test.

Usage
-----
    python -m engines.drift.train_residual --samples 60000 --epochs 300
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

from engines.drift.grids import load_metocean          # noqa: E402
from engines.drift.ml_residual import (                # noqa: E402
    FEATURE_NAMES, N_FEATURES, build_features,
)

REPO = Path(__file__).resolve().parents[3]

# Real forcing already in the repository. EVERY met-ocean cache with both a currents
# and a wind file is used, so the model sees many distinct dynamical regimes rather
# than two. Training on only two fields produced a model that improved in-distribution
# and was catastrophically worse on an unseen field (-631%); that result is recorded in
# docs/qa/evidence/ml_hindcast/ and is the reason this list is discovered, not hard-coded.
HOLDOUT_FIELDS = ("S1A_IW_GRDH_BALI", "S1A_IW_GRDH_OREGON", "S1A_IW_GRDH_NILE")


def discover_fields(include_holdout: bool = False):
    """Every metocean cache that has both currents and wind."""
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


def field_domain(spec):
    """Sampling box and time span taken from the field itself, with a margin inside
    the edges so finite differences never fall outside the grid."""
    import xarray as xr

    with xr.open_dataset(spec["currents"]) as ds:
        lat = np.asarray(ds["lat"].values, dtype=float)
        lon = np.asarray(ds["lon"].values, dtype=float)
        t = ds["time"].values
    mlat = 0.1 * (lat.max() - lat.min())
    mlon = 0.1 * (lon.max() - lon.min())
    t0 = float(np.datetime64(t[0], "s").astype("int64"))
    t1 = float(np.datetime64(t[-1], "s").astype("int64"))
    return {
        "bbox": (lon.min() + mlon, lat.min() + mlat, lon.max() - mlon, lat.max() - mlat),
        "t0": t0,
        "span_s": max(t1 - t0, 3600.0),
    }


DT_OP = 600.0          # operational timestep
SUBSTEPS = 10          # reference = 10 x 60 s


def _step(metocean, t_s, lon, lat, dt, direction):
    """One diffusion-free forward-Euler step, latitude-correct."""
    u, v = metocean.drift_velocity(t_s, lon, lat)
    m_lat = 110574.0
    m_lon = 111320.0 * np.cos(np.radians(lat))
    m_lon = np.where(np.abs(m_lon) < 1.0, 1.0, m_lon)
    return (lon + direction * np.asarray(u) * dt / m_lon,
            lat + direction * np.asarray(v) * dt / m_lat)


def generate(n_samples: int, seed: int = 26143, fields=None):
    """Sample (features, truncation-error) pairs from every available real field."""
    rng = np.random.default_rng(seed)
    X, Y, BLK = [], [], []
    fields = fields if fields is not None else discover_fields()
    if not fields:
        raise SystemExit("no forcing fields available to train on")
    per_field = max(n_samples // len(fields), 1)

    for spec in fields:
        try:
            dom = field_domain(spec)
            mo, _ = load_metocean(str(spec["currents"]), str(spec["wind"]))
        except Exception as exc:                    # noqa: BLE001 - skip a broken cache
            print(f"  skip {spec['name']}: {exc}")
            continue

        lo0, la0, lo1, la1 = dom["bbox"]
        if not (lo1 > lo0 and la1 > la0):
            print(f"  skip {spec['name']}: degenerate grid")
            continue

        lons = rng.uniform(lo0, lo1, per_field)
        lats = rng.uniform(la0, la1, per_field)
        ts = dom["t0"] + rng.uniform(0, dom["span_s"], per_field)
        dirs = rng.choice([-1, 1], per_field)
        kept = 0

        for d in (-1, 1):
            m = dirs == d
            if not m.any():
                continue
            lo, la, t = lons[m], lats[m], ts[m]

            olon, olat = _step(mo, t, lo, la, DT_OP, d)

            rlon, rlat = lo.copy(), la.copy()
            tt = t.copy()
            for _ in range(SUBSTEPS):
                rlon, rlat = _step(mo, tt, rlon, rlat, DT_OP / SUBSTEPS, d)
                tt = tt + d * DT_OP / SUBSTEPS

            m_lat = 110574.0
            m_lon = 111320.0 * np.cos(np.radians(la))
            dx = (rlon - olon) * m_lon
            dy = (rlat - olat) * m_lat

            feats = build_features(mo, t, lo, la, DT_OP, d)
            good = np.isfinite(feats).all(axis=1) & np.isfinite(dx) & np.isfinite(dy)
            if not good.any():
                continue
            X.append(feats[good])
            Y.append(np.stack([dx[good], dy[good]], axis=1))
            # Blocks are namespaced by field so a tile in one sea can never collide
            # with a tile in another.
            BLK.append(np.char.add(
                np.char.add(spec["name"] + "_", (lo[good] // 0.25).astype(int).astype(str)),
                np.char.add("_", (la[good] // 0.25).astype(int).astype(str))))
            kept += int(good.sum())
        print(f"  {spec['name']:52s} {kept:6d} samples")

    if not X:
        raise SystemExit("no usable samples generated")
    return np.concatenate(X), np.concatenate(Y), np.concatenate(BLK)


def train(X, Y, hidden=(64, 64), epochs=300, lr=1e-3, seed=26143, verbose=True):
    """Small MLP trained with Adam. NumPy only, so the artefact stays dependency-free."""
    rng = np.random.default_rng(seed)
    n, d = X.shape
    h1, h2 = hidden

    w1 = rng.normal(0, np.sqrt(2.0 / d), (d, h1)); b1 = np.zeros(h1)
    w2 = rng.normal(0, np.sqrt(2.0 / h1), (h1, h2)); b2 = np.zeros(h2)
    w3 = rng.normal(0, np.sqrt(2.0 / h2), (h2, 2)); b3 = np.zeros(2)

    params = [w1, b1, w2, b2, w3, b3]
    m = [np.zeros_like(p) for p in params]
    v = [np.zeros_like(p) for p in params]
    b1a, b2a, eps = 0.9, 0.999, 1e-8

    batch = 512
    for ep in range(1, epochs + 1):
        idx = rng.permutation(n)
        tot = 0.0
        for s in range(0, n, batch):
            bi = idx[s:s + batch]
            x, y = X[bi], Y[bi]

            z1 = x @ w1 + b1; a1 = np.tanh(z1)
            z2 = a1 @ w2 + b2; a2 = np.tanh(z2)
            out = a2 @ w3 + b3

            diff = out - y
            loss = float(np.mean(diff ** 2))
            tot += loss * len(bi)

            g = 2.0 * diff / len(bi)
            gw3 = a2.T @ g; gb3 = g.sum(0)
            ga2 = g @ w3.T; gz2 = ga2 * (1 - a2 ** 2)
            gw2 = a1.T @ gz2; gb2 = gz2.sum(0)
            ga1 = gz2 @ w2.T; gz1 = ga1 * (1 - a1 ** 2)
            gw1 = x.T @ gz1; gb1 = gz1.sum(0)

            grads = [gw1, gb1, gw2, gb2, gw3, gb3]
            for i, (p, gr) in enumerate(zip(params, grads)):
                m[i] = b1a * m[i] + (1 - b1a) * gr
                v[i] = b2a * v[i] + (1 - b2a) * gr ** 2
                mh = m[i] / (1 - b1a ** ep)
                vh = v[i] / (1 - b2a ** ep)
                p -= lr * mh / (np.sqrt(vh) + eps)

        if verbose and (ep % 50 == 0 or ep == 1):
            print(f"  epoch {ep:4d}  train mse {tot / n:.6f}")
    return params


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=int, default=60000)
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--seed", type=int, default=26143)
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent / "weights" / "drift_residual.npz")
    args = ap.parse_args(argv)

    t0 = time.time()
    print("generating training data from REAL forcing fields ...")
    fields = discover_fields()
    print(f"  {len(fields)} real forcing fields (holdout excluded: "
          f"{', '.join(HOLDOUT_FIELDS)})")
    X, Y, BLK = generate(args.samples, args.seed, fields)
    print(f"  samples {len(X)}  features {X.shape[1]}  target rms "
          f"{np.sqrt((Y ** 2).sum(1)).mean():.2f} m")

    # ---- geographic block split -----------------------------------------
    blocks = np.unique(BLK)
    rng = np.random.default_rng(args.seed)
    rng.shuffle(blocks)
    n_test = max(int(0.25 * len(blocks)), 1)
    test_blocks = set(blocks[:n_test].tolist())
    te = np.array([b in test_blocks for b in BLK])
    tr = ~te
    print(f"  blocks {len(blocks)}  train {tr.sum()}  test {te.sum()} "
          f"(split by geographic block, not at random)")

    x_mean = X[tr].mean(0); x_std = X[tr].std(0); x_std[x_std < 1e-12] = 1.0
    y_scale = np.abs(Y[tr]).mean(0); y_scale[y_scale < 1e-9] = 1.0

    Xn = (X - x_mean) / x_std
    Yn = Y / y_scale

    print("training ...")
    w1, b1, w2, b2, w3, b3 = train(Xn[tr], Yn[tr], epochs=args.epochs, seed=args.seed)

    def predict(xs):
        h = np.tanh((xs - x_mean) / x_std @ w1 + b1)
        h = np.tanh(h @ w2 + b2)
        return (h @ w3 + b3) * y_scale

    pred = predict(X[te])
    err_phys = np.sqrt((Y[te] ** 2).sum(1))              # physics-only residual left over
    err_ml = np.sqrt(((Y[te] - pred) ** 2).sum(1))       # residual after correction
    rmse_phys = float(np.sqrt((err_phys ** 2).mean()))
    rmse_ml = float(np.sqrt((err_ml ** 2).mean()))
    improvement = 100.0 * (1 - rmse_ml / rmse_phys) if rmse_phys > 0 else 0.0

    print()
    print("HELD-OUT BLOCK EVALUATION (per operational step)")
    print(f"  physics-only  step RMSE : {rmse_phys:.3f} m")
    print(f"  ML-corrected  step RMSE : {rmse_ml:.3f} m")
    print(f"  improvement             : {improvement:.1f} %")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "model_version": f"drift-residual-mlp-{datetime.now(timezone.utc):%Y%m%d}",
        "trained_utc": datetime.now(timezone.utc).isoformat(),
        "task": "learned correction of forward-Euler truncation error at the operational "
                "600 s timestep, referenced to a 60 s ten-substep integration of the same "
                "real forcing",
        "architecture": f"MLP {N_FEATURES}-64-64-2, tanh, Adam",
        "features": list(FEATURE_NAMES),
        "samples_total": int(len(X)),
        "train_samples": int(tr.sum()),
        "test_samples": int(te.sum()),
        "split": "by 0.25-degree geographic block (never at random)",
        "seed": args.seed,
        "epochs": args.epochs,
        "forcing_fields": [f["name"] for f in fields],
        "holdout_fields_never_trained_on": list(HOLDOUT_FIELDS),
        "forcing_data_source": "real (CMEMS GLORYS currents + ECMWF ERA5 wind)",
        "eval_step_rmse_physics_m": round(rmse_phys, 4),
        "eval_step_rmse_ml_m": round(rmse_ml, 4),
        "eval_improvement_pct": round(improvement, 2),
        "honesty_note": "This corrects the integrator's own discretisation error. It does "
                        "NOT claim to predict real oil drift better than physics; there is "
                        "no observed drifter ground truth for these scenes.",
        "train_seconds": round(time.time() - t0, 1),
    }
    np.savez(
        args.out, w1=w1, b1=b1, w2=w2, b2=b2, w3=w3, b3=b3,
        x_mean=x_mean, x_std=x_std, y_scale=y_scale,
        metadata_json=json.dumps(meta),
    )
    sha = hashlib.sha256(args.out.read_bytes()).hexdigest()
    meta["checkpoint_sha256"] = sha
    (args.out.parent / "drift_residual_metadata.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")
    print()
    print(f"  checkpoint -> {args.out}")
    print(f"  sha256     -> {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
