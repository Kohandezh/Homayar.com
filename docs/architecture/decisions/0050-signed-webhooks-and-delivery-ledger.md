# ADR 0050: Signed outbound webhooks and the delivery ledger

Date: 2026-10-30 · Status: Proposed (HOM-89, WP16-T05) · Builds on: ADR 0007, ADR 0013,
ADR 0023, ADR 0031

Amended by HOM-357 (2026-11-14).

All code paths are under `Homayar/backend/` unless they start with `Homayar/sdk/`.

## Context

Customers want Homayar to call their systems (a CRM, a ticket tool) when something
happens in a workspace. Feature F69 asks for three things:

- a receiver can check that a request really comes from Homayar and was not changed;
- an old or replayed request is rejected or dropped as a duplicate;
- a failed delivery can be sent again later without doing the receiver's work twice.

The tables `webhook_endpoints` and `webhook_deliveries` existed but nothing used them.
The legacy `routers/webhook.py` (an inbound VoIP hook) is gone and stays gone.

## Decision

### D1. One signature over the id, the time and the raw body

Every request is `POST <endpoint url>` with these headers:

```text
Content-Type: application/json
User-Agent: Homayar-Webhooks/1
Homayar-Webhook-Id: <event_id>
Homayar-Webhook-Timestamp: <t>
Homayar-Webhook-Signature: v1=<key_id>:<hex>[,v1=<key_id>:<hex>...]
```

`hex = HMAC-SHA256(key = secret as UTF-8, msg = "<event_id>.<t>." + raw body)`, 64
lowercase hex chars. `t` is unix seconds at this attempt.

The event id and the timestamp are inside the signed bytes. So a request cannot keep
its signature with a new timestamp (to get past the receiver's time check) or a new id
(to get past its duplicate check). The body is signed as raw bytes, so the receiver
checks exactly what it received, before any JSON parsing.

The `v1=` prefix names the scheme. A later scheme adds `v2=` entries next to `v1=`, and
receivers ignore versions they do not know.

### D2. Secrets, key ids and rotation

- A signing secret is `whsec_` + base64url of 32 random bytes (49 chars). The HMAC key is
  the whole string.
- A key id is `whk_` + 24 lowercase hex chars. It is public and travels in every request,
  so the receiver knows which secret to use.
- Rotation (`POST /api/v1/webhooks/endpoints/{id}/signing-keys`) makes a new active key at
  once. The previous key keeps signing until `now + overlap_seconds` (default 24 hours,
  0 to 7 days). While both are valid, each request carries one entry per key, active key
  first. A receiver that still has only the old secret keeps working; one that already
  has the new secret works too. With `overlap_seconds: 0` the old key stops at once (for a
  leaked secret).
- An endpoint has at most 5 unexpired keys, counted as they would be after the rotation
  (`409 webhook_signing_key_limit` otherwise, and nothing changes). A rotation with
  `overlap_seconds: 0` expires the old active key at once, so it is never refused: a leaked
  active key can always be rotated away.
- Revoke (`POST /api/v1/webhooks/endpoints/{endpoint_id}/signing-keys/{key_id}/revoke`,
  `webhook:manage` like every change here) ends a retiring key early: its `expires_at`
  becomes now, so a leaked retiring key does not keep signing until its overlap ends. The
  active key is refused (`409 webhook_signing_key_active`; rotate with `overlap_seconds: 0`
  instead), an already expired key too (`409 webhook_signing_key_expired`), and an unknown
  key or a key of another endpoint is `404 webhook_signing_key_not_found`. Other keys of
  the endpoint keep their `expires_at`, and the revoked key no longer counts as unexpired,
  so a rotation refused at the 5-key cap works again after a revoke. The audit row is
  `webhook.signing_key_revoked` with `key_id`, `scheduled_expires_at` and `revoked_at`.
  The worker reads the key rows `FOR SHARE`, so a revoke cannot commit while a claim
  reads the keys. That is not an exact revoke boundary: a delivery whose task started
  before the revoke time may still sign with the revoked key, because the filter is
  `expires_at > now` with one `now` per task and the claim commits before the send: a
  window of milliseconds, wider under DB pool wait or clock skew between hosts.
- The secret is in the create and rotate answers only. No other answer, log line, audit
  row or error message carries it.

### D3. Secrets at rest are encrypted, not hashed

An API key is stored as a hash, because the server only compares. A webhook secret
cannot be: the worker needs the secret itself to compute the HMAC. So it is stored
encrypted with AES-256-GCM under an application keyring, `WEBHOOK_SECRET_KEYS`, in the
same `key_id:base64(32 bytes)` format as `PII_MAPPING_KEYS` (ADR 0023). The keyring comes
from the secret manager, never from the database. Each row keeps the id of the keyring
key that encrypted it, so the keyring can rotate without re-encrypting old rows at once.

The associated data is `homayar-webhook-secret/1|<workspace_id>|<endpoint_id>|<key_id>`.
A ciphertext copied to another endpoint or workspace does not decrypt.

Without a keyring, endpoints cannot be created or rotated (503
`webhook_signing_unavailable`), and the worker moves queued deliveries to the dead
letter with `signing_unavailable` (D9) instead of sending them unsigned.

### D4. Frozen payload bytes and a ledger

`enqueue_event` serializes the event once:

```json
{"created_at": "2026-10-30T12:00:00Z", "data": {}, "id": "<event_id>", "type": "<event_type>", "workspace_id": "<id>"}
```

(compact, sorted keys, UTF-8). It stores these bytes on the delivery row. Every attempt,
and every later redelivery, sends the same bytes with the same event id. A receiver never
sees two bodies for one id, and a later change to the event data cannot change what was
already sent.

One event to several endpoints gets the same event id on each delivery. That id is the
receiver's duplicate key. An event is never queued twice for one endpoint (unique
`(endpoint_id, event_id)`).

