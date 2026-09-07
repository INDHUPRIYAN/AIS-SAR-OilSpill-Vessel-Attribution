# P12 - scene search over HTTP, and the EO refusal

Executed: 2026-09-07T04:46:21Z

## Sentinel-2 refuses rather than returning nothing
```
GET /api/scenes/search?bbox=-91.5,27.8,-89,29.8&source=S2   ->  501
{
    "status": "NOT_DEPLOYED",
    "detail": "Sentinel-2 / EO is not deployed. The adapter exists and is tested, but no optical data is wired into the pipeline and no accuracy has been measured for it, so this endpoint will not return EO results.",
    "adapter": "scene_service/satellite/s2_adapter.py"
}
```
An empty S2 result would read as "we looked and found none". The adapter is
real and tested (11 tests, one 809 MB acquisition on record) but no optical
data is wired into the pipeline and no accuracy has been measured for it, so
the honest answer is a refusal that names the adapter.

## Live S1 search (CDSE -> ASF -> LocalCache)
```
GET /api/scenes/search?bbox=-91.5,27.8,-89.0,29.8&start=2023-01-01&end=2023-01-31&top=10
provider : ASF   total: 10   elapsed: 4.184s
attempts :
    {'provider': 'ASF', 'ok': True, 'returned': 10}

  2023-01-27T00:02:31  GRD_HD VV+VH  S1A_IW_GRDH_1SDV_20230127T000231_20230127T000256_046962_05A1EB_FAD7
  2023-01-27T00:02:06  GRD_HD VV+VH  S1A_IW_GRDH_1SDV_20230127T000206_20230127T000231_046962_05A1EB_F0EC
  2023-01-27T00:01:41  GRD_HD VV+VH  S1A_IW_GRDH_1SDV_20230127T000141_20230127T000206_046962_05A1EB_C3D0
  2023-01-21T23:54:23  GRD_HD VV+VH  S1A_IW_GRDH_1SDV_20230121T235423_20230121T235448_046889_059F70_91D4
  2023-01-21T23:53:54  GRD_HD VV+VH  S1A_IW_GRDH_1SDV_20230121T235354_20230121T235423_046889_059F70_5AA2
  2023-01-20T00:10:33  GRD_HD VV+VH  S1A_IW_GRDH_1SDV_20230120T001033_20230120T001058_046860_059E70_A5F0
  2023-01-20T00:10:08  GRD_HD VV+VH  S1A_IW_GRDH_1SDV_20230120T001008_20230120T001033_046860_059E70_E7BB
  2023-01-15T00:02:31  GRD_HD VV+VH  S1A_IW_GRDH_1SDV_20230115T000231_20230115T000256_046787_059BFF_EC4F
  2023-01-15T00:02:06  GRD_HD VV+VH  S1A_IW_GRDH_1SDV_20230115T000206_20230115T000231_046787_059BFF_44F2
  2023-01-15T00:01:41  GRD_HD VV+VH  S1A_IW_GRDH_1SDV_20230115T000141_20230115T000206_046787_059BFF_0E0B
```

## Observed vs inferred

The chain tries CDSE, then ASF, then the local cache, and returns only the
member that answered -- an earlier failure is logged inside the chain and never
surfaces to the caller. Reporting a CDSE error this route never received would
be inventing evidence, so members ahead of the winner are listed as
`not_reached` with `inferred: true`, which is a deduction from the chain's
order and is labelled as one.

Note in the live result above: the search fell through to **ASF**, so CDSE did
not answer. That is visible rather than hidden, and it is exactly the kind of
thing a "provider: ASF" line alone would have concealed.

## A catalogue entry is not a file

`cached_path` is populated only for a product already on disk. Conflating a
search hit with a downloaded scene is how a UI ends up offering "run this" for
something nobody has fetched.

## Note for P12b

Every product returned above is acquired between **00:01 and 00:11 UTC**. D1
prefers acquisitions after ~12:00 UTC so a backward origin window stays inside
the same UTC day -- these are the opposite, which is precisely why D1 also says
to ingest the **preceding day** of AIS. That instruction is not belt-and-braces
here; it is load-bearing.

## Regression

```
829 passed, 4 skipped   (815 before P12; +14)
```

The first run of this suite produced **3 errors**, all from the P09 guard:

```
RuntimeError: refusing to seed a test account into the live database
```

`test_truth_sweep` was never isolated when `test_metrics_truth` and
`test_scene_catalog` were, and it had been passing only because an earlier
module happened to set `DATABASE_URL` first. Adding a new test file changed the
ordering and the guard caught it -- which is what a guard against a *class* of
defect is for. Fixed by pinning its own throwaway database; the real DATA_ROOT
stays, because the module reads the real weights YAML and run artefacts.
