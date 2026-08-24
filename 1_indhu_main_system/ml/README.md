# Detection model pipeline

**Two models, two stages.** Detection is a screen-then-delineate cascade:

| Stage | Model | Dataset | Asks | Module |
|---|---|---|---|---|
| 1 · Screen | YOLO11n, 1 class | DARTIS (PANGAEA) | *Is this oil, or a look-alike?* | `ml.dartis` → `ml.train_yolo` |
| 2 · Delineate | U-Net / ResNet-34 | Trujillo (Zenodo) | *Exactly which pixels?* | `ml.download` → `ml.train_unet` |

They are trained **separately and never merged** — different radiometry, format
and label geometry. Stage 1 gates stage 2 at inference: a patch the screen calls
a look-alike is never handed to the segmenter. Both are optional at runtime; the
`threshold_fallback` path in `backend/services/detection/threshold.py` works with
neither.

Steps 1–11 of the handbook's training workflow, as runnable stages. Run every
stage from **`1_indhu_main_system/`** (that is where the `ml` package lives),
using the project venv at the repo root:

```
cd 1_indhu_main_system
../.venv/Scripts/python -m ml.<stage>      # Windows
```

Paths in the commands below are relative to `1_indhu_main_system/`. `data/`,
`docs/` and checkpoints always resolve to the **repo root** regardless of where
you invoke from — `ml.config` anchors them.

> `config/normalisation.yaml` is a **frozen contract shared by training and
> inference**. Its dB clip range is hashed into a fingerprint that is stamped
> into prepared tiles, every checkpoint and the exported ONNX. Training,
> evaluation and export all refuse to run across a mismatch — because a
> train/inference mismatch does not throw, it just quietly destroys real-scene
> performance.

## 0 · Environment

```bash
python -m venv .venv
.venv/Scripts/python -m pip install torch torchvision \
    --index-url https://download.pytorch.org/whl/cu121
.venv/Scripts/python -m pip install -r 1_indhu_main_system/requirements.txt
.venv/Scripts/python -c "import torch; print(torch.cuda.is_available())"   # must be True
```

A plain `pip install torch` gives the **CPU** wheel and training will not run.

## 1 · Download

```bash
../.venv/Scripts/python -m ml.download --dataset trujillo --part 3 --list   # inspect sizes first
../.venv/Scripts/python -m ml.download --dataset trujillo --part 3          # test harness FIRST
../.venv/Scripts/python -m ml.download --dataset dartis                     # screening stage
```

Part III first (**9.2 GB**): it becomes the untouched test split every reported
metric comes from. Parts I–II are **80.7 GB** combined — more than the handbook's
40–60 GB estimate — and are handled by extract-and-discard then tile-and-discard,
never held on disk in full. Downloads resume if interrupted.

## 2 · Extract

Trujillo ships as `.7z` archives, with **images and masks in separate archives**
(Part 1: a 37.9 GB image archive beside a 5.9 MB mask archive).

```bash
../.venv/Scripts/python -m ml.extract --part 3 --list      # inspect layout first
../.venv/Scripts/python -m ml.extract --part 3
../.venv/Scripts/python -m ml.extract --part 1 --discard-archive
```

Unpacks into sibling `images/` and `masks/` trees, which is what the pairing
logic expects. Masks are extracted first — they are tiny, so a surprising layout
surfaces in seconds instead of after 38 GB of imagery.

Peak disk is archive **plus** expansion (7z barely compresses float32 TIFFs), so
Part 1 needs roughly 90 GB free and Part 3 about 20 GB. `--discard-archive`
deletes each `.7z` once unpacked; use it on Parts 1–2.

## 3 · Audit

```bash
../.venv/Scripts/python -m ml.audit --dataset trujillo --part 3 --samples 20
```

Opens 20 random image/mask pairs, reports band statistics, dtypes, CRS and oil
coverage, writes `docs/data_card.md`, and renders a contact sheet to
`docs/audit/` with the mask overlaid on the image.

**Look at the contact sheet.** A misaligned mask produces a model that scores
well and is useless, and no metric will tell you. This step also confirms which
band holds backscatter — set `sar.primary_band` in `normalisation.yaml` to
match, and reconcile the dB range if the audit warns about it.

## 4 · Prepare tiles

```bash
../.venv/Scripts/python -m ml.prepare_trujillo --part 3                # test  (kept unfiltered)
../.venv/Scripts/python -m ml.prepare_trujillo --part 1 --discard      # train/val, frees source
../.venv/Scripts/python -m ml.prepare_trujillo --part 2 --discard
```

Cuts 2048px scenes into 256px uint8 tiles under `data/processed/trujillo/`.

Two things worth knowing:

- **Train/val is filtered** to tiles with ≥1% oil plus an equal number of
  matched hard negatives *from the same scene*, so the model learns oil
  texture rather than scene identity.
- **Test is NOT filtered.** Part III is 150 oil / 150 look-alike / 150 no-oil
  precisely so per-phenomenon false positives can be reported; dropping its
  no-oil tiles would inflate every number we quote. `--discard` is refused on
  Part III.

`--discard` deletes the source archive after tiling. Run one part, discard, then
download the next — peak disk stays in the low tens of GB.

## 5 · Train

```bash
../.venv/Scripts/python -m ml.train_unet --smoke                       # prove the loop runs
../.venv/Scripts/python -m ml.train_unet --epochs 40 --batch-size 12
../.venv/Scripts/python -m ml.train_unet --resume data/runs/training/unet-r34/last.pt
```