`enqueue_event` runs in the caller's transaction (the outbox rule of ADR 0007): the
delivery rows commit together with the business change, or not at all. The route
publishes a delivery task after the commit as a speed-up. The sweeper finds anything
that was not published.

Each HTTP request writes one append-only row in `webhook_delivery_attempts`: time,
duration, status code, a short error code and the key ids that signed it. A response body
is never stored or logged.

### D5. At-least-once delivery

The worker claims a due delivery with `FOR UPDATE SKIP LOCKED`, marks it `sending` with a
lease, commits, sends with no transaction open, then records the result. A worker that
stops after sending and before recording leaves the lease to expire, and the next worker
sends again. So a receiver can get one event twice. This is the safe direction: losing an
event is worse, and the receiver's duplicate check (D6) makes a second copy harmless.

Redirects are not followed (a 3xx is a failure), so an endpoint cannot send the signed
request on to a host that the URL policy never checked. The timeout is
`WEBHOOK_DELIVERY_TIMEOUT_SECONDS` (default 10) for the whole request, from the start of
the send: an httpx timeout limits each read on its own, so a receiver that sends one byte
at a time is cut at the deadline (its socket is shut down) and recorded as `timeout`.

The sweeper discovers due rows of every workspace under the read-only `webhook_delivery`
service scope. Every write binds the row's own workspace (row-level security, as for
`entitlements` and `usage_reconciliation`). The scope is never held while writing: the
claim and the record each open their own session bound to the workspace only.

With `TASKS_EAGER` (development, tests) neither a request nor a sweep sends anything
itself: a request must never wait on a receiver, and a test must never reach the
network. Due rows wait for `python -m backend.jobs.webhook_delivery` or a worker. A row
written before this ticket has no frozen payload; it moves to the dead letter with
`payload_missing` and is never sent.

HOM-89 stopped at one attempt: a failed delivery stayed `failed`. HOM-90 adds the retry
schedule and the dead letter on the same rows (D9).

### D6. Guidance for receivers

The Python SDK has `verify_webhook`, `WebhookVerificationError` and an in-memory
`WebhookDeduplicator` (`Homayar/sdk/python/homayar_client/webhooks.py`, standard library
only), and an example receiver (`Homayar/sdk/python/examples/verify_webhook.py`). The
verifier checks in this order and names the first failure: `missing_header`,
`malformed_header`, `timestamp_out_of_tolerance`, `unknown_key`, `invalid_signature`,
`invalid_payload`.

1. Verify over the raw body bytes before parsing; never re-serialize first.
2. Reject a timestamp more than 300 s from your clock. Keep the clock synced (NTP).
3. Accept when any entry with a known key id verifies. During rotation keep the old secret
   until its `expires_at`.
