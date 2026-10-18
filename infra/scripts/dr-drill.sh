#!/usr/bin/env bash
# Restore drill on a SCRATCH host (HOM-118): measure the full usable-recovery path.
#
#   1. a throwaway "production" project (homayar-drill-prod) is started and seeded,
#      and one conversation of each of two tenants is deleted (journal not empty);
#   2. backup.sh takes a set and copies it off-site (a directory and an S3 MinIO
#      that stands in for another host);
#   3. post-backup changes through the public API (deletions, a revocation, a new
#      upload), and the tombstone journal is replicated;
#   4. the production project is destroyed WITH its volumes, journal and local
#      backup root (only the off-site copies and the journal replica survive);
#   5. from the recovery decision (T0): fetch from off-site, restore-isolated.sh
#      into homayar-drill-restore, open ingress, run the smoke tests through
#      nginx; T_ready = smoke tests passed. RTO = T_ready - T0;
#   6. after T_ready (not timed): restore-gate probes on a second, database-only
#      staging restore of the same set (gate closed while loaded, promote refused
#      without a PASS report and with another database's PASS report);
#   7. after T_ready (not timed): every service log of both projects, the console
#      output and the evidence are scanned for the drill's secrets and the seeded
#      transcript text (evidence/leakage-scan.json; the drill fails on a match).
#
# Evidence (timeline, manifest, reconciliation report, smoke results, summary)
# goes to $DRILL_DIR/evidence. It only ever touches the homayar-drill-* projects.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"
COMPOSE_FILE="${REPO_ROOT}/infra/compose/compose.onprem.yaml"

: "${DRILL_DIR:?Set DRILL_DIR to a new, empty directory}"
: "${API_IMAGE:?Set API_IMAGE to the API image built from this commit}"
PY="${DRILL_PYTHON:-${REPO_ROOT}/Homayar/.venv/bin/python}"
TENANTS="${DRILL_TENANTS:-4}"
SAMPLE_RATE="${DRILL_SAMPLE_RATE:-48000}"
PROD_PORT="${DRILL_PROD_PORT:-18080}"
RESTORE_PORT="${DRILL_RESTORE_PORT:-18081}"
OFFSITE_PORT="${DRILL_OFFSITE_PORT:-19000}"
PROD=homayar-drill-prod
RESTORE=homayar-drill-restore
OFFSITE=homayar-drill-offsite

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ ! -e "${DRILL_DIR}" || -z "$(ls -A "${DRILL_DIR}")" ]] || die "DRILL_DIR must be new or empty"
mkdir -p "${DRILL_DIR}"/{backups,offsite-dir,journal,journal-replica,restore-backups,evidence,logs}
# node_exporter textfile directory of the "production" host: outside BACKUP_ROOT,
# owned by BACKUP_UID:BACKUP_GID (this user), 0755. Set for every backup-tool run:
# the Compose default would bind a directory of the repository.
mkdir -p "${DRILL_DIR}/node-exporter-textfile"
chmod 0755 "${DRILL_DIR}/node-exporter-textfile"
DRILL_DIR="$(cd -- "${DRILL_DIR}" && pwd -P)"
E="${DRILL_DIR}/evidence"
TIMELINE="${E}/drill-timeline.log"
LOGS="${DRILL_DIR}/logs"
# Everything the drill prints is a diagnostic too (scanned in step 7).
exec > >(tee -a "${LOGS}/console.log") 2>&1

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
epoch() { "${PY}" -c 'import time; print(f"{time.time():.3f}")'; }
mark() { printf '%s %s %s %s\n' "$(now)" "$(epoch)" "$1" "$2" | tee -a "${TIMELINE}"; }
rand() { "${PY}" -c 'import secrets; print(secrets.token_hex(24))'; }

