# Baseline — 682ffee (origin/main 2e834ed + skill-only commit)
Environment: local PostgreSQL 16.13 (CI: 17), role homayar_test SUPERUSER (matches CI postgres image), Redis 7 local, ffmpeg installed, HOMAYAR_REQUIRE_POSTGRES_TESTS=1, HOMAYAR_REQUIRE_MEDIA_TOOLS=1, CI env values from .github/workflows/ci.yml.
- infra/ci/check-migrations.sh backend: exit 0
- pytest backend/tests: 1 failed, 1048 passed, 46 skipped (583 s)
  - FAILED backend/tests/test_truthful_outputs.py::test_no_random_numbers_outside_the_allowlist  -> offender voice/admission.py (import random; jitter at line 165). Introduced by c4e34d0 (PR #10).
  - Skips: 19 live-S3 opt-in (BLOCKED_EXTERNAL), 3 Padyar gateway contract missing, 1 transport drill needs docker, 1 SQLite-only restore drill, others see pytest.log
Run 1 (invalid, env): migrations failed CREATE ROLE privilege; 303 errors — environment defect, superseded by run 2.