4. Deduplicate on `Homayar-Webhook-Id`. Store handled ids under a unique key for at least
   7 days; do the side effect and the id insert in one transaction; on a duplicate return
   2xx and do nothing. A replay inside the tolerance window passes the signature check and
   is stopped here; a replay outside it is stopped by the timestamp check.
5. Answer 2xx fast and work later. Non-2xx, a redirect or no answer within 10 s is a
   failure.
6. Events may arrive more than once and out of order. A retry (D9) and a redelivery send
   the same id and the same body bytes again, with a new timestamp and signature.

### D7. Who may manage webhooks, and where they may point

- `webhook:manage` is granted to OWNER and ADMIN only, and is never an API key scope. A
  key that could add an endpoint could copy every future event out of the workspace.
- Every change is audited (`webhook.endpoint_created`, `webhook.endpoint_updated`,
  `webhook.signing_key_rotated`, `webhook.signing_key_revoked`, `webhook.test_queued`,
  `webhook.delivery_redelivered`, `webhook.dead_letters_redelivered`) with ids, key ids,
  event types, states, counts, `is_active`, and only the scheme and host of the URL.
- The URL policy runs at create, at update and again before every send. Refused: more than
  1000 chars, not http(s), no host, a user name or password in the URL, cloud metadata
  names and addresses, link-local, unspecified and multicast addresses, and host spellings
  that some resolvers read as an address (`2852039166`, `0xA9FEA9FE`). In production also
  plain `http` and loopback. In a production cloud deployment (`DEPLOYMENT_MODE=cloud`) also
  a host name that is single-label (`minio`, `api`), contains `_` (Docker's
  `<service>.<network>` form, such as `api.homayar-cloud_private`) or ends in `.internal`,
  `.local` or `.localhost`: there such names reach the internal compose services, not a
  customer.
  On-premise keeps them, and IP literals follow the one address rule below.
- One address rule, shared by the URL policy (for an IP literal) and every connect the
  worker makes:

  | Deployment | Refused addresses |
  | --- | --- |
  | every deployment | cloud metadata addresses, link-local, unspecified, multicast |
  | production (any mode) | also loopback |
  | production cloud (`DEPLOYMENT_MODE=cloud`) | also every address that is not global: RFC 1918 private, CGNAT `100.64.0.0/10`, IPv6 ULA `fc00::/7`, reserved and documentation ranges, and so on |

  Outside production loopback and private addresses stay allowed (local development and
  tests send to `127.0.0.1`); on-premise production keeps private addresses (on-premise
  CRMs live there). At send time, when the worker builds its own client, the host is
  resolved once and every returned address is judged with this rule; if any is refused no
  socket is opened (the attempt is recorded as `address_refused` and follows the normal
  retry schedule), otherwise the connect goes only to the checked addresses, in order, by
  address, so the name is never resolved a second time (a DNS rebinding cannot swap it).
  TLS still uses the URL's host name for SNI and the certificate check. With an
  environment egress proxy (`HTTP_PROXY`/`HTTPS_PROXY`, as for AI providers, ADR 0031) the
  request goes to the proxy unchanged and the proxy's rules apply; the proxy's own address
  is never judged. A host in `NO_PROXY` goes direct and is judged by the rule.
- The test route sends a real request to a customer's server, so it is rate limited per
  workspace like uploads: `WEBHOOK_TEST_RATE_LIMIT_PER_HOUR` (default 30). Over it the answer
  is `429 webhook_test_rate_limited` and no delivery is written.

### D8. Only events that something sends

The event catalogue is `webhook.test` only (`POST .../endpoints/{id}/test`). No business
event is advertised before a producer sends it. Later tickets call `enqueue_event` from
their own transactions and add their event type to the catalogue.

### D9. Retries and the dead letter (HOM-90)

A failed attempt is not final. The worker sends it again later on a fixed schedule, and
stops after a limit.

