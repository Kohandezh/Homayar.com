#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/compose.sh
source "${SCRIPT_DIR}/lib/compose.sh"

if [[ "${1:-}" == "--help" ]]; then
  printf 'Usage: BACKUP_ROOT=/secure/path %s\n' "$(basename -- "$0")"
  exit 0
fi
[[ $# -eq 0 ]] || die "Unexpected arguments. Use --help for usage."

require_env MINIO_ROOT_USER
require_env MINIO_ROOT_PASSWORD
MINIO_BUCKET="${MINIO_BUCKET:-homayar-private}"
export MINIO_BUCKET

timestamp="$(utc_timestamp)"
backup_dir="${BACKUP_ROOT}/minio/${timestamp}"
if [[ -e "${backup_dir}" ]]; then
  backup_dir="${backup_dir}-$$"
fi
mkdir -p -- "${backup_dir}"
backup_set="$(backup_relative_path "${backup_dir}")"

# mc takes the root keys from MC_HOST_source (export builtin), never as arguments (HOM-340).
# This mc reads MC_HOST_* raw: a ":" in the keys would start a session token, so it is refused.
# A line break or a carriage return (a CRLF env file) would change the keys, so they are refused.
compose --profile onprem --profile ops run --rm --no-deps \
  --env "BACKUP_SET=${backup_set}" \
  minio-tools '
    umask 077
    destination="/backups/$BACKUP_SET"
    nl="
"
    cr=$(printf "\r")
    case "$MINIO_ROOT_USER$MINIO_ROOT_PASSWORD" in
      *:* | *"$nl"* | *"$cr"*)
        echo "backup-minio: MINIO_ROOT_USER and MINIO_ROOT_PASSWORD must not contain \":\", a line break or a carriage return (mc reads them from MC_HOST_source)." >&2
        exit 2 ;;
    esac
    export MC_HOST_source="${MINIO_ENDPOINT%%://*}://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@${MINIO_ENDPOINT#*://}"
    mkdir -p "$destination/objects"
    mc stat "source/$MINIO_BUCKET" >/dev/null
    mc mirror --overwrite --preserve "source/$MINIO_BUCKET" "$destination/objects"
    mc ls --recursive --versions --json "source/$MINIO_BUCKET" > "$destination/inventory.jsonl"
  '

cat > "${backup_dir}/metadata.txt" <<EOF
format=minio-mirror-current
created_at=${timestamp}
bucket=${MINIO_BUCKET}
all_object_versions_included=false
iam_configuration_included=false
EOF
write_checksum "${backup_dir}/inventory.jsonl"
write_checksum "${backup_dir}/metadata.txt"
printf 'complete\n' > "${backup_dir}/.complete"

printf 'MinIO backup completed: %s\n' "${backup_dir}"
