# ADR 0064: On-prem lifecycle and support bundle

Date: 2026-11-13 · Status: Proposed (HOM-110, WP19-T05, F75; parent HOM-23) · Builds on: ADR 0047

## Context

A customer installs Homayar on their own host. They need install, upgrade,
failed-upgrade recovery, rollback, backup, restore and a way to send us
diagnostics. The acceptance that matters (F75): a prior-version upgrade and a
failed-upgrade recovery preserve data, and the default support bundle holds
no canary secret, transcript or identity. The pieces that exist are
scattered: `backup.sh` and `restore-isolated.sh`, the model preflight, the
build identity in `backend.observability.release`, and the release script of
our own app server (`app-server-release.sh`), whose dump, prove-restore,
switch and rollback flow is proven in production. Licence checks (HOM-109),
signed bundles (HOM-107), the network-denied install test (HOM-111) and the
upgrade rehearsal on real fixture data (HOM-112) are separate tickets.

## Decision

### D1. One operator command

`infra/scripts/homayar-onprem.sh` owns the whole life of an install:
`pack`, `preflight`, `install`, `upgrade`, `rollback`, `backup`, `restore`,
`support-bundle`, `status`. Exit codes mean the same everywhere: 0 done, 1
failed and rolled back with data restored, 2 refused or failed before any
live change, 3 rollback failed and manual steps are printed. A
`mkdir`-based lock in `ops/` allows one operator at a time. Every run logs
to `ops/` through a filter that hides runs of 20 or more hex characters, and
no value of the operator env file is ever logged.

### D2. The package is a hash list, with a seam for signing

A package is a folder (or a tar of it) with `SHA256SUMS`, a `release.env`
of five keys (version, 40-hex SHA, three versioned image references),
`images/*.tar` and `infra/`. Verification refuses a tampered file, a missing
file, an unlisted file, a `..` or absolute path, and any deviation in
`release.env`; `:local` and `:latest` tags are refused. `release.env` is
parsed as data, never sourced. The `verify_package_signature` function is
the seam HOM-107 fills: today it only prints that the package is
hash-checked, not signed.

### D3. Upgrades take a pre-migration dump and require a fresh verified backup

Before anything changes, the upgrade checks stack health, free disk, that
the new Compose config renders, and that the backup freshness gauge
(`homayar_backup_last_success_timestamp_seconds`, written only after a deep
verify by `backup.sh`) is younger than 24 hours by default; the operator can
override with a recorded reason. Then it writes a state record (from and to
identity, from image IDs, alembic version before, stopped-at time), stops
the writers, dumps the database and proves the dump restores into a scratch
database before it switches `current`, migrates and starts the stack. A
failure from the switch on rolls back automatically: restore the dump when
the alembic version moved, switch `current` back, restart the old release.
The database is never downgraded with alembic; it only goes back by
restoring the dump. Manual rollback requires the exact `stopped_at`
timestamp, so the operator states the data-loss boundary explicitly.

### D4. The support bundle is allowlist-only and fails closed

`backend.support_bundle` runs in the api image. `collect` reports build
identity, Python and a short package list, and the settings: only names on a
`PUBLIC_SETTINGS` allowlist (booleans, numeric limits, mode and topology
names) carry their value; every other setting appears as `set` or `unset`.
Database facts are reachability, server version and alembic version; no
tenant row, name or count. The shell part adds host facts (no hostname, no
IPs, no process list), a log summary with counts only, and a manifest. The
whole bundle is then scanned in the api image with the operator env file:
secret values, credential URLs, private keys, JWTs, e-mail addresses,
Persian text, audio data and non-loopback IP addresses are findings, and
image IDs, digests and the release SHA are not. Any finding or scan error
deletes the collected files and writes nothing. There is no way to skip the
scan.

## Consequences

- An operator needs one command and one runbook
  (`docs/operations/onprem-lifecycle.md`) instead of a wiki page of steps.
- Upgrade downtime is the stop, dump, migrate and start window; the dump is
  the data-preservation guarantee, which is why its restore is proven before
  the switch.
- Old releases and images stay on disk (the script never prunes), so disk
  use grows per upgrade until an operator cleans up.
- Until HOM-107, a package is only as trustworthy as its transfer channel.
- The scanner may refuse a bundle over a false positive (for example an
  e-mail-shaped string in a version label). The operator can inspect the
  finding kinds, fix the source and rerun; there is no override by design.

## Alternatives considered

- **Ansible or another config-management layer.** Another runtime to
  install, air-gap and trust on a customer host; the single bash script with
  the proven `app-server-release.sh` patterns needs nothing extra.
- **Blue-green with two live stacks.** Doubles the host requirements (the
  P40 reference host is already fully used by the model); the dump plus
  symlink switch gives the same recovery with minutes of downtime.
- **`alembic downgrade` for rollback.** Downgrades are untested and can lose
  data silently; restoring the pre-migration dump is the tested, exact
  boundary.
- **A denylist scanner** (strip known secret names). One forgotten name
  leaks; the allowlist plus fail-closed scan can only leak what a name on
  the allowlist explicitly permits.
