# On-Prem Lifecycle Runbook

One operator command runs the whole life of an on-prem (customer) install:
`infra/scripts/homayar-onprem.sh` (HOM-110, ADR 0064). It installs, upgrades,
rolls back, runs backups and restores, and collects a sanitized support
bundle. Run it on the install host, never with sudo, and always with stdin
from `/dev/null`:

```bash
bash homayar-onprem.sh <command> [flags] </dev/null
```

Exit codes for every command: `0` done (or already done); `1` failed and
rolled back (the data was restored); `2` refused, or failed before any live
change; `3` the rollback failed, manual action is needed.

The health checks curl the proxy on `127.0.0.1` and the `HTTP_PORT` of
`config/onprem.env` (8080 when unset). When `BIND_ADDRESS` in the env file
is set to another address than `0.0.0.0`, that address is used instead of
loopback.

## Layout

Everything lives under `HOMAYAR_HOME` (default `/opt/homayar`):

```text
/opt/homayar/
  releases/<version>/     one extracted package per version
  current -> releases/<version>   switched atomically by the script
  config/onprem.env       operator settings and secrets (mode 600)
  state/<state-id>/       upgrade records and pre-migration dumps (mode 700)
  ops/                    the lock and one log per run
```

`config/onprem.env` is the Compose env file. It holds the secrets described
in [deployment.md](deployment.md). No value from it ever reaches a log, the
console output or a support bundle.

## The package

A release arrives as a folder (or a `.tar` of its contents), signed with an
Ed25519 key (HOM-107, ADR 0070):

- `SHA256SUMS`: one `sha256sum` line per file. A missing file, a changed
  file, an extra unlisted file, or a `..` or absolute path refuses the
  package.
- `SHA256SUMS.sig`: the raw 64-byte Ed25519 signature over the exact bytes
  of `SHA256SUMS`. `install` and `upgrade` verify it against the public
  keys of the trust directory (below) before anything else; an unsigned or
  tampered package is refused with exit 2 and nothing is changed. There is
  no override flag.
- `BUNDLE.manifest`: the signed description of the bundle (schema line,
  `release` line, one `image` line per `images/*.tar` with its hash, image
  reference and licence, one `model` line per file under `models/`, and the
  `requires` lines of the host). A manifest that disagrees with the package
  (a hash, an image reference, a licence, a missing or extra line) refuses
  even when the signature is good.
- `release.env`: exactly `HOMAYAR_RELEASE_VERSION`, `HOMAYAR_RELEASE_SHA`
  (40 hex), `API_IMAGE`, `CONSOLE_WEB_IMAGE`, `EGRESS_PROXY_IMAGE` with
  versioned tags. A `:local` or `:latest` tag is refused. The file is parsed
  as data, never sourced.
- `images/*.tar`: `docker save` archives of the three images.
- `models/`, `manifests/`: only when the package embeds model files (see
  `pack --model-root` below).
- `infra/`: the release's `compose/`, `scripts/` and `nginx/` folders.

Build one from a clean checkout with already-built images:

```bash
bash homayar-onprem.sh pack --source <checkout> --output /tmp/homayar-0.6.0 \
  --version 0.6.0 \
  --api-image registry/homayar/api:0.6.0 \
  --console-web-image registry/homayar/console-web:0.6.0 \
  --egress-proxy-image registry/homayar/egress-proxy:0.6.0 \
  --signing-key /path/to/bundle-signing-key.pem
```

`pack` refuses a dirty checkout, a non-empty output folder, a missing
signing key, a key with group or other permission bits, a key that is not
Ed25519, and a checkout without `infra/offline/bundle.conf` (the committed
file that holds the image licences and the `requires` lines written into
`BUNDLE.manifest`). On success it prints the signing key's fingerprint.

To embed model files, pass the operator model manifests and the model root
(both flags together, the manifest flag repeatable):

```bash
bash homayar-onprem.sh pack ... --signing-key <key.pem> \
  --model-root /path/to/model-artifacts \
  --model-manifest infra/manifests/<set>/<model>.json
```

Every `file` artifact must exist under the model root and hash to its pin;
it is copied to `models/<path>` and described by a signed `model` line with
the manifest's licence and id. `dir` artifacts are refused (not supported
yet). `pack` needs python3 on the packing host for this; `install` and
`upgrade` never need python3.

