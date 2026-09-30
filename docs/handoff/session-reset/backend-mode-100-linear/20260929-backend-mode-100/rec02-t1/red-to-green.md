# Red -> green (Foreman), rec02-t1
- Oracle sha b558d2a5…; base 7112407 (lane 1): 7 failed / 11 passed (Foreman re-run = reviewer).
- Maker turn 1 patch (loadtest.py +65/-8, new test_voice_admission_postgres.py): 17 passed / 1 failed.
  Red: test_delivered_proof_caps_one_workspace_at_its_limit_across_planes -> "no simultaneous single-workspace burst
  reached the workspace-capacity cap". The maker's AC3 burst mixes the capped workspace (limit 15: 15 admitted,
  27 rejected) with another organization's workspace (42/42 admitted) in the SAME burst.
  Foreman reading: AC3 ("simultaneous admissions into one workspace with limit L ... admit exactly L") does not require
  a single-workspace burst; the mixed burst meets it and adds a cross-tenant control. The reviewer rules in phase C
  whether this is a patch gap or an oracle over-reach.
- ruff check/format clean on both changed files. Maker's own suite: 1 failed (known HOM-163 drill) / 1283 passed / 47 skipped.
