# wp18t03-t1-impl — handoff turn 1 (LANE 3)  [IN PROGRESS]

Status: working. SPEC read (incl. Foreman rulings, A1-A2). Worktree clean at 7112407.
Plan: tests first in backend/tests/test_plan_catalog.py (see red), then models, migration, service, rls literals, docs.

## Progress log (02:40)
- Tests written first: backend/tests/test_plan_catalog.py (26 tests). Red at base: collection ImportError (PlanVersion missing);
  behavioural red at base via scratch probe (restart and sign-up both reset professional/business edits).
- Implemented: saas_models (Plan.current_version, PlanVersion + guard, Subscription.plan_version + fk), rls.py two literals,
  migration c5e7a9b1d3f6_versioned_plans.py, tenancy_service (insert-only seed, drift report, config version, publish, pinned grant).
- New tests: 26 passed on lane-3 PostgreSQL. Next: focused command, alembic heads/check, ruff, deployment.md, full suite.