# --- environment ----------------------------------------------------------------
pg_password="$(rand)"; redis_password="$(rand)"
cat > "${DRILL_DIR}/drill.env" <<EOF
API_IMAGE=${API_IMAGE}
PUBLIC_WEB_IMAGE=${PUBLIC_WEB_IMAGE:-homayar/public-web:local}
CONSOLE_WEB_IMAGE=${CONSOLE_WEB_IMAGE:-homayar/console-web:local}
POSTGRES_DB=homayar
POSTGRES_USER=homayar
POSTGRES_PASSWORD=${pg_password}
DATABASE_URL=postgresql+psycopg://homayar:${pg_password}@postgres:5432/homayar
REDIS_PASSWORD=${redis_password}
REDIS_URL=redis://:${redis_password}@redis:6379/0
JWT_SECRET=$(rand)
TOKEN_PEPPER=$(rand)
MINIO_ROOT_USER=drill-root
MINIO_ROOT_PASSWORD=$(rand)
STORAGE_ACCESS_KEY=drill-app
STORAGE_SECRET_KEY=$(rand)
BIND_ADDRESS=127.0.0.1
EOF
chmod 0600 "${DRILL_DIR}/drill.env"
# SECURE_COOKIES stays true (production refuses false); the drill client treats
# the loopback origin as a secure context, as browsers do.
BACKUP_UID="$(id -u)"
BACKUP_GID="$(id -g)"
export COMPOSE_ENV_FILE="${DRILL_DIR}/drill.env" BACKUP_UID BACKUP_GID

compose_for() {  # project, then compose arguments
  local project="$1"
  shift
  docker compose --project-name "${project}" --env-file "${DRILL_DIR}/drill.env" --file "${COMPOSE_FILE}" "$@"
}
collect_logs() {  # file prefix, then the compose wrapper (prod / restore_env)
  local prefix="$1" service
  shift
  for service in $("$@" --profile onprem config --services); do
    "$@" --profile onprem logs --no-color --timestamps "${service}" \
      > "${LOGS}/${prefix}-${service}.log" 2>&1 || true
  done
}
wait_healthy() {  # compose wrapper, seconds: every service with a health check is healthy
  local wrapper="$1" deadline pending
  deadline=$(( $(date +%s) + $2 ))
  while :; do
    pending="$("${wrapper}" --profile onprem ps --format '{{.Service}} {{.Health}}' |
      awk '$2 != "" && $2 != "healthy" { printf "%s(%s) ", $1, $2 }')"
    [[ -n "${pending}" ]] || return 0
    (( $(date +%s) < deadline )) || die "not healthy after $2 s: ${pending}"
    sleep 5
  done
}
prod() {
  PUBLIC_ORIGIN="http://127.0.0.1:${PROD_PORT}" CORS_ORIGINS="http://127.0.0.1:${PROD_PORT}" \
  TRUSTED_HOSTS="127.0.0.1,localhost" HTTP_PORT="${PROD_PORT}" \
  TOMBSTONE_JOURNAL_PATH="${DRILL_DIR}/journal" BACKUP_ROOT="${DRILL_DIR}/backups" \
  NODE_EXPORTER_TEXTFILE_DIR="${DRILL_DIR}/node-exporter-textfile" \
    compose_for "${PROD}" "$@"
}

docker run --rm --entrypoint python "${API_IMAGE}" -m backend.backup keygen drill-1 > "${DRILL_DIR}/backup.key"
chmod 0600 "${DRILL_DIR}/backup.key"

# --- off-site stand-in (another "host": own container and volume) ----------------
docker rm -f "${OFFSITE}" >/dev/null 2>&1 || true
offsite_secret="$(rand)"
# The secret goes through an env file, never on a command line (argv is visible).
printf 'MINIO_ROOT_USER=offsite\nMINIO_ROOT_PASSWORD=%s\n' "${offsite_secret}" > "${DRILL_DIR}/offsite.env"
chmod 0600 "${DRILL_DIR}/offsite.env"
docker run -d --name "${OFFSITE}" -p "127.0.0.1:${OFFSITE_PORT}:9000" --env-file "${DRILL_DIR}/offsite.env" \
  quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z server /data >/dev/null
