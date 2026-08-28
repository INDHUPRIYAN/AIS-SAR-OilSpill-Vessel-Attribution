# DARTIS training-set sample

10 real images from the `train` split the screening detector is training on right now.

- `annotated/` — the same images with their labels drawn on (**look here**)
- `original/`  — untouched 640x640 patches, exactly as the model sees them
- `labels/`    — the YOLO label files, byte for byte

## The thing to notice

Oil patches (`ow`, `oc`) carry boxes. Look-alike patches (`nw`, `nc`) carry
**an empty label file** — 0 bytes. That is not missing data; it is the label.
They are background negatives: images the detector must look at and report
nothing on. Roughly 63% of the training set is exactly this, which is how the
model learns not to fire on calm water and internal waves.

| file | subset | meaning | boxes | label size |
|---|---|---|---|---|
| `ow-0500.jpg` | `ow` | oil / open water | 2 | 76 B |
| `oc-0187.jpg` | `oc` | oil / coast | 1 | 37 B |
| `nw-0982-06-000070.jpg` | `nw` | look-alike / water | **0 — background** | 0 B |
| `nc-0180-02-000020.jpg` | `nc` | look-alike / coast | **0 — background** | 0 B |
| `ow-0499.jpg` | `ow` | oil / open water | 1 | 37 B |
| `oc-0186.jpg` | `oc` | oil / coast | 1 | 37 B |
| `nw-0983-06-000071.jpg` | `nw` | look-alike / water | **0 — background** | 0 B |
| `nc-0181-02-000021.jpg` | `nc` | look-alike / coast | **0 — background** | 0 B |
| `ow-0501.jpg` | `ow` | oil / open water | 1 | 37 B |
| `oc-0188.jpg` | `oc` | oil / coast | 1 | 37 B |

## YOLO label format

`class cx cy w h` — one line per object, all values normalised to [0,1],
class `0` = `oil` (the only class).

```
oc-0186.txt:
  0 0.501563 0.511719 0.062500 0.082812
nc-0180-02-000020.txt:
  (empty file - no oil in this patch)
```