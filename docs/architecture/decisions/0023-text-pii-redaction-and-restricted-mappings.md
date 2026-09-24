# ADR 0023 — Deterministic text PII redaction and restricted mappings

Date: 2026-09-24 · Status: accepted, amended by ADR 0024 · Package: WP07-T05

## Context

Transcripts carry identifiers people say aloud in meetings: mobile numbers, national
IDs, card numbers, IBANs, e-mail addresses. Customers need some workspaces to run
analysis, search and exports on a copy without them, while a few authorised people
can still recover an original for a legitimate reason. Persian transcripts mix
Persian, Arabic-Indic and ASCII digits, spaces and dashes inside numbers, and
several prefixes for the same phone number. A statistical named-entity model would
also catch names, but its behaviour cannot be validated here (no annotated Persian
set, no approved model), and a redaction feature that silently misses is worse than
one whose limits are stated.

## Decision

1. **Deterministic, validated classes only.** `backend/privacy/text_pii.py`
   (`DETECTOR_VERSION = "text-pii/1"`) detects `email`, `phone` (Iranian mobile and
   landline in every common spelling, international numbers with an explicit
   `+`/`00`), `ir_national_id` (check digit), `bank_card` (Luhn), `iban` (mod 97,
   26 characters for `IR`) and `ir_postal_code` (only within 48 characters after a
   postal-code keyword). Digits are normalised one character for one, so offsets
   always refer to the original text; overlapping candidates resolve to the longest,
   then the better validated class. Names, addresses, dates of birth, plates,
   spelled-out numbers and numbers split across segments are **not** detected; the
   module docstring, the authorization matrix and this ADR state it.
2. **Policy per workspace.** `pii_redaction_enabled` (default off, existing
   workspaces backfilled off) and `pii_redaction_classes` (only supported classes,
   at least one) change through `PATCH /admin/workspace-settings` with
   `workspace:manage` (ADR 0024 adds `pii:original_access`) and are audited by
   field name.
3. **Redacted versions are immutable derivatives.** A version is identified by
   `(revision, policy_digest)`, where the digest covers the detector version and the
   sorted class list; a detector or policy change produces a new version and never
   rewrites one (ORM immutability guard, unique constraint, idempotent creation that
   tolerates a concurrent creator). Placeholders (`[PHONE_1]`) are stable per value
   within a version.
4. **Mapping copies only as ciphertext, keys outside the database.** The redacted
   version and the mappings hold no replaced value in plain text; the original
   transcript segments stay in the database as the access-controlled original
   (not encrypted by the application; correction recorded in ADR 0024). Each
   replaced value is copied into `pii_mappings` as AES-256-GCM ciphertext with a
   random nonce; the associated data binds it to tenant, redaction and placeholder. Keys come from
   `PII_MAPPING_KEYS` (`key_id:base64`, secret manager) with
   `PII_MAPPING_ACTIVE_KEY_ID` for new rows; older key ids still decrypt, so keys
   rotate without a bulk re-encryption. Without a key the redacted version is still
   produced but nothing original is retained (`mapping_retained = false`) and no
   reveal is possible. Removing an old key from the keyring makes its rows
   unrecoverable (crypto-shredding by key retirement).
5. **Reveal is audited and value-free.** `reveal_placeholder` decrypts one
   placeholder for a caller whose permission was checked and records
   `pii.revealed` with the placeholder, class and a reason — never the value. Logs
   and audits of the whole feature carry ids, counts, classes and versions only.
6. **Tenant data, purged with the conversation.** `transcript_redactions`,
   `redacted_segments` and `pii_mappings` are workspace tables under forced RLS and
   are removed by the conversation purge (ADR 0021) before the transcript rows.
7. **Accuracy gate.** The feature acceptance (F16: recall ≥ 95 %, precision ≥ 90 %
   on a separately annotated supported-class Persian/English set) is checked in CI
   against a small synthetic annotated set covering every class plus hard
   negatives (dates, invoice and tracking numbers, ticket ids). That is an
   engineering gate, not the acceptance measurement: no customer-representative
   annotated set exists yet.

## Consequences

- Redaction reduces exposure; it is not anonymisation and never masks audio. Product
  copy and exports must say so (WP07-T06, WP14).
- Nothing routes analysis, retrieval, export or provider input to the redacted
  version yet, the pipeline does not create versions automatically and there is no
  reveal route; that is WP07-T06, and WP08-T03 (privacy/eligibility filtering)
  depends on it.
- A key compromise exposes the mappings of every redaction encrypted under it until
  the key is rotated and retired; key custody belongs to the deployment's secret
  manager (WP20).
- Adding a class or changing a validator is a new detector version and therefore new
  redacted versions; old versions stay as they were until the conversation is
  purged.
