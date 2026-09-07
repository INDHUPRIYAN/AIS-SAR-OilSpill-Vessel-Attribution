# P17 — Provider honesty, data catalog, model registry

Four distinctions a dashboard tends to erase, restored.

## 1. REACHABLE is not WORKING

The probe reported `WORKING` for **any unauthenticated HTTP 200**. That proves
a host answered and nothing else — not that our credentials are accepted, not
that the dataset exists, not that a download would succeed.

This is not hypothetical. During the flagship run, ERA5 sat 33 minutes without
returning; a socket probe showed `cds.climate.copernicus.eu` timing out on both
address families, and a probe five minutes later connected in 0.2 s. The
credentials were never exercised. Under the old vocabulary a front-page ping
would have rendered that provider green.

```
WORKING       an authenticated / functional probe succeeded
REACHABLE     the host answered an unauthenticated request; this does not
              prove it will serve us data
DEGRADED      reachable and refusing us, or failing intermittently
UNCONFIGURED  credentials required and absent
FAILED        unreachable
NOT_DEPLOYED  the adapter exists but nothing consumes it
```

Three cheap **functional probes** were added — one round trip each, no
bandwidth — and each publishes what it proves:

| provider | probe | proves |
|---|---|---|
| MarineCadastre | `HEAD` a real daily archive | a real daily AIS archive is downloadable |
| CMEMS | STAC dataset document | the currents dataset the chain requests is published |
| CDSE | one-item Sentinel-1 catalogue query | the catalogue answers a real product query |

Where no cheap functional probe exists the honest answer is `REACHABLE`, and
`/api/catalog` reports `probe: functional | reachability | none` per provider.
**A status without its probe kind is unfalsifiable**, so the two travel
together. The vocabulary itself is returned by the API so the UI renders the
server's definition rather than inventing a second, contradictory one.

## 2. "No credentials needed" is not "credentials configured"

`has_credentials()` returned `True` for providers with no credential fields at
all — HYCOM, Open-Meteo, MarineCadastre, DMA — which rendered as a green
"configured" tick. A tick claims a key is present and correct. That is not a
claim anyone can make about a field that does not exist.

`credential_state()` is now tri-state, and `n_a` is grey rather than green:

```
configured  every field of an accepted credential set is present
missing     this provider needs credentials and some are absent
n_a         this provider takes no credentials — there is nothing to
            configure, so neither a tick nor a cross would be true
```

`has_credentials()` is kept for gating probes, with a docstring saying it is
wrong as a display value.

## 3. Not deployed is neither missing nor broken

`Sentinel2` and `AISStream` now appear in the registry with
`deployment: NOT_DEPLOYED` and a reason. Omitting them would suggest optical
and live AIS were never considered; showing them beside working providers would
suggest they are available.

They are **never probed**. A 200 would render as a healthy provider that feeds
nothing; a timeout as an outage of something never switched on.

**The AISStream key field is removed.** Nothing reads it, and offering the field
invited operators to configure a capability that does not exist. The credential
checker's message changed too — it said the key was *"optional — only the live
demo tab needs it"*, and there is no live demo tab. Live AIS is stream-only: it
cannot answer questions about a scene acquired in the past, which is every
question this system asks. If a key is set, the checker now says **nothing
reads it**.

## 4. A tile for a component we do not run is a claim

`/api/system/health` reports CPU, memory, disk, database, model files and a
provider summary — each measured on the host at request time. It lists under
`not_reported`:

```
queue depth      -- this system has no queue
message broker   -- there is none
cluster health   -- this runs as a single process
object storage   -- artefacts are on the local filesystem
```

When `psutil` is absent, `cpu_percent` is **absent**, not zero. A 0% reading
would be a measurement we did not make.

## Model registry

`GET /api/models` reports both deployed ONNX files with sha256, byte count and
config fingerprint, read from the **`model_version`** metadata key — the same
key `run._model_records` reads for the sealed manifest.

That key was a live defect for one iteration: my first version guessed
`checkpoint`/`name`, silently fell back to the filename, and the page reported
the segmenter as *"segment"* while every manifest named
`unet-r34-fullcorpus-e48`. Two answers to one question. A test now pins the
name, and another asserts **no model is ever named "YOLOv8"** — the deployed
screen is YOLO11n, and a wrong name in a registry is how a report cites a model
that was never run.

The **drift ML residual** is registered as:

```
status:  EXPERIMENTAL
applied: false
note:    "evaluated negative; disabled"
detail:  "...The hindcast is physics: no machine learning contributes to the
          origin estimate."
```

Omitting it would hide work that was done; listing it without the verdict would
imply it is in use.

## A seeding bug my own change exposed

Giving the NOT_DEPLOYED providers an empty `chain` crashed `init_db()` on
`spec["chain"][0]`. A provider that feeds nothing has no active chain member,
and defaulting it to itself would put a provider at the head of a chain that
does not exist. Fixed, and those rows now seed with `status: NOT_DEPLOYED`.

## Coverage metadata

Every provider carries `dataset`, `bbox`, `temporal`, `resolution` and a note,
so the catalogue can say *"this provider does not cover your AOI"* rather than
letting a search return nothing and leaving the operator to guess whether it
was an outage. Two notes state limitations found during the flagship run:

* **CMEMS** — "daily means: no tidal or sub-daily structure"
* **Open-Meteo** — "point API — the adapter samples bbox corners, so the field
  is spatially coarse"

## Frontend

One page, three tabs (`/catalog`): **Data catalog**, **Models**, **Health**.
`WORKING` renders green and `REACHABLE` amber — they used to render
identically. Status tooltips come from the API's published vocabulary. `n_a`
credentials render grey.

## Tests

18, in `main_system/tests/test_provider_honesty.py`, covering P17's three named
cases (keyless provider reads `n_a`; EO and live AIS render NOT DEPLOYED;
`/api/system/health` fields trace to measurements) plus: an unauthenticated 200
is REACHABLE not WORKING; a functional probe earns WORKING; a refused
functional probe is DEGRADED; the catalogue says how each status was measured;
no model is named YOLOv8; health invents no tile for a component we do not run;
the Keys page cannot offer an AISStream field.

Evidence: [`pytest_full.txt`](pytest_full.txt).
