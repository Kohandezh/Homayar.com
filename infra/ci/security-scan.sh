#!/usr/bin/env bash
# Dependency, container and SBOM scans with a vulnerability gate (HOM-130).
# The Security scans workflow (.github/workflows/security-scans.yml) runs these
# commands. Run them locally from the repository root with the same arguments.
#
#   security-scan.sh deps <out-dir>     SBOM, guard and gate for the 4 lockfiles
#   security-scan.sh images <out-dir>   build the 3 images, then SBOM, guard and gate
#   security-scan.sh canary <out-dir>   prove the gate denies a known finding
#   security-scan.sh gate <name> <sbom> <out-dir>   gate one CycloneDX SBOM
#
# Syft writes one CycloneDX JSON SBOM per target. Grype scans it with the triage
# ignore file (.grype.yaml, or SECURITY_SCAN_CONFIG) and writes a JSON report.
# The gate fails a target when Grype reports a high or critical vulnerability
# that has a fix and no ignore rule. Findings without a fix, and medium or low
# findings, stay in the report but do not fail the gate.
#
# The guard fails a target when its SBOM lacks a known component. Without it, a
# Syft that stops reading a lockfile format would give an empty SBOM and a green gate.
#
# Exit codes: 0 pass, 1 the gate found a fixable high/critical vulnerability,
# 2 usage error, 3 the SBOM guard failed, 4 a tool failed, 5 the canary failed.
set -Eeuo pipefail

# Official Anchore images, pinned to the multi-platform index digest of the tag
# (resolved on 2026-10-29 with `docker buildx imagetools inspect <tag>`).
SYFT_IMAGE="anchore/syft:v1.55.0@sha256:4cc92b64ca74ffca4c94d4c8fcc01f217731375300be14d0b6eeb2e82eaa110a"
GRYPE_IMAGE="anchore/grype:v0.121.0@sha256:621d56d1d68106b787022c81546f7e0a36202a25d1239f0c20195fab8c16f7ea"

# The canary fixture lists lodash 4.17.20. GHSA-35jh-r3h4-6jhm (CVE-2021-23337,
# high, command injection in template) is fixed in 4.17.21.
CANARY_SBOM="infra/ci/fixtures/canary-sbom.cdx.json"
CANARY_ID="GHSA-35jh-r3h4-6jhm"

GRYPE_CONFIG="${SECURITY_SCAN_CONFIG:-.grype.yaml}"
IMAGE_PREFIX="${SECURITY_SCAN_IMAGE_PREFIX:-homayar-scan}"
DB_CACHE="${SECURITY_SCAN_DB_CACHE:-${XDG_CACHE_HOME:-${HOME}/.cache}/homayar-security-scan/grype-db}"

usage() {
  printf 'usage: %s deps|images|canary <out-dir>\n       %s gate <name> <sbom> <out-dir>\n' "$0" "$0" >&2
  exit 2
}

fail_tool() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 4
}

# Callers run gate_sbom and the guards inside `||` or `$(...)`, where Bash turns
# `set -e` off. So every read whose result decides pass or fail checks its own
# status and calls fail_tool; a missing or broken file is a tool error, never a pass.

# check_json <file> <jq-test> <what>: fail_tool unless <file> is JSON and <jq-test> is true.
check_json() {
  if [[ ! -s "$1" ]] || ! jq --exit-status "$2" "$1" >/dev/null 2>&1; then
    fail_tool "$3 is missing or not valid ($1)"
  fi
}

check_setup() {
  if [[ ! -f .github/workflows/security-scans.yml || ! -f "${CANARY_SBOM}" ]]; then
    printf 'ERROR: run this script from the repository root.\n' >&2
    exit 2
  fi
  if [[ ! -f "${GRYPE_CONFIG}" ]]; then
    printf 'ERROR: Grype config %s not found.\n' "${GRYPE_CONFIG}" >&2
    exit 2
  fi
  command -v jq >/dev/null || fail_tool "jq is required"
  command -v docker >/dev/null || fail_tool "docker is required"
}

# The containers see the repository read-only at /repo, the output folder at /out
# and a scratch folder at /tmp. Every path given to a tool must be under one of them.
# They start in /tmp, so Grype reads only the config passed with --config and never
# finds /repo/.grype.yaml (or a test config) a second time on its own.
prepare_out() {
  mkdir -p "$1/sbom" "$1/reports" "${DB_CACHE}"
  out_dir="$(cd -- "$1" && pwd -P)"
  work_dir="$(mktemp -d)"
  trap 'rm -rf -- "${work_dir}"' EXIT
  # A copy, so a config outside the repository (a local test) works the same way.
  cp -- "${GRYPE_CONFIG}" "${work_dir}/grype.yaml"
}