## Package signing

The signature turns the hash list into a statement by the signer. The
private key never lives in the repository, on the customer host or in CI:
it stays on an offline signing host (or wherever you keep release
credentials), mode 600, and only `pack` ever uses it. Test suites generate
throwaway keys at run time; no key file is ever committed.

Make a key pair once:

```bash
openssl genpkey -algorithm ed25519 -out bundle-signing-key.pem   # keep offline
chmod 600 bundle-signing-key.pem
openssl pkey -in bundle-signing-key.pem -pubout -out bundle-public.pem
```

Install the public half on the customer host, out of band (the package
never carries trust):

```bash
mkdir -p /opt/homayar/config/bundle-keys
cp bundle-public.pem /opt/homayar/config/bundle-keys/homayar-bundle.pem
```

`HOMAYAR_BUNDLE_TRUST_DIR` points the check somewhere else (tests use it);
the default is `$HOMAYAR_HOME/config/bundle-keys`. Any `*.pem` there that
is not an Ed25519 public key makes every install refuse (fail closed, the
message names the file), so keep the directory to real bundle keys only. A
package is accepted when **any** trusted key verifies it.

The key fingerprint (first 16 hex of the sha256 of the key's DER public
key) is printed by `pack` and by every verified `install` and `upgrade`,
and lands in the `ops/` log, so you can see which key signed and which key
verified. Fingerprints never contain key material.

Rotation: generate the new key, copy the new public PEM next to the old one
in `config/bundle-keys`, sign new packages with the new key, and remove the
old PEM only when no old package ever needs to install again.

What a refusal means: `signature` in the output says no trusted key could
verify `SHA256SUMS.sig` over the package's `SHA256SUMS` (unsigned package,
broken transfer, a re-packed package, or a key that is not trusted on this
host); `BUNDLE.manifest` says the signed description disagrees with the
package contents. Both exit 2, nothing was changed, and there is no flag to
skip either check: get a good package or fix the trust directory.

## Install

First install on an empty host. Write `config/onprem.env` first, then:

```bash
bash homayar-onprem.sh install --package /tmp/homayar-0.5.0 </dev/null
```

The command stages the package, verifies its signature against the trust
directory, checks the hash list and `release.env`, checks `BUNDLE.manifest`,
prints the verifying key's fingerprint and the signed `requires` lines, then
extracts the release to `releases/<version>`, loads
the images, renders the Compose config with the operator env file, checks
free disk, runs the host preflight, starts the stores, runs the migration
job, starts the stack, waits for `/healthz` and `/readyz` on the proxy, and
points `current` at the release.

Install refuses (exit 2) when `current` already exists. Use `upgrade` then.
A failure names the failed step and changes nothing: the release is extracted
to a temporary folder that is removed on any refusal, so a retry is never
blocked by a half-extracted `releases/<version>`. Operator data volumes are
never deleted.

## Upgrade

```bash
bash homayar-onprem.sh upgrade --package /tmp/homayar-0.6.0 </dev/null
```

Before anything changes, the command checks: the new package's signature,
hash list and `BUNDLE.manifest` (exactly as `install` does); the stack is
healthy; free disk
on `HOMAYAR_HOME` and the Docker root (10 GiB default, `--min-disk-gib`); the
new release's Compose config renders; and a verified full backup is fresh.
Fresh means the `homayar_backup_last_success_timestamp_seconds` gauge in
`NODE_EXPORTER_TEXTFILE_DIR/homayar_backup.prom` is younger than
`--max-backup-age-hours` (default 24). When it is missing or old, the upgrade
refuses and tells you to run `homayar-onprem.sh backup`. You can override
with `--skip-backup-check "<reason>"`; the reason is written into the state
record.

Then the upgrade writes a state record (from and to version and SHA, from
image IDs, the alembic version before, the backup gauge time), stops the
writers (`proxy console-web api worker scheduler`; the stores keep running),
dumps the database with `pg_dump -Fc` into `state/<state-id>/db.dump` and
proves the dump restores into a scratch database that is dropped after. Only
then does it switch `current`, run the migration job, start the stack and
check health, the running api image and the database version.

On success (exit 0) the command prints the state id and the exact manual
rollback command. The dump and the record stay in `state/<state-id>/`.

## Failed-upgrade recovery