export HOMAYAR_BACKUP_S3_ENDPOINT="http://127.0.0.1:${OFFSITE_PORT}" HOMAYAR_BACKUP_S3_ACCESS_KEY=offsite \
  HOMAYAR_BACKUP_S3_SECRET_KEY="${offsite_secret}" HOMAYAR_BACKUP_S3_REGION=us-east-1
offsite_ready=0
for _ in $(seq 1 60); do
  if (cd "${REPO_ROOT}/Homayar" && "${PY}" -c \
    'from backend.backup.replicate import s3_client; s3_client().create_bucket(Bucket="homayar-offsite")') \
    >/dev/null 2>&1; then
    offsite_ready=1
    break
  fi
  sleep 1
done
[[ "${offsite_ready}" == "1" ]] || die "the off-site MinIO did not accept the bucket"

# --- 1. production ----------------------------------------------------------------
mark prod_up START
prod --profile onprem up -d --wait postgres redis minio
prod --profile onprem run --rm --no-deps minio-bootstrap
prod --profile onprem --profile migrate run --rm --no-deps migrate
# Before T0, not measured: on a loaded host a health check can miss its window
# (public-web start-up, the worker's ~10 s `celery inspect ping`), and `up --wait`
# gives up on the first unhealthy state; wait for every service to report healthy.
prod --profile onprem up -d --wait || wait_healthy prod "${DRILL_PROD_HEALTH_SECONDS:-900}"
mark prod_up END

mark seed START
prod --profile onprem --profile ops run --rm --no-deps -T \
  -e ENVIRONMENT=development -e UPLOAD_QUARANTINE_DIR=/tmp/quarantine -e UPLOAD_DIR=/tmp/legacy \
  -v "${SCRIPT_DIR}/drill:/drill:ro" --entrypoint python backup-tool \
  /drill/seed_tenants.py --tenants "${TENANTS}" --sample-rate "${SAMPLE_RATE}" > "${E}/tenants.json"
mark seed END

# Real tombstones before the backup, so the backup fingerprints a non-empty
# journal and the restore must prove the replica is this deployment's journal.
mark pre_backup_deletions START
"${PY}" "${SCRIPT_DIR}/drill/drill_client.py" predelete --base "http://127.0.0.1:${PROD_PORT}" \
  --tenants "${E}/tenants.json" > "${E}/pre-backup-changes.json"
sleep "${DRILL_PURGE_SETTLE_SECONDS:-30}"  # let the purge jobs of those deletions run
mark pre_backup_deletions END

# --- 2. backup + off-site ---------------------------------------------------------
# A directory off-site target must carry the marker (and lie outside BACKUP_ROOT).
: > "${DRILL_DIR}/offsite-dir/.homayar-offsite"
mark backup START
PUBLIC_ORIGIN="http://127.0.0.1:${PROD_PORT}" CORS_ORIGINS="http://127.0.0.1:${PROD_PORT}" \
TRUSTED_HOSTS="127.0.0.1,localhost" HTTP_PORT="${PROD_PORT}" COMPOSE_PROJECT_NAME="${PROD}" \
TOMBSTONE_JOURNAL_PATH="${DRILL_DIR}/journal" BACKUP_ROOT="${DRILL_DIR}/backups" \
BACKUP_KEY_FILE="${DRILL_DIR}/backup.key" BACKUP_OFFSITE_PATH="${DRILL_DIR}/offsite-dir" \
NODE_EXPORTER_TEXTFILE_DIR="${DRILL_DIR}/node-exporter-textfile" \
BACKUP_TEXTFILE_DIR="${DRILL_DIR}/node-exporter-textfile" \
  "${REPO_ROOT}/infra/scripts/backup.sh" | tee "${E}/backup.out"
