# Red -> green (Foreman), capdoc-t1
- Base 7112407: check.py exit 1; N items 0/15 MISSING; preservation ALL PASS (reviewer phase A, base-run.txt).
- Maker turn 1 (AppVision.md +388/-4): check.py exit 0; SUMMARY PASS=157 INFO=6 WARN=3 REVIEW=4 FAIL=0 MISSING=0;
  N items 15/15; preservation ALL PASS; A1 (only AppVision.md changed) PASS. Output: gates/check-turn-1.txt.
- Open for phase C: WARN E-N08-offlineb, E-N09-offline, E-N13-offline; REVIEW E-N11-claims/-claimw/-num, G5.
- Foreman ruling already recorded in SPEC: non-N11 items PLANNED (no acceptance criteria met).
- Maker turn 2 (+ addendum): check.py exit 0; PASS=157 FAIL=0 MISSING=0 (same WARN 3 / REVIEW 4 set as turn 1, all
  cleared by the reviewer in turn 1); only AppVision.md changed. Round-2 inputs: REVIEW-INPUT.diff (full, sha
  f2915672…), REVIEW-DELTA.diff (turn 1 -> 2, sha 113eaf00…).
