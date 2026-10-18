#!/usr/bin/env bash
# One scheduled backup run (HOM-116, ADR 0035): create -> verify -> off-site copy -> prune.
#
# Runs the backup-tool service of the on-prem Compose project against the
# running postgres and minio services. Exits non-zero on any failure. The
# freshness textfile (homayar_backup_last_success_timestamp_seconds) moves
# only after a deep verify (postgres, objects) or a verified off-site copy
# (offsite), so the backup-age alert fires when a step fails. A set whose
# object copy is degraded (missing or mismatching required objects) is still
# verified and copied off-site, but its objects gauge does not move and the
# run exits 3. When the freshness textfile cannot be written, the backup,
# off-site copy and prune still run (the stale or missing gauge raises the
# alert) and the run exits 4.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/compose.sh
source "${SCRIPT_DIR}/lib/compose.sh"

usage() {
  cat <<EOF
Usage: BACKUP_ROOT=/secure/homayar-backups BACKUP_KEY_FILE=/run/secrets/homayar-backup.key \\
       [BACKUP_OFFSITE=s3://bucket/prefix | BACKUP_OFFSITE_PATH=/mnt/offsite/homayar] \\
       $(basename -- "$0") [--no-prune] [--no-offsite]

Environment:
  BACKUP_KEY_FILE        key_id:base64(32 bytes) from the secret manager (required)
  BACKUP_OFFSITE         s3:// target on another host (BACKUP_OFFSITE_S3_* credentials)
  BACKUP_OFFSITE_PATH    or a mounted directory of another host/medium
  NODE_EXPORTER_TEXTFILE_DIR  node_exporter's --collector.textfile.directory (required,
                         outside BACKUP_ROOT, writable by BACKUP_UID:BACKUP_GID;
                         BACKUP_TEXTFILE_DIR is accepted as an alias)
Relative paths are resolved against the Compose file's directory, as Compose does.
Exit codes: 0 done, 3 degraded object copy, 4 freshness textfile not written, else failed.
EOF
}

prune=1
offsite=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-prune) prune=0; shift ;;
    --no-offsite) offsite=0; shift ;;
    --help) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