run_tool() {
  local image="$1"
  shift
  # npm lockfiles skip dev dependencies by default. The uv and pnpm catalogers list
  # every locked package, so the npm SBOMs include dev dependencies too.
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp \
    --env TMPDIR=/tmp \
    --env GRYPE_DB_CACHE_DIR=/cache \
    --env SYFT_JAVASCRIPT_INCLUDE_DEV_DEPENDENCIES=true \
    --volume "$(pwd -P):/repo:ro" \
    --volume "${out_dir}:/out" \
    --volume "${DB_CACHE}:/cache" \
    --volume "${work_dir}:/tmp" \
    --workdir /tmp \
    "${image}" "$@"
}

update_db() {
  run_tool "${GRYPE_IMAGE}" db update --quiet || fail_tool "grype db update failed"
  run_tool "${GRYPE_IMAGE}" db status --output json >"${out_dir}/reports/grype-db-status.json" ||
    fail_tool "grype db status failed"
  check_json "${out_dir}/reports/grype-db-status.json" '.valid == true and (.built | type == "string")' \
    "the Grype database status"
  printf 'Grype database built %s\n' "$(jq -r '.built' "${out_dir}/reports/grype-db-status.json")"
}

# make_sbom <name> <source>; the source is a Syft source as seen inside the container.
make_sbom() {
  local name="$1" source="$2"
  printf '\n== SBOM %s (%s)\n' "${name}" "${source}"
  run_tool "${SYFT_IMAGE}" scan "${source}" --quiet \
    --output "cyclonedx-json=/out/sbom/${name}.cdx.json" ||
    fail_tool "syft failed for ${name}"
  check_json "${out_dir}/sbom/${name}.cdx.json" '.components | type == "array"' "the SBOM of ${name}"
  printf '%s components\n' "$(jq '.components | length' "${out_dir}/sbom/${name}.cdx.json")"
}

# guard_component <name> <purl-prefix>: the SBOM of <name> must list a component
# whose package URL starts with <purl-prefix> (for example pkg:pypi/fastapi@).
guard_component() {
  local name="$1" prefix="$2"
  check_json "${out_dir}/sbom/${name}.cdx.json" '.components | type == "array"' "the SBOM of ${name}"
  if jq --exit-status --arg prefix "${prefix}" \
    '[.components[]? | select((.purl // "") | startswith($prefix))] | length > 0' \
    "${out_dir}/sbom/${name}.cdx.json" >/dev/null; then
    printf 'guard: %s lists %s\n' "${name}" "${prefix}"
  else
    printf 'GUARD FAILED: %s does not list %s. The SBOM is empty or incomplete.\n' "${name}" "${prefix}" >&2
    guard_failed=1
  fi
}

# guard_os_packages <name>: an image SBOM must list at least one Debian or Alpine package.
guard_os_packages() {
  local name="$1" count
  check_json "${out_dir}/sbom/${name}.cdx.json" '.components | type == "array"' "the SBOM of ${name}"
  count="$(jq '[.components[]? | select((.purl // "") | test("^pkg:(deb|apk)/"))] | length' \
    "${out_dir}/sbom/${name}.cdx.json")" || fail_tool "could not count OS packages of ${name}"
  if [[ "${count}" =~ ^[0-9]+$ && "${count}" -gt 0 ]]; then
    printf 'guard: %s lists %s OS packages\n' "${name}" "${count}"
  else
    printf 'GUARD FAILED: %s lists no OS package.\n' "${name}" >&2
    guard_failed=1
  fi
}

