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
