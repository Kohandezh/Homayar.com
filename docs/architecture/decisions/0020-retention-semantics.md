# ADR 0020 — Explicit retention semantics and the safe migration of the ambiguous zero

Date: 2026-09-16 · Status: accepted · Package: WP07-T01

## Context

Retention was one integer, `Workspace.retention_days >= 0` (default 90),
shown in the console as "Data retention (days) — zero means no retention
period". Nothing enforced it: uploads stamped `retention_expires_at = now +
days` on the conversation (so a zero expired at ingestion), the replay guard
refused expired conversations, and no job ever deleted anything by age. The
zero was therefore ambiguous — "delete at once" and "keep forever" were both
defensible readings — and WP07 (retention, deletion, text privacy) cannot
build a purge sweep on an ambiguous value. F15 requires that finite expiry
and explicit immediate/indefinite settings behave as documented.

## Decision

1. **Three explicit modes, one legacy sentinel.** `Workspace.retention_mode`
   is `finite` (deletion-eligible `retention_days` ∈ [1, 3650] after
   ingestion), `indefinite` (never eligible by age; only an explicit
   deletion removes data), or `immediate` (eligible as soon as processing is
   terminal — the sweep never removes a conversation that is still
   processing). `legacy_unresolved` is what the migration leaves for a
   pre-WP07 row with `retention_days = 0`: nothing is reinterpreted, no
   expiry is stamped, nothing is purged by age, and an administrator must
   pick an explicit mode. It can be read but never chosen. A check
   constraint ties `retention_days >= 1` to `finite` and forces 0 under
   every other mode.
2. **The workspace row is the only authority.** Clients never supply an
   expiry. `retention_expiry_for(workspace, now)` is evaluated at ingestion
   and stored on the conversation and its media rows; every sweep re-checks
   `is_age_based_deletion_enabled(workspace)` before it acts on a stored
   expiry, so switching to `indefinite` stops age-based deletion even for
   rows carrying an old date, and the legacy value is never destructive.
   Only `workspace:manage` (+CSRF) changes the policy; every change is
   audited by field name.
3. **Ambiguity is refused, not guessed.** A bare `retention_days: 0` is a
   422; `retention_days` under `indefinite`/`immediate` needs the mode as
   well; switching to `finite` needs a positive number; a positive number
   sent to a `legacy_unresolved` workspace resolves it to `finite` (that is
   an explicit choice). `RETENTION_DAYS_DEFAULT` must be 1..3650 so new
   workspaces start `finite`.
4. **Migration `c3e7a9d2b5f1`** adds the column with server default
   `FINITE`, marks `retention_days = 0` rows `LEGACY_UNRESOLVED`, then adds
   the constraint. The downgrade drops only the mode column; the days are
   untouched, so the pre-WP07 state (ambiguous zero included) is restored
   losslessly.
5. **`delete_audio_after_processing` stays** as the narrower, audio-only
   preference that already runs today; `immediate` is the broader policy the
   WP07-T03 purge will act on (original, derived data, transcripts) once it
   exists. Precedence: a legal hold (WP07-T02) overrides both.

## Consequences

- Until WP07-T03 exists no job deletes by age; T01 only makes the intent
  unambiguous and stamps expiries the sweep can trust. Business defaults
  (which mode, how many days) remain the owner's decision; engineering tests
  use explicit fixture policies.
- Existing conversations under a `legacy_unresolved` workspace keep whatever
  `retention_expires_at` they carry; the sweep will ignore it because the
  mode is not enforceable. Resolving the workspace to `finite` does not
  rewrite historical expiries (a later task may offer an explicit "apply to
  existing" action; not designed here).
- The console shows the policy selector and, for a legacy value, a warning;
  the days field appears only for `finite`.
