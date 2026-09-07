# Carry-forward items

Findings raised during Phase 0 that belong to a later prompt. Recorded so they
are not lost between sessions; none block the prompt that found them.

| id | finding | raised in | owner |
|---|---|---|---|
| CF-1 | `weights/model_card.md:24` calls the eval split "untouched test split"; the split's own `data/processed/trujillo/test/index.json` says `poc_holdout: true` with a re-measure WARNING. Both e29 and e48 were evaluated on it. `/api/metrics` follows the split metadata and keeps the caveat in `notes[]`; the two records should be reconciled, or the split re-cut and re-measured. Out of P04's surface (drift/normalise). | P01 | **P17** (model registry) |
| CF-2 | Engine B's forcing block has `fallback: null` even when the grid came from a fallback provider. The provider string now tells the truth (read from the grid's global attrs), but the engine's own `fallback` field is unpopulated. Not invented here. | P04 | **P17** (provider honesty) |
| CF-3 | Chennai scene: screen returns 0 oil + 78 look-alike while the segmenter returns 18 slicks. A real disagreement between the two stages on real data, recorded as measured. Worth investigating; must not be tuned to agree. | P02 | post-SIH / P12b input |
| CF-4 | ~~Bali scene yields EMPTY_MASK~~ **RETRACTED.** The empty masks came from my own invocation: `run.py --scene-meta X` without `--scene` falls back to the mock raster, so the pipeline ran real code on `contracts/mocks/scene_sigma0_db.tif`. The provenance chain caught it correctly (`data_source: synthetic` despite a real scene_meta). Re-run with `--scene` gives 5/5 real on the same scene. No detection regression exists. | P04 | closed in P05 |
| CF-5 | The CLI silently pairs a caller-supplied `--scene-meta` with the demo raster when `--scene` is omitted. `routes.py:171-176` already guards this on the API path ("Never pair a caller-supplied scene_meta with the demo raster"); the CLI does not. A run whose metadata says EMED while its pixels are the mock is a provenance trap, even though data_source labels it honestly. | P05 | **P13** (investigation v2 / CLI parity) |