mark backup END
backup_id="$(sed -n 's/^  "backup_id": "\([^"]*\)",*$/\1/p' "${E}/backup.out" | head -n 1)"
[[ -n "${backup_id}" ]] || die "no backup id"
cp "${DRILL_DIR}/backups/sets/${backup_id}/manifest.json" "${E}/manifest.json"
mark offsite_s3 START
(cd "${REPO_ROOT}/Homayar" && HOMAYAR_BACKUP_TEXTFILE="${DRILL_DIR}/node-exporter-textfile/homayar_backup.prom" \
  "${PY}" -m backend.backup --root "${DRILL_DIR}/backups" replicate "${backup_id}" s3://homayar-offsite/site-a) \
  > "${E}/offsite-s3.json"
mark offsite_s3 END
cp "${DRILL_DIR}/node-exporter-textfile/homayar_backup.prom" "${E}/homayar_backup.prom"

# --- 3. after the backup ----------------------------------------------------------
sleep "${DRILL_GAP_SECONDS:-60}"
mark post_backup_changes START
"${PY}" "${SCRIPT_DIR}/drill/drill_client.py" mutate --base "http://127.0.0.1:${PROD_PORT}" \
  --tenants "${E}/tenants.json" --pre "${E}/pre-backup-changes.json" > "${E}/changes.json"
mark post_backup_changes END
cp -a "${DRILL_DIR}/journal/." "${DRILL_DIR}/journal-replica/"

# --- 4. disaster ------------------------------------------------------------------
collect_logs prod prod
mark failure START
prod --profile onprem --profile ops --profile migrate down --volumes --remove-orphans
rm -rf "${DRILL_DIR}/journal" "${DRILL_DIR}/backups" "${DRILL_DIR}/node-exporter-textfile"
mark failure END

# --- 5. recovery (timed from T0) ----------------------------------------------------
mark recovery_decision T0
mark fetch START
(cd "${REPO_ROOT}/Homayar" && "${PY}" -m backend.backup --root "${DRILL_DIR}/restore-backups" \
  fetch "${backup_id}" s3://homayar-offsite/site-a) > "${E}/fetch.json"
mark fetch END

mark restore_isolated START
PUBLIC_ORIGIN="http://127.0.0.1:${RESTORE_PORT}" CORS_ORIGINS="http://127.0.0.1:${RESTORE_PORT}" \
TRUSTED_HOSTS="127.0.0.1,localhost" HTTP_PORT="${RESTORE_PORT}" COMPOSE_PROJECT_NAME="${RESTORE}" \
LIVE_COMPOSE_PROJECT="${PROD}" TOMBSTONE_JOURNAL_PATH="${DRILL_DIR}/journal-replica" \
BACKUP_ROOT="${DRILL_DIR}/restore-backups" BACKUP_KEY_FILE="${DRILL_DIR}/backup.key" \
POSTGRES_DB=homayar \
  "${REPO_ROOT}/infra/scripts/restore-isolated.sh" --backup "${backup_id}" --start-app \
  2>&1 | tee "${E}/restore-isolated.out"
mark restore_isolated END

mark ingress START
PUBLIC_ORIGIN="http://127.0.0.1:${RESTORE_PORT}" CORS_ORIGINS="http://127.0.0.1:${RESTORE_PORT}" \
TRUSTED_HOSTS="127.0.0.1,localhost" HTTP_PORT="${RESTORE_PORT}" \
TOMBSTONE_JOURNAL_PATH="${DRILL_DIR}/journal-replica" BACKUP_ROOT="${DRILL_DIR}/restore-backups" \
  compose_for "${RESTORE}" --profile onprem up -d --wait proxy
mark ingress END

mark smoke START
"${PY}" "${SCRIPT_DIR}/drill/drill_client.py" smoke --base "http://127.0.0.1:${RESTORE_PORT}" \
  --tenants "${E}/tenants.json" --changes "${E}/changes.json" > "${E}/smoke.json"
mark smoke END
mark recovery READY

# --- 6. restore-gate probes (after READY, not part of the RTO) ---------------------
# A second staging restore of the same set (database only) proves the gate on
# this host: a loaded staging database is closed, promote is refused without a
# PASS report and with another database's PASS report; the live one is open.
restore_env() {
  PUBLIC_ORIGIN="http://127.0.0.1:${RESTORE_PORT}" CORS_ORIGINS="http://127.0.0.1:${RESTORE_PORT}" \
  TRUSTED_HOSTS="127.0.0.1,localhost" HTTP_PORT="${RESTORE_PORT}" \
  TOMBSTONE_JOURNAL_PATH="${DRILL_DIR}/journal-replica" BACKUP_ROOT="${DRILL_DIR}/restore-backups" \
  BACKUP_KEY_FILE="${DRILL_DIR}/backup.key" \
  NODE_EXPORTER_TEXTFILE_DIR="${DRILL_DIR}/restore-backups/restores/${backup_id}/metrics" \
    compose_for "${RESTORE}" "$@"
}
probe() {  # name, expected exit code, backup-tool arguments
  local name="$1" expected="$2" rc=0
  shift 2
  restore_env --profile onprem --profile ops run --rm --no-deps -T backup-tool "$@" \
    > "${DRILL_DIR}/probe.out" 2>> "${DRILL_DIR}/gate-probes.stderr" || rc=$?
  "${PY}" -c '
import json, sys
name, expected, rc, command, path = sys.argv[1:]
text = open(path).read()
try:
    output = json.loads(text)
except ValueError:
    output = text[-2000:]
if isinstance(output, dict) and "database" in output and "phases" in output:
    output = {k: output[k] for k in ("backup_id", "staging_database", "status", "storage_target")}
print(json.dumps({"probe": name, "command": command, "expected_exit": int(expected),
                  "exit": int(rc), "ok": int(rc) == int(expected), "output": output}))
' "${name}" "${expected}" "${rc}" "$*" "${DRILL_DIR}/probe.out" | tee -a "${E}/gate-probes.jsonl"
}
probe_work="/backups/restores/${backup_id}-gate-probe"
mark gate_probes START
probe staging_restore_db_only 0 restore "${backup_id}" --staging-db homayar_probe_restoring --no-objects \
  --work-dir "${probe_work}"
probe gate_closed_before_reconcile 1 gate --live-db homayar_probe_restoring
probe promote_refused_without_report 1 promote "${backup_id}" --staging-db homayar_probe_restoring \
  --live-db homayar_probe_live --work-dir "${probe_work}"
probe promote_refused_with_other_databases_pass 1 promote "${backup_id}" \
  --staging-db homayar_probe_restoring --live-db homayar_probe_live
probe live_gate_open_after_promote 0 gate --live-db homayar
restore_env --profile onprem exec -T postgres sh -ec \
  'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS homayar_probe_restoring WITH (FORCE)"'
mark gate_probes END

cp "${DRILL_DIR}/restore-backups/restores/${backup_id}/reconciliation-report.json" "${E}/"
cp "${DRILL_DIR}/restore-backups/restores/${backup_id}/restore-state.json" "${E}/"
cp "${DRILL_DIR}/restore-backups/restores/${backup_id}/timeline.log" "${E}/restore-timeline.log"
"${PY}" "${SCRIPT_DIR}/drill/summarize.py" "${E}" > "${E}/summary.json"
cat "${E}/summary.json"

# --- 7. diagnostics leakage scan (after READY, not timed) ---------------------------
collect_logs restore restore_env
docker logs "${OFFSITE}" > "${LOGS}/offsite-minio.log" 2>&1 || true
mark leakage_scan START
leak_rc=0
"${PY}" "${SCRIPT_DIR}/drill/leak_scan.py" --root "${DRILL_DIR}" --env drill.env --key backup.key \
  --demo-seed "${REPO_ROOT}/Homayar/backend/database/demo_seed.py" \
  --exclude drill.env backup.key offsite.env evidence/tenants.json --out evidence/leakage-scan.json \
  logs evidence restore-backups/restores restore-backups/metrics gate-probes.stderr probe.out ||
  leak_rc=$?
mark leakage_scan END
[[ "${leak_rc}" == "0" ]] || die "the diagnostics leakage scan matched; see ${E}/leakage-scan.json"
