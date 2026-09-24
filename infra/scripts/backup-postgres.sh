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

require_env POSTGRES_DB
require_env POSTGRES_USER

timestamp="$(utc_timestamp)"
backup_dir="${BACKUP_ROOT}/postgres/${timestamp}"
if [[ -e "${backup_dir}" ]]; then
  backup_dir="${backup_dir}-$$"
fi
mkdir -p -- "${backup_dir}"
dump_file="${backup_dir}/database.dump"
partial_file="${dump_file}.partial"

cleanup() {
  rm -f -- "${partial_file}"
}
trap cleanup EXIT

compose_onprem exec -T postgres sh -ec '
  export PGPASSWORD="$POSTGRES_PASSWORD"
  exec pg_dump \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --format custom \
    --compress 9 \
    --no-owner \
    --no-privileges
' > "${partial_file}"
mv -- "${partial_file}" "${dump_file}"

compose_onprem exec -T postgres pg_restore --list < "${dump_file}" > "${backup_dir}/table-of-contents.txt"
postgres_version="$(compose_onprem exec -T postgres postgres --version | tr -d '\r')"
cat > "${backup_dir}/metadata.txt" <<EOF
format=postgres-custom
created_at=${timestamp}
database=${POSTGRES_DB}
postgres_version=${postgres_version}
roles_included=false
EOF
write_checksum "${dump_file}"
write_checksum "${backup_dir}/table-of-contents.txt"
write_checksum "${backup_dir}/metadata.txt"
printf 'complete\n' > "${backup_dir}/.complete"
trap - EXIT

printf 'PostgreSQL backup completed: %s\n' "${backup_dir}"