1. **Settings.** `WEBHOOK_MAX_ATTEMPTS` (default 8, 1 to 20) is the number of HTTP
   attempts. `WEBHOOK_RETRY_DELAYS_SECONDS` (default `60,300,1800,7200,18000,36000,36000`:
   1 min, 5 min, 30 min, 2 h, 5 h, 10 h, 10 h, about 27 hours in total) is a comma-separated
   list of whole seconds, each 1 to 604800 (7 days). The wait after failed attempt `n` is
   item `n` of the list; when the list is shorter, the last item repeats. There is no random
   jitter. A bad value refuses to start.
2. **A failed attempt.** Any attempt with outcome `failed` (non-2xx, a 3xx, a timeout, a
   connection error) on a row that still holds its claim is retried while
   `attempt_count < WEBHOOK_MAX_ATTEMPTS`: the row goes back to `pending` with
   `next_attempt_at = now + delay`, and `last_status_code` and `last_error` keep the
   result. The sweeper finds it when it is due; there is no Celery retry or countdown. A
   4xx is retried too: the receiver may fix its side. When the attempts are used up, the
   row moves to `dead_letter` with `next_attempt_at` empty. A `dead_letter` row is never
   due again.
3. **Refusals before sending.** `endpoint_inactive`, `url_refused`, `payload_missing` and
   `signing_unavailable` are configuration problems; a retry does not fix them. The row
   moves to `dead_letter` with the code in `last_error`. No request is sent, no attempt
   row is written and no attempt is counted. The worker never writes `failed` any more;
   only rows from HOM-89 hold it.
4. **An expired lease.** A `sending` row whose lease ended (its worker stopped after the
   claim) is claimed again while `attempt_count < WEBHOOK_MAX_ATTEMPTS`, as in D5. When the
   last attempt's lease ends, the claim moves the row to `dead_letter` with
   `last_error = lease_expired` and sends nothing. This check runs before the refusals of
   rule 3. Why: a request that stops the worker every time would otherwise loop forever.
   It weakens at-least-once for the last attempt only: that request may never have left.
   An admin can redeliver the row.
5. **Rows past the budget.** A redelivered row whose `attempt_count` already reached the
   limit gets exactly one attempt; on a failure it moves straight to `dead_letter`. A
   redelivered row with attempts left gets the rest of the schedule.
6. **Logs.** A move to the dead letter writes one WARNING line:
   `WEBHOOK_DELIVERY_DEAD_LETTER delivery_id=<id> endpoint_id=<id> attempts=<n> error_code=<code>`.
   A retry writes the `WEBHOOK_DELIVERY_ATTEMPT` line with its `next_attempt_at`. As in D4
   and D5, no log line, attempt row or `last_error` carries the URL path or query, a
   secret, a signature, the payload or the response body.

### D10. Admin diagnostics and redelivery (HOM-90)

Owners and admins can see what happened to each delivery and send a failed event again.
All routes are under `/api/v1/webhooks` and need `webhook:manage`, like the rest of D7.
Every query filters by the caller's workspace, on top of row-level security. A row of
another workspace answers like a row that does not exist: `404 webhook_delivery_not_found`
(or `404 webhook_endpoint_not_found` for an endpoint).

1. `GET /deliveries` lists the workspace's deliveries, newest first (`created_at`, then
   `id`). Filters: `endpoint_id`, `state` (one of the five states; anything else is `422`)
   and `event_type`. `limit` is 1 to 100 (default 50). `cursor` is an opaque keyset
   cursor; a bad one is `400 invalid_cursor`. The answer is
   `{"items": [...], "next_cursor": ...}`, and `next_cursor` is null on the last page.
2. A delivery view has ids, the state, `attempt_count`, the times, `last_status_code` and
   `last_error`. It never has the payload, its digest, the endpoint URL, a secret, headers
   or a signature.
3. `GET /deliveries/{id}` adds the latest 100 attempt rows, in ascending
   `attempt_number`: time, duration, outcome, status code, error code, the public key ids
   and the signed timestamp. `attempt_total` counts all attempt rows.
4. `GET /endpoints/{id}/delivery-summary` counts the endpoint's deliveries per state (all
   five, zero included). It also gives the newest `delivered_at`, and the time and error
   code of the newest failed attempt. It uses SQL aggregates and loads no rows.