# gate_sbom <name>: gate the SBOM at <out-dir>/sbom/<name>.cdx.json.
# Returns 0 on pass and 1 on a gate failure; exits 4 when the report cannot be read.
gate_sbom() {
  local name="$1" report="${out_dir}/reports/$1.grype.json" blocking ignored
  printf '\n== Gate %s\n' "${name}"
  # A stale report from an earlier run must not be read as this run's result.
  rm -f -- "${report}"
  run_tool "${GRYPE_IMAGE}" "sbom:/out/sbom/${name}.cdx.json" --quiet \
    --config /tmp/grype.yaml \
    --name "${name}" \
    --sort-by severity \
    --output table \
    --output "json=/out/reports/${name}.grype.json" ||
    fail_tool "grype failed for ${name}"
  # Grype always writes `matches` (an empty array when clean) and leaves out
  # `ignoredMatches` when no rule matched.
  check_json "${report}" '(.matches | type == "array") and ((.ignoredMatches // []) | type == "array")' \
    "the Grype report of ${name}"
  blocking="$(jq -r '
    .matches[]
    | select(.vulnerability.severity == "High" or .vulnerability.severity == "Critical")
    | select(.vulnerability.fix.state == "fixed")
    | "\(.vulnerability.id) \(.vulnerability.severity) \(.artifact.name)@\(.artifact.version) fixed in \(.vulnerability.fix.versions | join(", "))"
  ' "${report}")" || fail_tool "could not read the Grype report of ${name}"
  blocking="$(sort -u <<<"${blocking}")"
  if [[ -n "${blocking}" ]]; then
    printf 'GATE FAILED: %s has fixable high/critical vulnerabilities without a triage ignore rule:\n%s\n' \
      "${name}" "${blocking}"
    return 1
  fi
  ignored="$(jq '.ignoredMatches // [] | length' "${report}")" || fail_tool "could not read the Grype report of ${name}"
  printf 'gate: %s passed (%s ignored by .grype.yaml rules)\n' "${name}" "${ignored}"
}

# finish <gate-failed>: the guard runs before the gate, so a guard failure wins.
finish() {
  if [[ "${guard_failed}" -ne 0 ]]; then
    exit 3
  fi
  if [[ "$1" -ne 0 ]]; then
    exit 1
  fi
  printf '\nAll targets passed.\n'
}

scan_deps() {
  local failed=0
  update_db
  make_sbom backend "file:/repo/Homayar/uv.lock"
  make_sbom console "file:/repo/Homayar/frontend/package-lock.json"
  make_sbom website "file:/repo/website/pnpm-lock.yaml"
  make_sbom linear-agent "file:/repo/tools/linear-agent/package-lock.json"
  guard_component backend "pkg:pypi/fastapi@"
  guard_component console "pkg:npm/react@"
  guard_component website "pkg:npm/next@"
  guard_component linear-agent "pkg:npm/%40anthropic-ai/claude-agent-sdk@"
  for name in backend console website linear-agent; do
    gate_sbom "${name}" || failed=1
  done
  finish "${failed}"
}

scan_images() {
  local failed=0 name
  update_db
  # Default build ARGs, as the container-config job in ci.yml builds them.
  for name in api console-web egress-proxy; do
    printf '\n== Build %s\n' "${name}"
    docker build --quiet --file "infra/docker/${name}.Dockerfile" --tag "${IMAGE_PREFIX}/${name}:local" . ||
      fail_tool "docker build failed for ${name}"
    # Syft reads the saved image from disk, so no container gets the Docker socket.
    docker save --output "${work_dir}/${name}.tar" "${IMAGE_PREFIX}/${name}:local" ||
      fail_tool "docker save failed for ${name}"
    make_sbom "image-${name}" "docker-archive:/tmp/${name}.tar"
    rm -f -- "${work_dir}/${name}.tar"
  done
  guard_component image-api "pkg:pypi/fastapi@"
  for name in api console-web egress-proxy; do
    guard_os_packages "image-${name}"
  done
  for name in api console-web egress-proxy; do
    gate_sbom "image-${name}" || failed=1
  done
  finish "${failed}"
}

# The canary passes only when the gate fails with exit code 1 and names the
# expected ID. A pass proves the scanner, the database and the gate are live.
scan_canary() {
  local status=0 output
  update_db
  cp -- "${CANARY_SBOM}" "${out_dir}/sbom/canary.cdx.json"
  output="$(gate_sbom canary 2>&1)" || status=$?
  printf '%s\n' "${output}"
  if [[ "${status}" -eq 1 ]] && grep -q "^${CANARY_ID} " <<<"${output}"; then
    printf '\ncanary: the gate denied %s as expected.\n' "${CANARY_ID}"
    return 0
  fi
  printf '\nCANARY FAILED: expected the gate to fail with exit 1 and name %s (exit was %s).\n' \
    "${CANARY_ID}" "${status}" >&2
  exit 5
}

guard_failed=0
[[ $# -ge 1 ]] || usage
command="$1"
shift
case "${command}" in
  deps | images | canary)
    [[ $# -eq 1 ]] || usage
    check_setup
    prepare_out "$1"
    "scan_${command}"
    ;;
  gate)
    [[ $# -eq 3 ]] || usage
    check_setup
    [[ -f "$2" ]] || { printf 'ERROR: SBOM %s not found.\n' "$2" >&2; exit 2; }
    prepare_out "$3"
    cp -- "$2" "${out_dir}/sbom/$1.cdx.json"
    update_db
    status=0
    gate_sbom "$1" "$2" || status=$?
    finish "${status}"
    ;;
  *) usage ;;
esac
