# ADR 0073: Signed offline licence

Date: 2026-11-13 · Status: Proposed (HOM-109, WP19-T04, F74/F75; parent HOM-23) · Builds on: ADR 0065, ADR 0070

## Context

An on-prem install had no trusted, offline way to say what the customer may
use and until when. `backend/security/modules.py::_licensed_keys` licensed
every module when a workspace had no subscription ("the HOM-109 gap", ADR
0065), and nothing limited processing by a licence. A customer host is often
air-gapped, so the answer cannot come from a licence server. HOM-107 (ADR 0070)
already made signed packages with Ed25519 and a host trust directory; HOM-229
(ADR 0065) made the capability registry that names the modules a licence can
turn on. One work unit, one root cause: no signed, offline licence. No
migration, no database change.

## Decision

### D1. Enforcement is opt-in

Two settings: `OFFLINE_LICENSE_FILE` (the absolute path of the licence file)
and `OFFLINE_LICENSE_TRUST_DIR` (the directory of trusted public keys). An
empty `OFFLINE_LICENSE_FILE` means the licence is not enforced, and every
behaviour stays exactly as before (state `not_enforced`). A non-empty value
turns enforcement on. `compose.onprem.yaml` passes both to `api` and `worker`
(the trust dir defaults to `/app/license/keys`) and binds the host licence
folder read-only at `/app/license` (`OFFLINE_LICENSE_DIR`, default the
`infra/compose/license` folder that ships in every package).

### D2. Files and signature

`license.json` (UTF-8 JSON, at most 65536 bytes) and `license.json.sig` (the
raw 64-byte Ed25519 signature of the exact file bytes) side by side. The
vendor signs with OpenSSL 3 as in ADR 0070 D1:
`openssl pkeyutl -sign -rawin -inkey key.pem -in license.json -out license.json.sig`.
The backend verifies with the pinned `cryptography` package; it never runs
`openssl`.

### D3. Trust lives on the host (as ADR 0070 D3)

Verifying keys are the `*.pem` files of the trust dir. Any trusted key
verifies (rotation). A `*.pem` that is not an Ed25519 public key makes the
licence `invalid` (`bad_trust_key`, the log names the file, never its
content). An empty, missing or key-less trust dir is `invalid`
(`no_trusted_keys`). Nothing inside the licence is used for trust.

### D4. Verification order, all offline

Missing or unreadable file or `.sig`: `missing`. Then the trust keys, then the
size (`too_large`, without reading the whole file), then the signature
(`bad_signature`: a `.sig` that is not 64 bytes, a tampered byte, or a key
that is not trusted), and only then the JSON (`malformed`). The JSON schema
`homayar-offline-license/1` is strict: a duplicate key anywhere, an unknown or
missing top-level key, or a wrong type is `malformed`. A date whose derived
value leaves the datetime range (`expires_at + grace_days`, `issued_at` minus
one day) is `malformed` too, decided once when the licence is parsed. Unknown
module keys and unknown limit metrics are ignored and reported (forward
compatible).

The evaluation never raises to its callers. Any other unexpected error inside
it gives `invalid`, reason `malformed`, logged once per process at `WARNING`
with the exception class name only (`OFFLINE_LICENSE_EVALUATION_FAILED`). So
module reads keep working and new processing stays refused.

### D5. States from the host clock

From a verified licence and `now` (UTC wall clock, read on every evaluation):
`valid` from `issued_at` minus one day until `expires_at`; `grace` until
`expires_at + grace_days`; `expired` after that. A clock more than one day
before `issued_at` is `invalid` (`not_yet_valid`). "Restricted" means
`expired`, `invalid` or `missing`.

### D6. A per-process cache of the verification only

The verification outcome is cached per process, keyed by the
`(st_mtime_ns, st_size, st_ino)` of the licence file, the `.sig` and every
`*.pem` of the trust dir (with their names). A changed file is picked up on
the next evaluation without a restart. A `missing` outcome is not cached. The
state is recomputed from `now` on every evaluation.

### D7. The processing gate is the reservation