5. `POST /deliveries/{id}/redeliver` (202) sends the same event again. It works only from
   `dead_letter` or `failed` (since D9 only rows from HOM-89 hold `failed`), and only when
   the row has a frozen payload. Anything else is
   `409 webhook_delivery_not_redeliverable`; an inactive endpoint is
   `409 webhook_endpoint_inactive`. The route locks the row first, then checks it. So a
   parallel second call waits, sees `pending`, and gets the 409. The row goes to `pending`
   with `next_attempt_at = now`. The event id, the payload bytes, `attempt_count`, the
   attempt rows and the last result stay. So the receiver gets the same id and the same
   body and drops a duplicate (D6), and the next attempt number is still unique. Because
   `attempt_count` stays, the retry budget of D9 rule 5 applies: a row past the limit gets
   exactly one attempt, a row with attempts left gets the rest of the schedule. The route
   writes the audit row in the same transaction and publishes the task after the commit,
   as the test route does.
6. `POST /endpoints/{id}/redeliver-dead-letters` (202) is for recovery after a receiver
   outage. It moves up to 500 of the endpoint's `dead_letter` and `failed` rows that have a
   payload, oldest first, by the rule of 5, in one transaction. An optional `since` keeps
   only rows created at or after that time. The answer says how many rows moved and
   whether more are left. It publishes nothing: the sweeper sends the rows at its own pace.
   Zero rows is a valid answer and is still audited.
7. Both write routes share one limit per workspace, `WEBHOOK_REDELIVER_RATE_LIMIT_PER_HOUR`
   (default 60), with the limiter of the test route. One call is one hit, also for a bulk
   call. Over it the answer is `429 webhook_redeliver_rate_limited` with `Retry-After`; a
   limiter that is down gives `503 rate_limiter_unavailable`. The limit is checked after
   every refusal and before any write, so a refused call costs nothing.
8. No answer, audit row or log line of these routes carries the payload, a secret, a
   signature, the path or query of the endpoint URL, or a response body. Audit details hold
   ids, states, counts and the `since` time only.

## Consequences

- Receivers get a standard scheme: HMAC over id, time and raw body, key ids for rotation,
  and a stable id for duplicates.
- Operators must set `WEBHOOK_SECRET_KEYS` and `WEBHOOK_SECRET_ACTIVE_KEY_ID` before anyone
  can add an endpoint. Removing a keyring key makes every secret under it unusable, so
  rotate the keyring by adding a key, making it active, and keeping the old key.
- Delivery is at-least-once. A receiver that ignores rule 4 can act twice on one event.
- A failed delivery is retried up to `WEBHOOK_MAX_ATTEMPTS` times over about 27 hours,
  then waits in the dead letter (D9). HOM-91 adds contract fixtures for clock skew, stale
  signatures and revoked keys. HOM-254 adds the console page.

## Known limitations

**Shared queue.** Deliveries run on the processing queue, next to meeting jobs. Each one
waits at most `WEBHOOK_DELIVERY_TIMEOUT_SECONDS` for its receiver (a total deadline, not a
per-read timeout), and the task's Celery limits end below its lease, so a slow receiver
holds a worker for seconds, not minutes. A follow-up ticket moves deliveries to their own
worker before any business event is wired. Retries (D9) wait in the database, not on the
queue: a retry holds no worker until it is due.

**Names that resolve to private addresses.** Since HOM-357 every send the worker makes
itself resolves the host once, judges every address with the one address rule (D7) and
connects only to a checked address; the URL policy also refuses non-global IP literals in
a production cloud deployment. One case stays with the network layer: a send through an
environment egress proxy goes to the proxy unchanged and the proxy's rules apply
(ADR 0031).

**Test route rate limit.** 30 test events per workspace per hour by default. The limiter is
the one uploads use: when its Redis is unavailable the route answers
`503 rate_limiter_unavailable`, or lets the request through when `RATE_LIMIT_FAIL_OPEN` is
set.

**Rotation cap.** At most 5 unexpired keys per endpoint. With 5, a rotation that keeps the
old key (overlap above 0) is refused until a retiring key expires or is revoked (D2); a
rotation with overlap 0 always works. A revoke stops a key quickly, not exactly: a
delivery whose task started before the revoke time may still sign with the revoked key
(the in-flight window, milliseconds wide, wider under DB pool wait or clock skew between
hosts).

**Endpoint limit.** The limit of active endpoints per workspace is counted without a lock,
like the API key limit, so two parallel creates can both pass at the 20th endpoint.
