# Health and Recovery Runbook

## Health Semantics

| Check | Meaning | Dependency impact |
| --- | --- | --- |
| nginx `/healthz` | proxy process can serve a local response | none |
| nginx `/readyz` | FastAPI reports PostgreSQL and required Redis ready | removes instance from traffic on 503 |
| API container `/healthz` | Python process and routing loop respond | restart after repeated failure |
| public web `/` | Next.js responds successfully | public routes unavailable |
| console `/app/index.html` | static console artifact exists | product UI unavailable |
| worker Celery ping | the addressed worker consumes control messages | processing capacity unavailable |
| worker queue subscription | `celery -A backend.celery_app inspect active_queues` lists `processing` (the only declared queue; producers route `backend.jobs.process_meeting` there and worker commands pass `--queues processing`) | accepted jobs stay queued forever |
| PostgreSQL `pg_isready` | server accepts the application database connection | API not ready |
| Redis authenticated `PING` | broker accepts authenticated commands | API not ready unless tasks are eager; workers unavailable |
| MinIO live endpoint | object-store process is alive | uploads and processing unavailable |
| Neo4j `RETURN 1` | optional projection accepts Cypher | analytical projection degraded only |

Liveness must not fail because a downstream provider is slow. Readiness must fail when the
instance cannot safely serve transactional requests. Provider-specific processing status
belongs in internal diagnostics and job failure states, not proxy liveness.

## First Response

```bash
docker compose --file infra/compose/compose.onprem.yaml --profile onprem ps
docker compose --file infra/compose/compose.onprem.yaml --profile onprem logs --since 15m proxy api worker
curl --fail --silent --show-error http://127.0.0.1:8080/healthz
curl --silent --show-error --include http://127.0.0.1:8080/readyz
```

Capture the release digest, migration revision, first failure time, request IDs, affected
workspaces, queue depth, storage capacity, and recent infrastructure changes. Preserve logs
before restarting repeatedly. nginx emits structured JSON and forwards a bounded request ID;
use that ID to correlate API logs without logging tokens, cookies, or request bodies.

## nginx 502 or 504

1. Check `public-web`, `console-web`, and `api` health separately.
2. Confirm the active Compose profile created all services on the `edge` network.
3. Inspect nginx upstream and request timing fields.
4. For uploads, confirm both `/api/v1/uploads` and `/api/v1/uploads/` select the streaming
   300 MB proxy policy, then distinguish that proxy limit from the application media limit
   and upstream timeout. Do not raise limits before validating storage and decompression risk.
5. If only one application service is failing, remove it from traffic or restart that
   service; do not restart PostgreSQL or MinIO as a generic response.

## API Not Ready

1. Read the `/readyz` response to identify PostgreSQL or Redis.
2. Validate DNS, TLS, credentials, certificate trust, pool exhaustion, and connection limits.
3. Check the current Alembic revision before starting a newer API image.
4. If PostgreSQL is in recovery or storage is full, keep API unavailable until consistency
   and free space are confirmed.
5. If Redis is lost, expect queued ephemeral state to be lost. Reconcile durable jobs from
   PostgreSQL before re-enqueueing with their existing idempotency keys.

## Worker Backlog or Failure

1. Check Celery health, active tasks, queue age, and Redis memory pressure.
2. Confirm provider reachability and that the image includes the selected provider extra.
3. Drain a worker before replacement and preserve its two-minute stop grace period.
4. Never bulk replay tasks without reconciling durable processing attempts and quota
   reservations in PostgreSQL.
5. Scale by measured stage bottleneck. Keep workspace concurrency and provider limits in
   force when adding workers.

## Dispatch Outbox (Jobs Accepted but Not Published)

Since WP06-T02 an upload commits its job **and** a `job_dispatches` row in one
transaction; the broker message is sent afterwards and recorded on that row
(`PENDING → PUBLISHED`, or `FAILED` after `DISPATCH_MAX_ATTEMPTS` publish
failures with exponential back-off, or `CANCELLED` when the meeting was deleted
first). Delivery is at-least-once: a crash between publish and mark republishes
the same job, and the worker's claim makes the duplicate a no-op.

- Recovery is automatic while the `scheduler` service (Celery beat) runs: every
  `DISPATCH_SWEEP_INTERVAL_SECONDS` (default 30) it publishes due `PENDING` rows in
  batches of `DISPATCH_SWEEP_BATCH_SIZE`. Run one beat replica.
- Without beat, or to force a pass: `python -m backend.jobs.outbox` (one bounded
  sweep; exit code 1 when a row reached `FAILED`).
