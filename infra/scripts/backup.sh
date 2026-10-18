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
# run exits 3.
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
                         outside BACKUP_ROOT; BACKUP_TEXTFILE_DIR is accepted as an alias)
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

require_env BACKUP_KEY_FILE
[[ -r "${BACKUP_KEY_FILE}" ]] || die "Backup key file is not readable: ${BACKUP_KEY_FILE}"
# The freshness gauge goes to node_exporter's textfile directory, never into the 0700
# backup root (a non-root node_exporter cannot traverse it and the alert would fire).
export NODE_EXPORTER_TEXTFILE_DIR="${NODE_EXPORTER_TEXTFILE_DIR:-${BACKUP_TEXTFILE_DIR:-}}"
[[ -n "${NODE_EXPORTER_TEXTFILE_DIR}" ]] ||
  die "Set NODE_EXPORTER_TEXTFILE_DIR to node_exporter's --collector.textfile.directory (outside BACKUP_ROOT)."
[[ -d "${NODE_EXPORTER_TEXTFILE_DIR}" && -w "${NODE_EXPORTER_TEXTFILE_DIR}" ]] ||
  die "NODE_EXPORTER_TEXTFILE_DIR must exist and be writable by the backup user (BACKUP_UID) and readable by node_exporter: ${NODE_EXPORTER_TEXTFILE_DIR}"
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

output="$(tool create)"
printf '%s\n' "${output}"
backup_id="$(sed -n 's/^  "backup_id": "\([^"]*\)",*$/\1/p' <<<"${output}" | head -n 1)"
[[ -n "${backup_id}" ]] || die "The backup did not report its id."
degraded=0
grep -q '"integrity": "degraded"' <<<"${output}" && degraded=1

# The deep verify marks the set verified and moves the freshness gauges.
tool verify "${backup_id}"

offsite_target=""
if [[ "${offsite}" == "1" ]]; then
  offsite_target="${BACKUP_OFFSITE:-/offsite}"
  tool replicate "${backup_id}" "${offsite_target}"
fi

if [[ "${prune}" == "1" ]]; then
  tool prune
  # The off-site copy follows the same retention (deleted data must expire there too).
  if [[ -n "${offsite_target}" ]]; then
    tool prune --destination "${offsite_target}"
  fi
fi

if [[ "${degraded}" == "1" ]]; then
  printf 'ERROR: backup %s has degraded object integrity (see stores.objects in its manifest).\n' "${backup_id}" >&2
  exit 3
fi
printf 'Backup %s completed.\n' "${backup_id}"
