# P11 - cross-run vessel index and dossier

Executed: 2026-09-07T03:10:27Z

## The frozen contract was not touched

`vessels.parquet` is a 14-column contract and `validate_vessels_df` rejects
extras outright -- *"unexpected columns: [...] (extend the contract, don't
smuggle)"*. It is validated by the main system at ingest, by `contracts/tests`,
and by a teammate's own handover tests, so widening it changes a file five
modules depend on.

Identity is therefore carried **beside** the contract file, not inside it:

```
mc_ingest.col_map      already mapped VesselName -> vessel_name, IMO -> imo
                       + CallSign -> call_sign  (added)
        |
        v
vessel_identities(df)  lifts {mmsi: name/imo/call_sign} from the frame
        |                BEFORE to_contract() projects onto the frozen 14
        v
to_contract(df)        unchanged; still returns exactly 14 columns
```

Two tests hold that boundary from both sides: `VESSEL_COLUMNS` is still exactly
14 with no identity field in it, and the validator still raises on an extra
column. Contract fingerprint unchanged from the P00 baseline.

## Identity is never invented

The single worst thing this feature could do is give a name to a vessel the
system may go on to rank as a suspect.

* A field the archive left blank produces **no entry**, so "we do not know this
  vessel's name" and "this vessel has no name" stay different statements.
* Synthetic AIS has none of these columns, so it yields an empty identity table
  and the dossier says so in words rather than showing a blank that reads like
  an omission.
* Identity only ever fills a blank. A later synthetic scenario reusing an MMSI
  cannot overwrite what a real archive said, and a vessel seen in a real
  archive is not downgraded to synthetic when a scenario borrows its number.
* The archive repeats identity on every fix, so the modal value is taken -- one
  corrupt row cannot rename a vessel.

## Exclusions are recorded, not hidden

A vessel the gates filtered out gets an appearance row carrying the gate that
excluded it (available since P10 carried `filter_reason` through). A dossier
showing only the runs where a vessel scored well would be a prosecution file
rather than a record, so `ranked_in` and `filtered_in` are both published.

## Backfill over the existing corpus

```
runs indexed        : 105
appearances         : 3,000
vessels             :   902   (33 real, 869 synthetic)
with identity       :     0
```

Zero identities is the correct result, not a failure: no run in this corpus has
an identity sidecar yet, because it is written by the MarineCadastre ingest and
no archive has been re-ingested since. Nothing was fabricated to fill the
column. The flagship run in P14 will be the first to populate it.

Re-indexing replaces a run's rows rather than appending, so running the backfill
twice cannot double an appearance count -- asserted by a test.

## Routes

```
GET /api/vessels               search by name/IMO/call sign/MMSI, filter by source
GET /api/vessels/{mmsi}        identity + every appearance, ranked or excluded
GET /api/vessels/{mmsi}/tracks per-run references to the sealed artefact
```

Tracks are references, not copies: a track lives in its run's own
`vessels.parquet`, and a second copy could drift from the artefact it claims to
represent.

## A recurring bug worth naming

The backfill first crashed on `can't compare offset-naive and offset-aware
datetimes` -- parquet returns tz-aware timestamps, SQLite returns naive. This is
the third appearance of the same family this phase (it broke the P08 audit hash
chain too). `_naive_utc()` normalises both sides, and its docstring points at
the earlier instance so the next person recognises it on sight.

## Regression

```
815 passed, 4 skipped   (797 before P11; +18)
frontend: npm run build clean, 6 vitest passing
```
