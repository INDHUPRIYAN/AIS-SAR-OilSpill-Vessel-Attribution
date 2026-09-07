# P05 - truth sweep: provider snapshot, lite perf, weights API, manifest v2

Executed: 2026-09-07T01:11:13Z

Verified on run `p05-verify`: held-out Trujillo scene Oil/00000 (EMED),
**5/5 stages real, 0 mock**, data_source `sensor`.

## (a) PC-11 - mock provider snapshot no longer copied
```
BEFORE  shutil.copy(contracts/mocks/provider_status.json, run_dir/)
        -> 19 of 95 sealed runs carried a byte-identical fictional board

AFTER   written from the run's own inputs, owner: "measured"
{
 "owner": "measured",
 "scene": {
  "provider": "LOCAL",
  "source": "cached",
  "scene_id": "S1A_IW_GRDH_EMED",
  "data_source": "sensor"
 },
 "currents": {
  "file": "currents.nc",
  "provider": "Copernicus Marine Service (CMEMS)",
  "dataset": "OceanTrace Normalized Surface Currents",
  "normalised": "Normalized at 2026-08-24T22:11:07.820997+00:00"
 },
 "wind": {
  "file": "wind.nc",
  "provider": "Open-Meteo API",
  "dataset": "OceanTrace Normalized 10m Atmospheric Winds (Open-Meteo Fallback)",
  "normalised": "Normalized at 2026-08-24T22:12:37.788384+00:00"
 },
 "ais": {
  "file": "vessels_generated.parquet",
  "data_source": "synthetic"
 }
}
```
Not a reachability probe: it records what the bytes were, which is the
question a sealed run has to answer years later.

## (b) X-05 - lite-origin O(n^2) removed
```
cause   others = [f for f in feats if f not in parts]
        -> ~56M whole-dict comparisons on a 7,500-particle cloud

BEFORE  full 0.159s / 1399106 B   lite 3.117s / 492579 B   (lite 19.6x SLOWER)
AFTER   full 0.137s / 1399106 B   lite 0.167s / 492579 B   (18.7x faster)
```
lite now costs ~30 ms over streaming the file, and returns a third of the bytes -
which is what it was for.

## (c) S-04/S-05 - weights validated and readable
```
GET /api/attribution/weights
{
    "profile": "default-v1",
    "profile_hash": "f7e12dd4a5154d05b8a1b1c945fa62093aa05b4cae1bf9cdc8bf12abff5255ba",
    "source": "analysis_engines/config/attribution_weights.yaml",
    "weights": {
        "proximity": 0.3,
        "temporal": 0.2,
        "trajectory": 0.2,
        "anomaly": 0.1,
        "ais_gap": 0.15,
        "prior": 0.05
    },
    "published_as": {
        "anomaly": "behaviour",
        "prior": "vessel_prior"
    },
    "sum": 1.0,
    "validated": true,
    "problems": [],
    "on_invalid": "Engine C renormalises and records a warning in the run; this endpoint reports the file as written."
}
```
Only the `weights:` block is summed - the same file also holds `gates:`,
`priors:` and `scoring:` thresholds, so summing the document would always fail.

`published_as` is stated because `suspects.json` renames two factors
(anomaly -> behaviour, prior -> vessel_prior); the UI should not have to guess.

Per D2 this is **read-only**. No editor, no versioned profiles: the same file is a
frozen CLI input, and editing it mid-flight would silently change what already
sealed runs mean.

## (d) Manifest v2 seeds
```
{
 "code_git_sha": "2a0555e42f703eeb999e92bda1fd4d07c1deea2d+dirty",
 "attribution_profile": {
  "profile": "default-v1",
  "profile_hash": "f7e12dd4a5154d05b8a1b1c945fa62093aa05b4cae1bf9cdc8bf12abff5255ba",
  "sum": 1.0,
  "validated": true
 },
 "models": [
  {
   "kind": "screen",
   "file": "screen.onnx",
   "sha256": "9a6ff8df0d2b4a8f8a287895c6780030c336c5c7f61f1d16d11cc89ceb9ac9fd",
   "bytes": 10423456,
   "name": "yolo11n-screen-dartis-2026-08-24",
   "config_fingerprint": "01e24b0fb0e8"
  },
  {
   "kind": "segment",
   "file": "segment.onnx",
   "sha256": "a4aac81f3ddd54ce05859c0fc4faada7b196288ed64a54293ab96488c5804a44",
   "bytes": 97719644,
   "name": "unet-r34-fullcorpus-e48",
   "config_fingerprint": "01e24b0fb0e8"
  }
 ]
}
```
Model identity is read from the ONNX `metadata_props` the exporter embedded, so
the manifest names the checkpoint that actually ran. Hashes match the audit:
screen `9a6ff8df...`, segment `a4aac81f...`. The `+dirty` suffix is deliberate -
a clean-looking sha that does not describe the code that ran would be worse.