`entitlement_service.reserve_usage`, after its idempotent early return and
before the entitlement lookup, raises `LicenseRestrictedError(state, reason)`
in a restricted state (no row read or written). In `valid` and `grace`, a
licence cap of the metric adds one condition to the existing atomic `UPDATE`
(`used + reserved + amount <= cap`); a refusal is the existing
`QuotaExceededError` with the smaller of the row limit and the cap. No lock,
no locking query and no lock-order change (HOM-333). `commit_usage` and
`release_usage` never read the licence. The upload, the voicemail import and
the job replay answer `403 license_restricted`. The manual analysis recompute
(`POST /api/v1/meetings/{meeting_id}/analysis/recompute`) is new processing
without a reservation: it checks the licence first and answers the same 403
before it records or dispatches anything.

### D8. Modules

In `valid` and `grace`, the licensed modules are the licence modules
intersected with the plan rule of ADR 0065 (a module the plan does not limit
is licensed only if the licence names it; a refusal is the existing
`module_disabled` / `not_licensed`). In a restricted state the module guard
lets `GET`, `HEAD` and `OPTIONS` pass the licence part and refuses every other
method with `403 license_restricted`; the install and admin switches still
apply first, as before. The capabilities listing in a restricted state shows
the plan rule as if the licence were not enforced; the console reads the state
from the operator endpoint and the response header.

### D9. Operator visibility

`GET /api/v1/admin/license`, guarded like system-health (platform operator,
session only, never an API key, `no-store`, not audited): the state, the
verified fields, `days_left`, the warnings, and one counter per entitlement
row of the caller's workspace with its row limit, licence limit and remaining
allowance. Every API response carries `X-Homayar-License: <state>` when the
licence is enforced and not `valid`; a failed evaluation sends `invalid` and
never fails the request. One `OFFLINE_LICENSE_STATE` warning line per process
when a state is first seen and when it changes; no customer name and no key
material in logs. `python -m backend.licensing status-line` prints one line
and exits 0 (no database, no network). When it cannot evaluate (for example
the settings do not load) it writes a short error to stderr, prints nothing on
stdout and exits 1; it never invents a state. `homayar-onprem.sh status`
prints the line, or `license: unreadable`.

## Consequences

- Opt-in enforcement: production `app.homayar.com` runs `compose.onprem.yaml`
  and has no licence, so a default-on licence would make it read-only at the
  next release. An install is licensed only once its operator sets
  `OFFLINE_LICENSE_FILE`.
- Limits are per workspace: the licence cap applies to each workspace's
  entitlement row on its own, not to the sum over the install (the
  entitlements table is per workspace). An install-wide total needs a new
  counter.
- A host clock set back extends a licence, and nothing detects it: there is
  no monotonic high-water mark without a schema change (a follow-up). A clock
  set forward ends a licence early. The runbook asks for a correct clock.
- A restricted state refuses exactly three things: new reservations (uploads,
  voicemail imports, job replays), every write to a module router (any method
  but `GET`, `HEAD`, `OPTIONS`), and the manual analysis recompute. It never
  loses data: no read, list, download, export, sign-in, sign-out or deletion
  request is refused, and no stored data is changed or hidden.
- Worker-side gating happens only at reservation time: a job that already
  holds its reservation finishes and settles normally after the licence
  expires.
- Core edits outside the module routers stay allowed on purpose in a
  restricted state (transcript corrections, action items, meeting updates): a
  customer must be able to correct and finish work on data they already have.
- Follow-ups, not gated by this decision: the sweeper's automatic recompute
  after a core edit still runs in a restricted state; and the CORS
  `expose_headers` does not list `X-Homayar-License`, so a console on another
  origin cannot read the header (a console behind the same proxy can).
- The licence is a contract tool, not copy protection: an operator with root
  on the host controls the trust dir and the code. It protects an honest
  customer from silent overuse and makes the terms visible, offline.
- A renewal replaces two files; a request between the two copies can see
  `invalid` for a moment. The runbook copies to temporary names and renames.

## Alternatives considered

- **Default-on enforcement.** Simpler to reason about, but it turns the
  current production server read-only on upgrade; opt-in keeps every
  existing install as it is.
- **Writing licence limits into the entitlements table (a new source label
  or a sync job).** Needs a migration and a second writer of the rows the
  reservation path locks; an extra condition in the existing atomic `UPDATE`
  reads the licence without writing any row.
- **An online licence server or periodic check-in.** Impossible on an
  air-gapped host, and a network dependency in the hot path.
- **A signed JWT or JWS.** A second format and parser next to the raw
  Ed25519 signature that ADR 0070 already uses with the same OpenSSL tooling.
- **Caching the state, not only the verification.** The state depends on
  `now`; caching it would delay an expiry until the cache is dropped.