The automatic rollback runs when anything from the `current` switch on
fails: it stops the writers, restores the dump when the alembic version
moved, switches `current` back and restarts the old release.

- Exit 1: the upgrade failed and the rollback restored the old release and
  the old data. The stack is up on the old version. Fix the cause and rerun.
- Exit 3: the rollback itself failed. The writers stay stopped and the exact
  manual steps are printed at the end of the output. Run them in order and
  check each result. The steps are also in the run log in `ops/`.

A failing dump is different: nothing was changed yet, so the command restarts
the writers on the old release and exits 2.

The database is never downgraded with `alembic downgrade`. A database only
ever goes back by restoring the pre-migration dump.

## Manual rollback

After an upgrade that finished, you can go back to the old release. This
restores the pre-migration dump, so writes made after the writers were
stopped are lost. You must confirm that boundary with the exact `stopped_at`
timestamp of the state record:

```bash
bash homayar-onprem.sh rollback --to <state-id> \
  --accept-data-loss-since <the record's STOPPED_AT value> </dev/null
```

Find the state id and the timestamp with `bash homayar-onprem.sh status`
(it prints the latest state record). A timestamp that does not match exactly
is refused. The command also refuses when `current` is not the record's
to-version, when the dump or the from-release folder is missing, or when one
of the from-images no longer exists.

## Backup and restore

The `backup` and `restore` subcommands run the release's own scripts with the
operator env file:

```bash
bash homayar-onprem.sh backup
bash homayar-onprem.sh restore --help
```

What they do, how to verify a set and how to restore into an isolated staging
database is in [backup-restore.md](backup-restore.md).

## Support bundle

```bash
bash homayar-onprem.sh support-bundle --output /tmp/homayar-support.tar.gz
```

The bundle is a `.tar.gz` (mode 600) with: `host.json` (kernel, docker and
compose versions, service states and exit codes, image references and IDs,
free disk, the name, driver and memory of every GPU when present),
`preflight.json` (the host preflight report), `logs-summary.json` (per
service: the number of log lines in the last 24 h and how many hold `ERROR`,
`WARNING` or `CRITICAL`; no log text), `container.json` (version and build
identity, Python and package versions, the public settings, database
reachability, server and alembic version), and `MANIFEST.json` (file list
with sha256).

What never goes in: no secrets, tokens, keys, passwords or DSNs; no URLs with
credentials; no private keys; no JWTs; no e-mail addresses; no tenant data,
names, transcripts or per-tenant counts; no log lines; no audio; no host
name or IP addresses. Settings outside a short public allowlist (booleans,
numeric limits, mode and topology names) appear only as `set` or `unset`.

The bundle is scanned before it is written, and the scan cannot be skipped:
it runs in the api image (as your own user, so it can read what it scans)
over the collected directory with the operator env file, looking for secret
values, credential URLs, private keys, JWTs, e-mail addresses, Persian text,
audio data and non-loopback IP addresses. Any finding, any scan failure, or
any file the scan cannot read deletes the collected files and exits 2
without writing the bundle. Nothing leaves the host unless the scan is clean.

## Status and preflight

```bash
bash homayar-onprem.sh status
bash homayar-onprem.sh preflight [--offline]
```

`status` prints the current version and SHA, the service rows, the image IDs,
the alembic version and the latest state record. It prints no secrets.
`preflight` renders the Compose config, checks free disk and runs the model
preflight with `--host` inside the api container
([deployment.md](deployment.md), "Host preflight"); the preflight exit code
(0 ready, 1 not ready, 2 configuration error) is passed through.

## Limits

- Packages are signed, but the signer is only as trustworthy as the signing
  host: keep the key offline, and rotate it by adding the new public PEM to
  `config/bundle-keys` (see "Package signing").
- Settings changes between releases are manual: compare
  `releases/<old>/infra/compose/compose.onprem.yaml` with the new release
  and update `config/onprem.env` yourself before upgrading.
- There is no licence check yet (HOM-109) and no network-denied install test
  (HOM-111). The upgrade rehearsal on real fixture data is HOM-112.
- One operator at a time: a held lock (`ops/homayar-onprem.lock`) makes every
  state-changing command refuse with exit 2. A lock left behind by a killed
  run is removed by hand after checking no run is active.
- Old releases and images are never removed by the script. Clean them up by
  hand once an upgrade proved itself.
