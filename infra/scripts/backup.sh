#!/usr/bin/env bash
# One scheduled backup run (HOM-116, ADR 0035): create -> verify -> off-site copy -> prune.
#
# Runs the backup-tool service of the on-prem Compose project against the
# running postgres and minio services. Exits non-zero on any failure; the
# freshness textfile (homayar_backup_last_success_timestamp_seconds) only
# moves forward after a step succeeded, so the backup-age alert fires.
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
  BACKUP_TEXTFILE_DIR    node_exporter textfile directory (default \$BACKUP_ROOT/metrics)
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
export BACKUP_TEXTFILE_DIR="${BACKUP_TEXTFILE_DIR:-${BACKUP_ROOT}/metrics}"
mkdir -p -- "${BACKUP_TEXTFILE_DIR}"
if [[ "${offsite}" == "1" && -z "${BACKUP_OFFSITE:-}" && -z "${BACKUP_OFFSITE_PATH:-}" ]]; then
  die "No off-site target: set BACKUP_OFFSITE (s3://) or BACKUP_OFFSITE_PATH, or pass --no-offsite."
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

tool verify "${backup_id}"

if [[ "${offsite}" == "1" ]]; then
  if [[ -n "${BACKUP_OFFSITE:-}" ]]; then
    tool replicate "${backup_id}" "${BACKUP_OFFSITE}"
  else
    tool replicate "${backup_id}" /offsite
  fi
fi

if [[ "${prune}" == "1" ]]; then
  tool prune
fi

printf 'Backup %s completed.\n' "${backup_id}"
