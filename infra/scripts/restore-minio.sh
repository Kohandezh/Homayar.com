#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/compose.sh
source "${SCRIPT_DIR}/lib/compose.sh"

backup_path=""
confirmation=""
delete_extra=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup)
      [[ $# -ge 2 ]] || die "--backup requires a path"
      backup_path="$2"
      shift 2
      ;;
    --confirm-bucket)
      [[ $# -ge 2 ]] || die "--confirm-bucket requires the target bucket name"
      confirmation="$2"
      shift 2
      ;;
    --delete-extra)
      delete_extra=1
      shift
      ;;
    --help)
      printf 'Usage: %s --backup DIRECTORY --confirm-bucket BUCKET [--delete-extra]\n' "$(basename -- "$0")"
      exit 0
      ;;
    *) die "Unknown argument: $1" ;;
  esac
done

require_env MINIO_ROOT_USER
require_env MINIO_ROOT_PASSWORD
MINIO_BUCKET="${MINIO_BUCKET:-homayar-private}"
export MINIO_BUCKET
[[ -n "${backup_path}" ]] || die "--backup is required"
[[ "${confirmation}" == "${MINIO_BUCKET}" ]] ||
  die "--confirm-bucket must exactly match MINIO_BUCKET (${MINIO_BUCKET})"

backup_path="$(canonical_existing_path "${backup_path}")"
[[ -d "${backup_path}/objects" ]] || die "Backup objects directory is missing: ${backup_path}/objects"
[[ -r "${backup_path}/.complete" ]] ||
  [[ "${ALLOW_INCOMPLETE_BACKUP:-0}" == "1" ]] ||
  die "Backup completion marker is missing. Set ALLOW_INCOMPLETE_BACKUP=1 only after independent verification."
verify_checksum "${backup_path}/inventory.jsonl"
verify_checksum "${backup_path}/metadata.txt"
source_format="$(metadata_value "${backup_path}/metadata.txt" format)"
source_bucket="$(metadata_value "${backup_path}/metadata.txt" bucket)"
[[ "${source_format}" == "minio-mirror-current" ]] ||
  die "Unsupported MinIO backup format: ${source_format:-missing}"
if [[ "${source_bucket}" != "${MINIO_BUCKET}" && "${ALLOW_SOURCE_BUCKET_MISMATCH:-0}" != "1" ]]; then
  die "Backup source bucket (${source_bucket:-missing}) does not match MINIO_BUCKET (${MINIO_BUCKET}). Set ALLOW_SOURCE_BUCKET_MISMATCH=1 only for an approved rename or migration."
fi
require_writers_stopped
backup_set="$(backup_relative_path "${backup_path}")"

compose --profile onprem --profile ops run --rm --no-deps \
  --env "BACKUP_SET=${backup_set}" \
  --env "DELETE_EXTRA=${delete_extra}" \
  minio-tools '
    mc alias set target "$MINIO_ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
    mc mb --ignore-existing "target/$MINIO_BUCKET"
    mc version enable "target/$MINIO_BUCKET"
    mc anonymous set none "target/$MINIO_BUCKET"
    if [ "$DELETE_EXTRA" = "1" ]; then
      mc mirror --overwrite --preserve --remove "/backups/$BACKUP_SET/objects" "target/$MINIO_BUCKET"
    else
      mc mirror --overwrite --preserve "/backups/$BACKUP_SET/objects" "target/$MINIO_BUCKET"
    fi
  '

printf 'MinIO restore completed into bucket %s from %s\n' "${MINIO_BUCKET}" "${backup_path}"
