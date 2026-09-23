#!/usr/bin/env bash
# Reapply journaled deletion tombstones after a restore (WP07-T04, ADR 0022).
#
# Run after restore-postgres.sh (and restore-minio.sh) and the forward
# migrations, with api and worker stopped and postgres/minio running. Exits
# non-zero unless every journaled conversation is tombstoned again and the
# journal is intact; only then may api and worker be started.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/compose.sh
source "${SCRIPT_DIR}/lib/compose.sh"

reapply_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --remove-unknown-keys)
      # Also remove journaled object keys of conversations the restored
      # database does not know (never keys referenced by a live row).
      reapply_args+=(--remove-unknown-keys)
      shift
      ;;
    --help)
      printf 'Usage: %s [--remove-unknown-keys]\n' "$(basename -- "$0")"
      exit 0
      ;;
    *) die "Unknown argument: $1" ;;
  esac
done

require_writers_stopped

restore_compose() {
  compose_onprem --profile restore "$@"
}

restore_compose run --rm --no-deps tombstone-reapply reapply ${reapply_args[@]+"${reapply_args[@]}"} ||
  die "Tombstone reapply failed or the journal contains untrustworthy lines; do not open traffic."
restore_compose run --rm --no-deps tombstone-reapply verify ||
  die "A journaled conversation is still alive after reapply; do not open traffic."

printf 'Tombstones reapplied and verified; api and worker may be started.\n'
