# ADR 0070: Signed offline bundles

Date: 2026-11-13 · Status: Proposed (HOM-107, WP19-T02, F74/F75; parent HOM-23) · Builds on: ADR 0064

## Context

An on-prem package travels to a customer host over a channel we do not
control (a download, a USB disk, a customer file share). ADR 0064 made every
package a hash list: `SHA256SUMS` proves a file was not changed since it was
hashed, but nothing proves who hashed it, and the signature seam of ADR
0064 (`verify_package_signature`) was a no-op stub that ran on the raw
`--package` path before staging. The same
package carried no machine-readable description: which licence each image is
under, which model files it embeds, and what the host must provide to run it.
HOM-107 (F74/F75) fills that gap inside `infra/scripts/homayar-onprem.sh`
(HOM-110): `pack` produces a signed, described bundle, and `install` and
`upgrade` refuse an unsigned or tampered one. One work unit, one root cause:
packages were neither signed nor described. No migration, no backend change.

## Decision

### D1. Scheme: Ed25519 with OpenSSL 3

Keys are PEM Ed25519 made with `openssl genpkey -algorithm ed25519`;
signing is `openssl pkeyutl -sign -rawin`, verifying
`openssl pkeyutl -verify -pubin -rawin`. OpenSSL 3 is already on every
target host (Ubuntu 22.04+, RHEL 9+; the `python:3.12-slim` test image ships
3.5), so an air-gapped host needs no extra binary.

### D2. What is signed

The exact bytes of the package's `SHA256SUMS`. The signature is
`SHA256SUMS.sig`, the raw 64-byte Ed25519 signature (binary, exactly 64
bytes). Because every package file must be listed in `SHA256SUMS`
(unlisted files are refused), one signature covers the folder form and the
`.tar` form alike.

### D3. Trust lives on the host, never in the package

Verifying keys are the `*.pem` files in the trust directory: env
`HOMAYAR_BUNDLE_TRUST_DIR`, default `$HOMAYAR_HOME/config/bundle-keys`. The
operator copies the public key there out of band. A package is accepted when
any trusted key verifies its signature (this is key rotation: old and new
key coexist during a switch). A `.pem` in the trust directory that is not an
Ed25519 public key makes the check refuse, naming the file; a missing or
empty trust directory refuses. Any key file inside the package is never used
for trust.

### D4. The signing key at pack time

`--signing-key <file>` or env `HOMAYAR_BUNDLE_SIGNING_KEY` (the flag wins);
no key refuses. The key must be a regular file with no group or other
permission bits (`mode & 077 == 0`, like ssh) and an Ed25519 private key,
else pack refuses. The key value is never copied into the package, printed
or logged. Keys are never committed: tests generate throwaway keys in their
temp folder at run time.

### D5. The bundle manifest

`BUNDLE.manifest` is a text file listed in `SHA256SUMS` (so it is signed).
One record per line, fields joined by one space, no field contains a space:
`schema homayar-offline-bundle/1`; one `release <version> <sha>` line equal
to `release.env`; one `image <path> <sha256> <image-ref> <license>` line per
`images/*.tar` (api, console-web, egress-proxy, in `release.env` order of
keys but sorted by path); one `model <path> <sha256> <license>
<manifest_id>` line per file under `models/`; one or more
`requires <name> <constraint>` lines for the host. A licence matches
`^[A-Za-z0-9][A-Za-z0-9.+-]*$` (an SPDX id or `LicenseRef-...`). Comments
and empty lines are not allowed.

### D6. Licences and requirements come from the checkout

`infra/offline/bundle.conf` (committed) holds `license <image> <licence>`
for the three Homayar images (all `LicenseRef-Homayar-Proprietary`; the
repository has no open licence file) and the `requires` lines. `#` comments
and empty lines are ignored in this input file only. Requirements: bash
`>=4.4` and openssl `>=3.0` (what the script itself needs), docker-engine
`>=27.3.1` and docker-compose-plugin `>=2.29.7` (assumed floors taken from
the values the test fixtures print, not measured on a real host; the
network-denied install test, HOM-111, should confirm or correct them), and
`nvidia-container-toolkit gpu-profile-only` (needed only for the GPU
profile). A missing file, a missing licence, or zero `requires` lines makes
pack refuse.

### D7. Model files in the bundle