U-Net / ResNet-34 (ImageNet init), Dice+BCE, AMP fp16, cosine LR.
Batch 12 fits 6 GB VRAM at 256px; 16 is tight. Checkpoints **every epoch** to
`last.pt`, plus `best.pt` by val IoU; per-epoch metrics land in `history.jsonl`.

Train/val is split **by scene**, not by tile — tiles from one 2048px scene are
near-duplicates, and a tile-level split leaks val into train and reports an IoU
several points too high.

Keep the laptop plugged in; the 4050 throttles hard on battery.

## 6 · Evaluate

```bash
../.venv/Scripts/python -m ml.evaluate --checkpoint data/runs/training/unet-r34/best.pt --sweep
```

Binary IoU, precision, recall, F1 on Part III, plus per-tile-kind breakdown and
the share of no-oil tiles that produce a false detection. Writes
`data/runs/training/metrics.json`.

**Pixel accuracy is never reported** — sea-class dominance means an all-sea
prediction scores >99%.

## 7 · Export

```bash
../.venv/Scripts/python -m ml.export --checkpoint data/runs/training/unet-r34/best.pt
```

Writes `segment.onnx` (dynamic batch) next to the detection service, embeds the
normalisation constants in ONNX metadata, runs a PyTorch-vs-ONNXRuntime parity
check, and generates `model_card.md` from the evaluation metrics.

## Testing the pipeline before the data lands

```bash
../.venv/Scripts/python -m ml.synth --split trainval --scenes 24
../.venv/Scripts/python -m ml.synth --split test --scenes 12
../.venv/Scripts/python -m ml.train_unet --smoke
```

`ml.synth` writes a **synthetic** tile cache in the exact prepared-data format —
gamma speckle with darker elliptical slicks. It exercises dataset → training →
evaluation → export so that when the real tiles arrive the only unknown left is
the data. It is not training data and no metric from it means anything; the
cache is stamped `"dataset": "SYNTHETIC"` so it cannot be mistaken later.

Delete `data/processed/trujillo/` before preparing real tiles.

## Model 2 · DARTIS screening detector

```bash
../.venv/Scripts/python -m ml.dartis matrix       # annotation table (2.4 MB)
../.venv/Scripts/python -m ml.dartis images       # 3,655 JPEGs, ~570 MB, resumable
../.venv/Scripts/python -m ml.dartis prepare      # build the YOLO dataset
../.venv/Scripts/python -m ml.train_yolo --epochs 50
../.venv/Scripts/python -m ml.train_yolo --export data/runs/training/screen/weights/best.pt
```

PANGAEA's bulk `allfiles.zip` is **behind a login (HTTP 401)**, but the individual
files under `/dataset/980773/files/` are open, so `ml.dartis images` fetches them
in parallel. The published data matrix already carries every bounding box in
patch pixels, so the per-image XML is never parsed.

**One class, not two.** Oil patches (`ow`/`oc`) carry annotated boxes; look-alike
patches (`nw`/`nc`) carry none — their bbox columns are empty and their XML files
do not exist. So look-alikes are **background negatives**: 2,290 images the model
must look at and report nothing on. That is how a detector is taught not to fire
on calm water, and it is what the labels actually support. A second "lookalike"
box class would mean inventing annotations.

Because ~63% of the set is background, `mAP` alone is misleading. `ml.train_yolo`
also reports the **background false-positive rate** — the share of no-oil patches
where the model claims a slick. That is the number that answers "how do you know
it's oil?".

## Stage map

| Stage | Module | Output |
|---|---|---|
| DARTIS fetch | `ml.dartis matrix/images` | `data/raw/dartis/` |
| DARTIS prep | `ml.dartis prepare` | `data/processed/dartis/dartis.yaml` |
| Screen train | `ml.train_yolo` | `data/runs/training/screen/weights/best.pt` |
| Screen export | `ml.train_yolo --export` | `backend/services/detection/weights/screen.onnx` |
| 1 Download | `ml.download` | `data/raw/trujillo/partN/*.7z` |
| 2 Extract | `ml.extract` | `data/raw/trujillo/partN/{images,masks}/` |
| 3 Audit | `ml.audit` | `docs/data_card.md`, `docs/audit/*.png` |
| 4 Prepare | `ml.prepare_trujillo` | `data/processed/trujillo/{trainval,test}/` |
| 5 Train | `ml.train_unet` | `data/runs/training/unet-r34/{best,last}.pt` |
| 6 Evaluate | `ml.evaluate` | `data/runs/training/metrics.json` |
| 7 Export | `ml.export` | `backend/services/detection/weights/segment.onnx` + `model_card.md` |
| — | `ml.config` | constants every stage reads; nothing hardcodes a dB range |
| — | `ml.synth` | synthetic tiles for pipeline smoke tests |

## Serving

`backend/services/detection/service.py` is the `/detect` entry point. It runs
screen → delineate, falls back automatically, and always returns a contract-valid
`DetectResponse`:

```
screen.onnx (stage 1)  +  segment.onnx (stage 2)   -> engine="ml"
             missing weights / no GPU / bad ONNX
                          v
        threshold + morphology                     -> engine="threshold_fallback"
```

Still to build: per-phenomenon false-positive breakdown (the matrix labels
look-alikes only as water/coast, not by phenomenon — finer categories would need
the dataset README).