# Compose resolves a relative bind source against its project directory (the
# Compose file's): resolve every host path the same way, so the checks below
# look at the directories the containers will get.
COMPOSE_DIR="$(cd -- "$(dirname -- "${COMPOSE_FILE}")" && pwd -P)"
compose_path() {
  case "$1" in
    /*) printf '%s
' "$1" ;;
    *) printf '%s/%s
' "${COMPOSE_DIR}" "$1" ;;
  esac
}

require_env BACKUP_KEY_FILE
BACKUP_ROOT="$(compose_path "${BACKUP_ROOT}")"
BACKUP_KEY_FILE="$(compose_path "${BACKUP_KEY_FILE}")"
export BACKUP_ROOT BACKUP_KEY_FILE
[[ -r "${BACKUP_KEY_FILE}" ]] || die "Backup key file is not readable: ${BACKUP_KEY_FILE}"
if [[ -n "${BACKUP_OFFSITE_PATH:-}" ]]; then
  BACKUP_OFFSITE_PATH="$(compose_path "${BACKUP_OFFSITE_PATH}")"
  export BACKUP_OFFSITE_PATH
fi
# The freshness gauge goes to node_exporter's textfile directory, never into the 0700
# backup root (a non-root node_exporter cannot traverse it and the alert would fire).
NODE_EXPORTER_TEXTFILE_DIR="${NODE_EXPORTER_TEXTFILE_DIR:-${BACKUP_TEXTFILE_DIR:-}}"
[[ -n "${NODE_EXPORTER_TEXTFILE_DIR}" ]] ||
  die "Set NODE_EXPORTER_TEXTFILE_DIR to node_exporter's --collector.textfile.directory (outside BACKUP_ROOT)."
NODE_EXPORTER_TEXTFILE_DIR="$(compose_path "${NODE_EXPORTER_TEXTFILE_DIR}")"
export NODE_EXPORTER_TEXTFILE_DIR
# Must exist: Docker would create a missing bind source owned by root. Whether the
# container user can write it is probed in the container below (textfile-check).
[[ -d "${NODE_EXPORTER_TEXTFILE_DIR}" ]] ||
  die "NODE_EXPORTER_TEXTFILE_DIR does not exist; create it owned by BACKUP_UID:BACKUP_GID (${BACKUP_UID}:${BACKUP_GID}), mode 0755: ${NODE_EXPORTER_TEXTFILE_DIR}"
case "$(cd -- "${NODE_EXPORTER_TEXTFILE_DIR}" && pwd -P)/" in
  "$(cd -- "${BACKUP_ROOT}" && pwd -P)"/*)
    die "NODE_EXPORTER_TEXTFILE_DIR is inside BACKUP_ROOT: node_exporter cannot read it through the 0700 backup root."
    ;;
esac
if [[ "${offsite}" == "1" && -z "${BACKUP_OFFSITE:-}" && -z "${BACKUP_OFFSITE_PATH:-}" ]]; then
  die "No off-site target: set BACKUP_OFFSITE (s3://) or BACKUP_OFFSITE_PATH, or pass --no-offsite."
fi
if [[ "${offsite}" == "1" && -z "${BACKUP_OFFSITE:-}" && -n "${BACKUP_OFFSITE_PATH:-}" ]]; then
  # The tool only sees the container paths (/backups, /offsite): compare the host paths here.
  [[ -d "${BACKUP_OFFSITE_PATH}" ]] || die "BACKUP_OFFSITE_PATH is not a directory (is the off-site medium mounted?): ${BACKUP_OFFSITE_PATH}"
  root_real="$(cd -- "${BACKUP_ROOT}" && pwd -P)"
  offsite_real="$(cd -- "${BACKUP_OFFSITE_PATH}" && pwd -P)"
  case "${offsite_real}/" in
    "${root_real}"/*) die "BACKUP_OFFSITE_PATH is inside BACKUP_ROOT: that is not an off-site copy." ;;
  esac
  case "${root_real}/" in
    "${offsite_real}"/*) die "BACKUP_ROOT is inside BACKUP_OFFSITE_PATH: that is not an off-site copy." ;;
  esac
  if [[ "$(df -P -- "${root_real}" | awk 'NR == 2 { print $6 }')" == "$(df -P -- "${offsite_real}" | awk 'NR == 2 { print $6 }')" ]]; then
    printf 'WARNING: BACKUP_OFFSITE_PATH is on the same filesystem as BACKUP_ROOT; it is off-site only if that filesystem is another host or medium.\n' >&2
  fi
fi
if [[ "${offsite}" == "1" && "${BACKUP_OFFSITE:-}" == s3://* && -z "${EGRESS_PROXY_URL:-}" ]]; then
  die "An s3:// off-site target is reached through the egress proxy: start --profile egress, allowlist its host and set EGRESS_PROXY_URL."
fi

tool() {
  compose --profile onprem --profile ops run --rm --no-deps -T backup-tool "$@"
}

running="$(compose_onprem ps --status running --services)"
grep -qx postgres <<<"${running}" || die "postgres is not running in this Compose project."
grep -qx minio <<<"${running}" || die "minio is not running in this Compose project."

# A gauge that cannot be written never stops the backup, the off-site copy or
# prune: those steps exit 4 when only the textfile failed, the run goes on, the
# stale or missing gauge raises the alert and the run exits 4 at the end.
gauge_failed=0
gauge_step() {
  local status=0
  tool "$@" || status=$?
  if [[ "${status}" == "4" ]]; then
    gauge_failed=1
    return 0
  fi
  return "${status}"
}
# As the container user (BACKUP_UID, no CAP_DAC_OVERRIDE): a host-side check cannot tell.
if ! tool textfile-check; then
  printf 'WARNING: %s:%s cannot write NODE_EXPORTER_TEXTFILE_DIR (%s); the backup goes on without its gauges.\n' \
    "${BACKUP_UID}" "${BACKUP_GID}" "${NODE_EXPORTER_TEXTFILE_DIR}" >&2
  gauge_failed=1
fi

output="$(tool create)"
printf '%s\n' "${output}"
backup_id="$(sed -n 's/^  "backup_id": "\([^"]*\)",*$/\1/p' <<<"${output}" | head -n 1)"
[[ -n "${backup_id}" ]] || die "The backup did not report its id."
degraded=0
grep -q '"integrity": "degraded"' <<<"${output}" && degraded=1

# The deep verify marks the set verified and moves the freshness gauges.
gauge_step verify "${backup_id}"

offsite_target=""
if [[ "${offsite}" == "1" ]]; then
  offsite_target="${BACKUP_OFFSITE:-/offsite}"
  gauge_step replicate "${backup_id}" "${offsite_target}"
fi

if [[ "${prune}" == "1" ]]; then
  tool prune
  # The off-site copy follows the same retention (deleted data must expire there too).
  if [[ -n "${offsite_target}" ]]; then
    tool prune --destination "${offsite_target}"
  fi
fi

if [[ "${gauge_failed}" == "1" ]]; then
  printf 'ERROR: backup %s completed, but the freshness textfile in %s could not be written (owner must be BACKUP_UID:BACKUP_GID = %s:%s).\n' \
    "${backup_id}" "${NODE_EXPORTER_TEXTFILE_DIR}" "${BACKUP_UID}" "${BACKUP_GID}" >&2
fi
if [[ "${degraded}" == "1" ]]; then
  printf 'ERROR: backup %s has degraded object integrity (see stores.objects in its manifest).\n' "${backup_id}" >&2
  exit 3
fi
[[ "${gauge_failed}" == "0" ]] || exit 4
printf 'Backup %s completed.\n' "${backup_id}"
