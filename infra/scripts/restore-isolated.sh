#!/usr/bin/env bash
# Disaster recovery into an ISOLATED Compose project (HOM-117/118, ADR 0035).
#
# Brings up only the stores of a fresh Compose project (no proxy, no api),
# restores one backup set into "<POSTGRES_DB>_restoring", reconciles it
# (tombstone replay, tenant/RLS and object checks) and only on PASS promotes
# it to POSTGRES_DB. With --start-app it then starts api, worker and
# scheduler WITHOUT ingress and waits for readiness; opening ingress (proxy
# and web services) stays a separate operator step after the smoke tests.
#
# Every phase is written with UTC timestamps to
# $BACKUP_ROOT/restores/<id>/timeline.log (drill evidence, HOM-118).
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/compose.sh
source "${SCRIPT_DIR}/lib/compose.sh"

usage() {
  cat <<EOF
Usage: COMPOSE_PROJECT_NAME=homayar-restore BACKUP_ROOT=/secure/homayar-backups \\
       BACKUP_KEY_FILE=/run/secrets/homayar-backup.key TOMBSTONE_JOURNAL_PATH=/replica/tombstones \\
       $(basename -- "$0") --backup ID [--fetch s3://bucket/prefix] [--start-app]
       [--accept-held REF] [--accept-missing REF --accept-missing-digest sha256:<64 hex>]
       [--accept-no-journal REF] [--revoke-share-links] [--resume]

The project must NOT be the live one (LIVE_COMPOSE_PROJECT, default homayar-onprem).
TOMBSTONE_JOURNAL_PATH must be the replicated tombstone journal (a host directory).
Relative paths are resolved against the Compose file's directory, as Compose does.
--accept-missing needs --accept-missing-digest: the missing_set_digest of the objects
check in a previous FAIL report. The owner accepts only that exact set of lost objects.
EOF
}

backup_id=""
fetch=""
start_app=0
accept_missing=""
accept_missing_digest=""
restore_args=()
reconcile_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup) [[ $# -ge 2 ]] || die "--backup requires an id"; backup_id="$2"; shift 2 ;;
    --fetch) [[ $# -ge 2 ]] || die "--fetch requires an s3:// source"; fetch="$2"; shift 2 ;;
    --start-app) start_app=1; shift ;;
    --resume) restore_args+=(--resume); shift ;;
    --revoke-share-links) reconcile_args+=(--revoke-share-links); shift ;;
    --accept-missing)
      [[ $# -ge 2 ]] || die "$1 requires an owner decision reference"
      accept_missing="$2"; shift 2 ;;
    --accept-missing-digest)
      [[ $# -ge 2 ]] || die "$1 requires the missing_set_digest of a FAIL report"
      accept_missing_digest="$2"; shift 2 ;;
    --accept-held|--accept-no-journal)
      [[ $# -ge 2 ]] || die "$1 requires an owner decision reference"
      reconcile_args+=("$1" "$2"); shift 2 ;;
    --help) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

# Lost objects are accepted only for the exact set a FAIL report showed (HOM-215),
# so a first run can never carry this override. Checked before anything starts.
if [[ -n "${accept_missing}${accept_missing_digest}" ]]; then
  [[ "${accept_missing}" =~ [^[:space:]] && "${accept_missing_digest}" =~ ^sha256:[0123456789abcdef]{64}$ ]] ||
    die "--accept-missing REF needs --accept-missing-digest sha256:<64 lowercase hex>. Run without the override first, read missing_set_digest in the objects check of the FAIL report, then pass both."
  reconcile_args+=(--accept-missing "${accept_missing}" --accept-missing-digest "${accept_missing_digest}")
fi

[[ "${backup_id}" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$ ]] || die "--backup must be a backup id"
require_env COMPOSE_PROJECT_NAME
require_env POSTGRES_DB
require_env BACKUP_KEY_FILE
# Relative host paths are read against the Compose file's directory, as Compose
# mounts them (the same as backup.sh), never against the current directory.
BACKUP_ROOT="$(compose_path "${BACKUP_ROOT}")"
BACKUP_KEY_FILE="$(compose_path "${BACKUP_KEY_FILE}")"
export BACKUP_ROOT BACKUP_KEY_FILE
[[ -r "${BACKUP_KEY_FILE}" ]] || die "Backup key file is not readable: ${BACKUP_KEY_FILE}"
compose_env_default BACKUP_DATABASE_URL_FILE
if [[ -n "${BACKUP_DATABASE_URL_FILE:-}" ]]; then
  BACKUP_DATABASE_URL_FILE="$(compose_path "${BACKUP_DATABASE_URL_FILE}")"
  export BACKUP_DATABASE_URL_FILE
  [[ -f "${BACKUP_DATABASE_URL_FILE}" && -r "${BACKUP_DATABASE_URL_FILE}" ]] ||
    die "Backup admin DSN file (BACKUP_DATABASE_URL_FILE) is missing or not a readable file: ${BACKUP_DATABASE_URL_FILE}"
fi
live_project="${LIVE_COMPOSE_PROJECT:-homayar-onprem}"
[[ "${COMPOSE_PROJECT_NAME}" != "${live_project}" ]] ||
  die "Refusing to restore into the live project ${live_project}; use a separate COMPOSE_PROJECT_NAME."
if [[ -n "${TOMBSTONE_JOURNAL_PATH:-}" ]]; then
  TOMBSTONE_JOURNAL_PATH="$(compose_path "${TOMBSTONE_JOURNAL_PATH}")"
  export TOMBSTONE_JOURNAL_PATH
fi
if [[ " ${reconcile_args[*]-} " != *" --accept-no-journal "* ]]; then
  require_env TOMBSTONE_JOURNAL_PATH
  [[ -d "${TOMBSTONE_JOURNAL_PATH}" ]] ||
    die "TOMBSTONE_JOURNAL_PATH must be the replicated journal directory: ${TOMBSTONE_JOURNAL_PATH}"
fi
# An isolated restore never writes the production freshness gauge: its verify
# writes a textfile of its own next to the timeline.
export NODE_EXPORTER_TEXTFILE_DIR="${BACKUP_ROOT}/restores/${backup_id}/metrics"
mkdir -p -- "${NODE_EXPORTER_TEXTFILE_DIR}"
timeline="${BACKUP_ROOT}/restores/${backup_id}/timeline.log"
staging="${POSTGRES_DB}_restoring"

mark() {
  printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" | tee -a "${timeline}" >&2
}

tool() {
  compose --profile onprem --profile ops run --rm --no-deps -T backup-tool "$@"
}

running="$(compose_onprem ps --status running --services)"
if grep -Eq '^(api|worker|scheduler|proxy)$' <<<"${running}"; then
  die "api/worker/scheduler/proxy are running in ${COMPOSE_PROJECT_NAME}; an isolated restore starts without them."
fi

mark restore START
previous=""
for argument in ${reconcile_args[@]+"${reconcile_args[@]}"}; do
  case "${previous}" in
    --accept-*) mark owner_override "${previous}=${argument}" ;;
  esac
  [[ "${argument}" != "--revoke-share-links" ]] || mark owner_override "${argument}"
  previous="${argument}"
done
mark stores START
compose_onprem up -d --wait postgres redis minio
compose_onprem run --rm --no-deps minio-bootstrap
mark stores END

if [[ -n "${fetch}" ]]; then
  # The tool has no route out except the egress proxy (its allowlist must
  # name the off-site host). Without it, fetch on a host that can reach the
  # copy: python -m backend.backup --root "$BACKUP_ROOT" fetch ID s3://...
  [[ -n "${EGRESS_PROXY_URL:-}" ]] ||
    die "--fetch needs the egress proxy (set EGRESS_PROXY_URL and allowlist the off-site host)."
  mark fetch START
  compose --profile egress up -d --wait egress-proxy
  tool fetch "${backup_id}" "${fetch}"
  mark fetch END
fi

mark verify START
# Exit 4: verified; only the restore-local freshness textfile could not be written.
tool verify "${backup_id}" || [[ $? -eq 4 ]]
mark verify END

mark load START
tool restore "${backup_id}" --staging-db "${staging}" ${restore_args[@]+"${restore_args[@]}"}
mark load END

mark reconcile START
if ! tool reconcile "${backup_id}" --staging-db "${staging}" ${reconcile_args[@]+"${reconcile_args[@]}"}; then
  mark reconcile FAILED
  die "Reconciliation did not PASS; see ${BACKUP_ROOT}/restores/${backup_id}/reconciliation-report.json. Nothing was promoted."
fi
mark reconcile END

mark promote START
tool promote "${backup_id}" --staging-db "${staging}" --live-db "${POSTGRES_DB}" --retire-existing
tool gate --live-db "${POSTGRES_DB}"
mark promote END

if [[ "${start_app}" == "1" ]]; then
  mark app START
  compose_onprem up -d --wait --no-deps api worker scheduler
  compose_onprem exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=10).read()"
  mark app READY
fi

mark restore END
printf 'Restore of %s promoted to %s in project %s.\n' "${backup_id}" "${POSTGRES_DB}" "${COMPOSE_PROJECT_NAME}"
printf 'Run the smoke tests, then open ingress: docker compose ... --profile onprem up -d --wait proxy\n'