Optional, repeatable in pack: `--model-root <dir>` and
`--model-manifest <file.json>`, both needed together. Each manifest is the
existing operator model manifest format (`homayar.model-manifests/1`).
Every artifact of kind `file` is path-checked (same rules as `SHA256SUMS`
paths), must exist under the model root, and must hash to its pin; it is
copied to `models/<path>` and gets a `model` line with the manifest's
licence and `manifest_id`. A `dir` artifact refuses (not supported yet); a
manifest without a licence refuses. The manifest JSON files are copied to
`manifests/<basename>` and signed like every other file. Only pack parses
JSON (it may use `python3 -I`); install and upgrade never need python3.

### D8. Verification order

`install` and `upgrade` stage the package first (a `.tar` is extracted into
the temp staging folder), then check the signature on the staged folder,
then the existing `verify_package` rules, then the `BUNDLE.manifest` rules.
Only after all three pass may images load, a release folder be created, or
the stack be touched. Every refusal exits 2, says why (the word
`signature` for signature failures, `BUNDLE.manifest` for manifest
failures) and prints `Nothing was changed.` as refusals always do.

### D9. The unlisted-file rule exempts exactly two names

`verify_package` still refuses any unlisted file, except `SHA256SUMS` and
`SHA256SUMS.sig` at the package root. Every other unlisted name (a stray
`extra.pem`, a `SHA256SUMS.sig2`) still refuses.

### D10. BUNDLE.manifest is checked, not just signed

Because a signed-but-wrong bundle must also refuse: the first line is
exactly the schema line; exactly one `release` line equal to `release.env`;
every `images/*.tar` has exactly one `image` line and every `image` line
names an existing `images/*.tar`; the same both ways for `models/**` and
`model` lines; each line's sha256 equals that file's hash in `SHA256SUMS`;
each image ref equals the matching `release.env` value; each licence
matches D5; at least one `requires` line; any other record type, an extra
or missing field, or a duplicate path refuses.

### D11. Output and logs

After a good signature check the run prints and logs one line naming the
verifying key's fingerprint: the first 16 hex characters of the sha256 of
the key's DER public key (16, because the log filter hides runs of 20 or
more hex characters). `install` and `upgrade` print the `requires` lines.
`pack` prints the signing key's fingerprint. Nothing ever prints `PRIVATE
KEY` or key bytes.

### D12. No override

There is no `--allow-unsigned`. A HOM-110-era package (no `SHA256SUMS.sig`,
no `BUNDLE.manifest`) is refused by install and upgrade; an operator who
must install one downgrades the script or stays on the old release.

## Consequences

- A package is as trustworthy as the signing key, not the transfer channel.
  The private key lives on an offline signing host: never in the repository,
  never on the customer host, never in CI (tests use throwaway keys).
- Rotation is additive: drop the new public PEM next to the old one in
  `config/bundle-keys`, ship packages signed by the new key, remove the old
  PEM when no old package must install again.
- Every refusal is a hard stop; there is no flag to skip the signature, so
  an operational incident needs a new package (or a deliberate key change),
  never a bypass.
- The docker-engine and compose-plugin constraints are recorded as
  `>=27.3.1` and `>=2.29.7`: assumed floors taken from the values the test
  fixtures print, not measured on a real host. The network-denied install
  test (HOM-111) should confirm or correct them; `bundle.conf` is the single
  place to change.
- `pack` now needs an Ed25519 key and (only with model flags) python3 on
  the packing host; `install` and `upgrade` need openssl 3 on the customer
  host, which every supported host already has.
- HOM-110 packages made before this change no longer install; the first
  signed release must be installed with a script from this change forward.

## Alternatives considered

- **minisign.** A small, well-audited tool, but an extra binary to carry
  into the air gap and to keep updated on every customer host; OpenSSL 3 is
  already there.
- **cosign key-pair signing.** Oriented to OCI registries and bundles of
  large Go binaries; the bundle is a folder or tar, not a registry object,
  and shipping the cosign binary into the air gap repeats the minisign
  problem.
- **GPG.** Keyring state on every host, a large attack surface, and parsing
  its output safely in bash is fragile; signing and verifying would depend
  on per-host configuration, not just a PEM file.
- **Signing a manifest of files instead of `SHA256SUMS` itself.** A second
  list to keep in sync with the hash list; signing the hash list that
  `verify_package` already consumes covers every file with one structure.
- **A `--allow-unsigned` escape hatch.** Every escape hatch ends up in a
  runbook under pressure and voids the guarantee; a deliberate downgrade of
  the script is the honest form of the same escape.