- `GET /meetings/{id}/job` exposes `dispatch.state`, attempts and `last_error`; a
  job stuck in `queued` with `dispatch.state = failed` means the broker rejected the
  publication repeatedly — fix the broker, then reset the row to `PENDING` with a
  due `next_attempt_at` (or re-run the sweep after raising `max_attempts`).
- The sweeper reads only `job_dispatches` (RLS service scope `dispatch`); it never
  sees meeting content. Processing still binds the job's own tenant from the
  persisted job row, never from the message.
- Since WP06-T04 the same row is also **re-armed** when a job is requeued: after
  a retryable processing failure (due at the job's `retry_after`) and after the
  recovery of an abandoned attempt (due immediately). `dispatch.generation`
  shows which lease generation the current intent belongs to. Retries no longer
  use broker-level task retry.

## Abandoned Running Jobs (Worker Killed or Partitioned)

Since WP06-T04 a running attempt holds an **execution lease** on its
`processing_jobs` row (`locked_by`, `lease_generation`, `lease_expires_at`,
`lease_renewed_at`, `attempt_id`) that the worker renews every
`JOB_LEASE_RENEW_INTERVAL_SECONDS` (default 30) for `JOB_LEASE_SECONDS`
(default 120), never past `JOB_ATTEMPT_TIMEOUT_SECONDS` after the claim. All
lease decisions use PostgreSQL time. A worker whose lease lapsed or was fenced
cannot renew, transition stages, persist results, complete the meeting, record a
failure or settle usage; its attempt ends as `superseded` and writes nothing.

- Recovery is automatic while the `scheduler` service (Celery beat) runs: every
  `JOB_RECOVERY_SWEEP_INTERVAL_SECONDS` (default 60) the lease sweep fences each
  RUNNING job whose lease expired (generation + 1), records the abandoned stage
  (`failed` / `lease_expired` on that attempt), and re-arms the dispatch intent so
  the job runs again (`stage = recovery_scheduled`, `failure_code = lease_expired`
  until the next attempt starts). Expect resumption within one lease plus one
  sweep interval of the last renewal (≈ 3 minutes with defaults).
- Without beat, or to force a pass: `python -m backend.jobs.recovery` (one bounded
  batch of `JOB_RECOVERY_BATCH_SIZE`; exit code 1 when a job was parked).
- An abandoned attempt consumes one of the job's `max_attempts` (3). When none
  remain the job is parked `dead_letter` with `failure_code = lease_expired`, the
  meeting shows `failed`, and both quota reservations are released — nothing is
  charged. Operator replay of parked jobs is WP06-T05 work; until then do not
  edit `processing_jobs` by hand to resurrect an attempt.
- `GET /meetings/{id}/job` exposes `lease.generation`, `lease.attempt_id`,
  `lease.expires_at`, `lease.renewed_at` and `retry_after`. A job `running` with
  `lease.expires_at` in the past and no `lease_expired` transition after two
  sweep intervals means beat is not running.
- The lease sweep reads only `processing_jobs` (RLS service scope `recovery`,
  read-only); every recovery write binds the job's own tenant from the persisted
  row. Healthy leases are never reclaimed; cancelled/deleted jobs are skipped.
- A provider call that was in flight when the lease lapsed is not interrupted by
  the fence; the next attempt may repeat that supplier call (bounded by
  `max_attempts`). Its late result is rejected at the transaction boundary and
  the customer's reservation is settled at most once.

## Object Storage Failure

1. Stop accepting uploads when MinIO or managed S3 is unavailable.
2. Check capacity, inode usage, KMS availability, bucket policy, and credential expiry.
3. Do not make the private bucket anonymous to recover service.
4. After recovery, reconcile quarantined uploads and durable jobs before retrying.
5. Treat a missing object referenced by PostgreSQL as a data-integrity incident and preserve
   evidence before repair.

## Compromise or Secret Exposure

1. Remove affected instances from traffic and preserve audit evidence.
2. Revoke the exposed credential at its source, then rotate dependent application secrets.
3. Rotate object-store application keys separately from MinIO root credentials.
4. JWT signing-secret rotation requires an explicit session invalidation strategy; changing
   it immediately invalidates outstanding tokens.
5. Scan Git history and released image layers, not only the current working tree.
6. Restore service only after access logs, audit events, object access, and tenant boundaries
   have been reviewed.

## Maintenance Checks

At every release, test Compose expansion, nginx syntax, migrations, image startup, and all
health checks. Weekly, review backup age, queue age, disk growth, restart counts, certificate
expiry, and secret expiry. Quarterly, run an isolated restore exercise and record achieved
RPO/RTO against policy.
