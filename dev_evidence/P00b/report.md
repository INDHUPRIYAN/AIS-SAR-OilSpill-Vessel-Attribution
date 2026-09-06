# P00b — Working-tree hygiene (D4, revised order)

Executed: 2026-09-06T16:51:03Z

## Commit chain
```
9874ec3 [PROMPT-00b] checkpoint: pre-production working tree
516400f [PROMPT-00b] ignore generated artefacts; keep golden fixtures tracked
eb32646 [PROMPT-00b] track untracked source modules
b2a8fd5 Renames and download status updates
```

## Tag / HEAD relationship
```
tag pre-production-2026-09-06 -> eb32646  [PROMPT-00b] track untracked source modules
HEAD                          -> 9874ec3  [PROMPT-00b] checkpoint: pre-production working tree
commits after tag             -> 2
tag is ancestor of HEAD       -> YES
```

## Files per commit
```
eb32646  [PROMPT-00b] track untracked source modules                56 files
516400f  [PROMPT-00b] ignore generated artefacts; keep golden fixtures tracked 1 files
9874ec3  [PROMPT-00b] checkpoint: pre-production working tree       168 files
```

## git status after P00b
```
?? Old-files-and-some-audit-report/
?? Road-to-Production/
?? dev_evidence/
?? docs/LIMITATIONS.md
?? docs/OilGuard_Audit_Prompt.md
?? docs/audit/trujillo_part1_pairs.png
?? docs/audit/trujillo_part2_pairs.png
?? docs/eval/
?? docs/qa/
?? docs/resolution_and_sensor_choice.md
?? docs/technical_note_01_drift_physics.md
?? docs/technical_note_02_attribution_scoring.md
?? docs/video/
TOTAL ENTRIES: 13   (was 232 at P00)
```

## Generated artefacts — verified EXCLUDED
```
.venv-drift                                                IGNORED
audit_evidence                                             IGNORED
audit_evidence.zip                                         IGNORED
uat_evidence                                               IGNORED
main_system/frontend/test-results                          NOT ignored
analysis_engines/engines/drift/weights                     IGNORED
main_system/backend/services/detection/weights/previous_poc IGNORED
```

## Golden fixtures — verified STILL TRACKED (D4 rule overridden, see conflict)
```
ais_service/test_output/ tracked files : 101
read by production pipeline           : run.py:684 vessels.parquet
benchmark truth.json fixtures         : 50
```

## Frozen surfaces — re-verified against P00 fingerprints
```
OK    7560abe18b0bc9e7  contracts/schemas/tabular.py
OK    2099a009a84a5222  contracts/schemas/geo.py
OK    08b8ddd808fb18af  contracts/schemas/scene.py
OK    95570a4f51dd43a2  contracts/schemas/common.py
OK    11e6f00a9b8bbfec  contracts/schemas/__init__.py
OK    78ce232050e72835  pyproject.toml
OK    02ac4f8c7101396f  pytest.ini

VESSEL_COLUMNS count : 14 (must be 14)
stage names          : ['detect', 'characterise', 'drift_hindcast', 'drift_forecast', 'attribution']

ALL FROZEN SURFACES UNCHANGED
```

## CORRECTION to the check above

`main_system/frontend/test-results` reported "NOT ignored" only because the probe omitted the
trailing slash. Verified correctly:
```
$ git check-ignore -v main_system/frontend/test-results/.../error-context.md
.gitignore:88:main_system/frontend/test-results/   ...error-context.md
exit=0  -> IGNORED
```
Note: `test-results/.last-run.json` remains **tracked** — .gitignore never untracks an
already-tracked file. Harmless; flagged for the record.

## CONFLICT C-P00b-2 — D4's `*/test_output/` rule would have broken the benchmark

D4 said test artefacts under `*/test_output/` should be gitignored and "stay untracked".
Repository reality:
```
  run.py:684  reads REPO_ROOT/ais_service/test_output/vessels.parquet  <- PRODUCTION INPUT
  ais_service/test_output/benchmark/scenario_*/truth.json  x50          <- GOLDEN TRUTH DATA
  101 files already TRACKED
```
Applying the rule literally would have required `git rm --cached` on 101 files, untracking the
attribution benchmark's ground truth (basis of the 86% top-1 / 100% top-3 claim) and a live
pipeline input. **Rule not applied.** These files stay tracked and were committed in 9874ec3.

## Regression suite after P00b
```
654 passed, 3 skipped, 25 warnings in 81.64s (0:01:21)
P00 baseline: 654 passed, 3 skipped
```
