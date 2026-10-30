# Internal transport policy (HOM-161)

Owner decision, 2026-10-10. Production (`ENVIRONMENT=production`) requires TLS on every internal hop
from the API, worker, scheduler and migration job to their backends. Development, test and staging
are not checked, as for the HOM-32 rules. `backend/config.py` enforces the policy at settings
validation, so a refused configuration stops the process before it opens any connection.

## Matrix

| Deployment | PostgreSQL | Redis | Neo4j (projection on) | Object storage |
| --- | --- | --- | --- | --- |
| Cloud (`DEPLOYMENT_MODE=cloud`) | TLS, no opt-out | TLS, no opt-out | TLS, no opt-out | TLS, no opt-out |
| On-premise, backend on another host | TLS, no opt-out | TLS, no opt-out | TLS, no opt-out | TLS, no opt-out |
| On-premise, backend on the same host | TLS, or the explicit opt-out | TLS, or the explicit opt-out | TLS, or the explicit opt-out | TLS, or the explicit opt-out |

What counts as TLS:

| Hop | Variable | Accepted in production |
| --- | --- | --- |
| PostgreSQL | `DATABASE_URL` | every `sslmode` query value is `require`, `verify-ca` or `verify-full` (lower case, at least one) |
| Redis | `REDIS_URL` | `rediss://` |
| Neo4j | `NEO4J_URI` (checked only with `ENABLE_NEO4J_PROJECTION=true`) | `bolt+s://` or `neo4j+s://` |
| Object storage | `STORAGE_ENDPOINT_URL` (checked only with `STORAGE_BACKEND=s3`) | an `https://` URL with a host, or empty (the provider's default endpoint) |

The schemes and modes that encrypt without verifying the server certificate are handled
differently per hop:

- Neo4j `bolt+ssc://` and `neo4j+ssc://` are refused.
- PostgreSQL `sslmode=require` and Redis `ssl_cert_reqs=none` are accepted, because the matrix asks
  for encryption on those hops.

Prefer `sslmode=verify-full` and `ssl_cert_reqs=required` wherever a CA is available. Celery
requires the `ssl_cert_reqs` query parameter on a `rediss://` URL.

## On-premise opt-outs

Each hop has its own explicit opt-out. All default to `false`:

| Hop | Opt-out | Plaintext form it allows |
| --- | --- | --- |
| PostgreSQL | `DATABASE_ALLOW_INSECURE_TRANSPORT=true` | a URL without an accepted `sslmode` |
| Redis | `REDIS_ALLOW_INSECURE_TRANSPORT=true` | `redis://` or `unix://<socket path>` |
| Neo4j | `NEO4J_ALLOW_INSECURE_TRANSPORT=true` | `bolt://` or `neo4j://` |
| Object storage | `STORAGE_ALLOW_INSECURE_ENDPOINT=true` (HOM-32) | `http://` |

An opt-out applies only when all of the following hold:

1. `DEPLOYMENT_MODE=onprem`. In cloud mode it has no effect, and the plaintext URL is refused with a
   message that says so.
2. Every host the URL names is on the same host. That means one of:
   - `localhost`, or a loopback address (`127.0.0.0/8`, `::1`);
   - a unix socket path (PostgreSQL `host=/…`, Redis `unix://`);
   - a single-label name starting with a letter, which means a Compose service on this host's
     internal network, such as `postgres`, `redis`, `minio` or `neo4j`.

   A dotted name, any other IP address, an all-digit or hexadecimal label (resolvers read those as
   IPv4 addresses), and a URL without a host are refused even with the opt-out. Query parameters
   that move the connection count as hosts too:
   - PostgreSQL: every `host`/`hostaddr` value. A `service` parameter is refused, because
     `pg_service.conf` would name the server.
   - Redis: `host`/`hostname`, because Celery's result backend lets query parameters override the
     URL's host.
3. The URL is a recognised plaintext form for that hop. Any other scheme is refused.

`DATABASE_URL` is parsed with SQLAlchemy's `make_url`, the parser the engine uses, so the rule and
the connection always read the same host and `sslmode`. A database name that is itself a
connection string (it contains `=` or `://`) is refused in production in every mode.

**Audit.** Every opt-out in use is logged once per process at startup, at WARNING level by the
`backend.config` logger, with hop and variable names only, never a URL or credential:

```text
TRANSPORT_TLS_OPT_OUT deployment_mode=onprem hops=object_storage(STORAGE_ALLOW_INSECURE_ENDPOINT),postgresql(DATABASE_ALLOW_INSECURE_TRANSPORT),redis(REDIS_ALLOW_INSECURE_TRANSPORT)
```

An opt-out that is set while the hop already uses TLS is not logged. Refusal messages name the
variable and the accepted forms, never the value.

**Limit of the same-host check.** A single-label name is trusted as a same-host Compose service.
Schedulers that resolve short names across machines (Kubernetes services, Docker Swarm overlay
networks, DNS search domains) are multi-host deployments, which must use TLS: never set an opt-out
there.

## Bundled Compose profiles

- `compose.onprem.yaml` sets `DATABASE_ALLOW_INSECURE_TRANSPORT`, `REDIS_ALLOW_INSECURE_TRANSPORT`
  and `STORAGE_ALLOW_INSECURE_ENDPOINT` to `true`. Its PostgreSQL, Redis and MinIO have no
  certificates yet. `DATABASE_URL` and `REDIS_URL` must name the `postgres` and `redis` services.
  It also sets `STORAGE_PRESIGNED_DELIVERY=false`, so recordings stream through the API and a
  browser never gets a presigned `http://minio:9000` URL (HOM-340).
- `compose.neo4j.yaml` sets `NEO4J_ALLOW_INSECURE_TRANSPORT=true` for `bolt://neo4j:7687`. With the
  cloud profile, production refuses that URL. A cloud deployment uses a TLS Neo4j endpoint instead.
- **Residual risk.** Until the bundled services serve TLS, their traffic is plaintext on the
  internal Compose networks, including recordings, transcripts and credentials. Keep those networks
  internal and the backend ports unpublished. Retiring the opt-outs needs a certificate
  provisioning decision (an installer-generated internal CA or operator-supplied certificates,
  rotation, and trust distribution to the API and workers). That decision is tracked in HOM-196.

Upgrade notes are in [deployment.md](../operations/deployment.md#upgrade-and-rollback).
