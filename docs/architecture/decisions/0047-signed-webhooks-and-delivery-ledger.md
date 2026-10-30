# ADR 0047: Signed outbound webhooks and the delivery ledger

Date: 2026-10-30 · Status: Proposed (HOM-89, WP16-T05) · Builds on: ADR 0007, ADR 0013,
ADR 0023, ADR 0031

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
`webhook_signing_unavailable`), and the worker fails queued deliveries with
`signing_unavailable` instead of sending them unsigned.

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
`WEBHOOK_DELIVERY_TIMEOUT_SECONDS` (default 10).

The sweeper discovers due rows of every workspace under the read-only `webhook_delivery`
service scope. Every write binds the row's own workspace (row-level security, as for
`entitlements` and `usage_reconciliation`). The scope is never held while writing: the
claim and the record each open their own session bound to the workspace only.

With `TASKS_EAGER` (development, tests) neither a request nor a sweep sends anything
itself: a request must never wait on a receiver, and a test must never reach the
network. Due rows wait for `python -m backend.jobs.webhook_delivery` or a worker. A row
written before this ticket has no frozen payload; it is failed with `payload_missing` and
never sent.

This ticket stops at one attempt: a failed delivery stays `failed`. HOM-90 adds the retry
schedule, the dead letter and admin redelivery on the same rows.

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
6. Events may arrive more than once and out of order.

### D7. Who may manage webhooks, and where they may point

- `webhook:manage` is granted to OWNER and ADMIN only, and is never an API key scope. A
  key that could add an endpoint could copy every future event out of the workspace.
- Every change is audited (`webhook.endpoint_created`, `webhook.endpoint_updated`,
  `webhook.signing_key_rotated`, `webhook.test_queued`) with ids, key ids, event types,
  `is_active`, and only the scheme and host of the URL.
- The URL policy runs at create, at update and again before every send. Refused: more than
  1000 chars, not http(s), no host, a user name or password in the URL, cloud metadata
  names and addresses, link-local, unspecified and multicast addresses, and host spellings
  that some resolvers read as an address (`2852039166`, `0xA9FEA9FE`). In production also
  plain `http` and loopback. Private addresses stay allowed: on-premise CRMs live there.
  What a name resolves to at send time is the network layer's job, as for AI providers
  (ADR 0031).

### D8. Only events that something sends

The event catalogue is `webhook.test` only (`POST .../endpoints/{id}/test`). No business
event is advertised before a producer sends it. Later tickets call `enqueue_event` from
their own transactions and add their event type to the catalogue.

## Consequences

- Receivers get a standard scheme: HMAC over id, time and raw body, key ids for rotation,
  and a stable id for duplicates.
- Operators must set `WEBHOOK_SECRET_KEYS` and `WEBHOOK_SECRET_ACTIVE_KEY_ID` before anyone
  can add an endpoint. Removing a keyring key makes every secret under it unusable, so
  rotate the keyring by adding a key, making it active, and keeping the old key.
- Delivery is at-least-once. A receiver that ignores rule 4 can act twice on one event.
- Known limitation: the limit of active endpoints per workspace is counted without a lock,
  like the API key limit, so two parallel creates can both pass at the 20th endpoint.
- One attempt per delivery until HOM-90. HOM-91 adds contract fixtures for clock skew,
  stale signatures and revoked keys. HOM-254 adds the console page.
