#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/compose.sh
source "${SCRIPT_DIR}/lib/compose.sh"

backup_path=""
confirmation=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup)
      [[ $# -ge 2 ]] || die "--backup requires a path"
      backup_path="$2"
      shift 2
      ;;
    --confirm)
      [[ $# -ge 2 ]] || die "--confirm requires the target database name"
      confirmation="$2"
      shift 2
      ;;
    --help)
      printf 'Usage: %s --backup PATH --confirm DATABASE\n' "$(basename -- "$0")"
      exit 0
      ;;
    *) die "Unknown argument: $1" ;;
  esac
done

require_env POSTGRES_DB
require_env POSTGRES_USER
[[ -n "${backup_path}" ]] || die "--backup is required"
[[ "${confirmation}" == "${POSTGRES_DB}" ]] ||
  die "--confirm must exactly match POSTGRES_DB (${POSTGRES_DB})"

if [[ -d "${backup_path}" ]]; then
  backup_path="${backup_path%/}/database.dump"
fi
backup_path="$(canonical_existing_path "${backup_path}")"
[[ -r "${backup_path}" ]] || die "Backup is not readable: ${backup_path}"
backup_dir="$(dirname -- "${backup_path}")"
metadata_file="${backup_dir}/metadata.txt"
toc_file="${backup_dir}/table-of-contents.txt"
if [[ -r "${backup_dir}/.complete" ]]; then
  :
elif [[ "${ALLOW_INCOMPLETE_BACKUP:-0}" != "1" ]]; then
  die "Backup completion marker is missing. Set ALLOW_INCOMPLETE_BACKUP=1 only after independent verification."
fi

verify_checksum "${backup_path}"
if [[ -r "${metadata_file}" && -r "${toc_file}" ]]; then
  verify_checksum "${metadata_file}"
  verify_checksum "${toc_file}"
  source_format="$(metadata_value "${metadata_file}" format)"
  source_database="$(metadata_value "${metadata_file}" database)"
  [[ "${source_format}" == "postgres-custom" ]] ||
    die "Unsupported PostgreSQL backup format in ${metadata_file}: ${source_format:-missing}"
  if [[ "${source_database}" != "${POSTGRES_DB}" && "${ALLOW_SOURCE_DATABASE_MISMATCH:-0}" != "1" ]]; then
    die "Backup source database (${source_database:-missing}) does not match POSTGRES_DB (${POSTGRES_DB}). Set ALLOW_SOURCE_DATABASE_MISMATCH=1 only for an approved rename or migration."
  fi
elif [[ "${ALLOW_UNVERIFIED_BACKUP:-0}" != "1" ]]; then
  die "Backup metadata or table of contents is missing. Set ALLOW_UNVERIFIED_BACKUP=1 only for an independently verified legacy backup."
fi
compose_onprem exec -T postgres pg_restore --list < "${backup_path}" >/dev/null
require_writers_stopped

compose_onprem exec -T postgres sh -ec '
  export PGPASSWORD="$POSTGRES_PASSWORD"
  exec pg_restore \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    --exit-on-error \
    --single-transaction
' < "${backup_path}"

printf 'PostgreSQL restore completed into database %s from %s\n' "${POSTGRES_DB}" "${backup_path}"
printf 'Next: apply newer migrations, restore MinIO if needed, then run infra/scripts/reapply-tombstones.sh BEFORE starting api or worker (WP07-T04).\n'

