# Carry-forward items

Findings raised during Phase 0 that belong to a later prompt. Recorded so they
are not lost between sessions; none block the prompt that found them.

| id | finding | raised in | owner |
|---|---|---|---|
| CF-1 | `weights/model_card.md:24` calls the eval split "untouched test split"; the split's own `data/processed/trujillo/test/index.json` says `poc_holdout: true` with a re-measure WARNING. Both e29 and e48 were evaluated on it. `/api/metrics` follows the split metadata and keeps the caveat in `notes[]`; the two records should be reconciled, or the split re-cut and re-measured. Out of P04's surface (drift/normalise). | P01 | **P17** (model registry) |
| CF-2 | Engine B's forcing block has `fallback: null` even when the grid came from a fallback provider. The provider string now tells the truth (read from the grid's global attrs), but the engine's own `fallback` field is unpopulated. Not invented here. | P04 | **P17** (provider honesty) |
| CF-3 | Chennai scene: screen returns 0 oil + 78 look-alike while the segmenter returns 18 slicks. A real disagreement between the two stages on real data, recorded as measured. Worth investigating; must not be tuned to agree. | P02 | post-SIH / P12b input |
| CF-4 | The Bali corpus scene now yields EMPTY_MASK where the sealed `inv-final-audit` run recorded confidence 0.9332. Pre-existing detection behaviour, unrelated to P04 (which touches no detection code). Worth a look when detection is next opened. | P04 | **P06+** |
