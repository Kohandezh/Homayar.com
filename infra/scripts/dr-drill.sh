#!/usr/bin/env bash
# Restore drill on a SCRATCH host (HOM-118): measure the full usable-recovery path.
#
#   1. a throwaway "production" project (homayar-drill-prod) is started and seeded;
#   2. backup.sh takes a set and copies it off-site (a directory and an S3 MinIO
#      that stands in for another host);
#   3. post-backup changes through the public API (deletions, a revocation, a new
#      upload), and the tombstone journal is replicated;
#   4. the production project is destroyed WITH its volumes, journal and local
#      backup root (only the off-site copies and the journal replica survive);
#   5. from the recovery decision (T0): fetch from off-site, restore-isolated.sh
#      into homayar-drill-restore, open ingress, run the smoke tests through
#      nginx; T_ready = smoke tests passed. RTO = T_ready - T0.
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
mkdir -p "${DRILL_DIR}"/{backups,offsite-dir,journal,journal-replica,restore-backups,evidence}
DRILL_DIR="$(cd -- "${DRILL_DIR}" && pwd -P)"
E="${DRILL_DIR}/evidence"
TIMELINE="${E}/drill-timeline.log"

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
epoch() { "${PY}" -c 'import time; print(f"{time.time():.3f}")'; }
mark() { printf '%s %s %s %s\n' "$(now)" "$(epoch)" "$1" "$2" | tee -a "${TIMELINE}"; }
rand() { "${PY}" -c 'import secrets; print(secrets.token_hex(24))'; }

# --- environment ----------------------------------------------------------------
pg_password="$(rand)"; redis_password="$(rand)"
cat > "${DRILL_DIR}/drill.env" <<EOF
API_IMAGE=${API_IMAGE}
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
SECURE_COOKIES=false
EOF
chmod 0600 "${DRILL_DIR}/drill.env"
export COMPOSE_ENV_FILE="${DRILL_DIR}/drill.env" BACKUP_UID="$(id -u)" BACKUP_GID="$(id -g)"

compose_for() {  # project, then compose arguments
  local project="$1"
  shift
  docker compose --project-name "${project}" --env-file "${DRILL_DIR}/drill.env" --file "${COMPOSE_FILE}" "$@"
}
prod() {
  PUBLIC_ORIGIN="http://127.0.0.1:${PROD_PORT}" CORS_ORIGINS="http://127.0.0.1:${PROD_PORT}" \
  TRUSTED_HOSTS="127.0.0.1,localhost" HTTP_PORT="${PROD_PORT}" \
  TOMBSTONE_JOURNAL_PATH="${DRILL_DIR}/journal" BACKUP_ROOT="${DRILL_DIR}/backups" \
    compose_for "${PROD}" "$@"
}

docker run --rm --entrypoint python "${API_IMAGE}" -m backend.backup keygen drill-1 > "${DRILL_DIR}/backup.key"
chmod 0600 "${DRILL_DIR}/backup.key"

# --- off-site stand-in (another "host": own container and volume) ----------------
docker rm -f "${OFFSITE}" >/dev/null 2>&1 || true
offsite_secret="$(rand)"
docker run -d --name "${OFFSITE}" -p "127.0.0.1:${OFFSITE_PORT}:9000" \
  -e MINIO_ROOT_USER=offsite -e MINIO_ROOT_PASSWORD="${offsite_secret}" \
  quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z server /data >/dev/null
export HOMAYAR_BACKUP_S3_ENDPOINT="http://127.0.0.1:${OFFSITE_PORT}" HOMAYAR_BACKUP_S3_ACCESS_KEY=offsite \
  HOMAYAR_BACKUP_S3_SECRET_KEY="${offsite_secret}" HOMAYAR_BACKUP_S3_REGION=us-east-1
for _ in $(seq 1 60); do
  "${PY}" -c 'from backend.backup.replicate import s3_client; s3_client().create_bucket(Bucket="homayar-offsite")' \
    >/dev/null 2>&1 && break
  sleep 1
done

# --- 1. production ----------------------------------------------------------------
mark prod_up START
prod --profile onprem up -d --wait postgres redis minio
prod --profile onprem run --rm --no-deps minio-bootstrap
prod --profile migrate run --rm --no-deps migrate
prod --profile onprem up -d --wait
mark prod_up END

mark seed START
prod --profile onprem --profile ops run --rm --no-deps -T \
  -e ENVIRONMENT=development -e UPLOAD_QUARANTINE_DIR=/tmp/quarantine -e UPLOAD_DIR=/tmp/legacy \
  -v "${SCRIPT_DIR}/drill:/drill:ro" --entrypoint python backup-tool \
  /drill/seed_tenants.py --tenants "${TENANTS}" --sample-rate "${SAMPLE_RATE}" > "${E}/tenants.json"
mark seed END

# --- 2. backup + off-site ---------------------------------------------------------
mark backup START
PUBLIC_ORIGIN="http://127.0.0.1:${PROD_PORT}" CORS_ORIGINS="http://127.0.0.1:${PROD_PORT}" \
TRUSTED_HOSTS="127.0.0.1,localhost" HTTP_PORT="${PROD_PORT}" COMPOSE_PROJECT_NAME="${PROD}" \
TOMBSTONE_JOURNAL_PATH="${DRILL_DIR}/journal" BACKUP_ROOT="${DRILL_DIR}/backups" \
BACKUP_KEY_FILE="${DRILL_DIR}/backup.key" BACKUP_OFFSITE_PATH="${DRILL_DIR}/offsite-dir" \
  "${REPO_ROOT}/infra/scripts/backup.sh" | tee "${E}/backup.out"
mark backup END
backup_id="$(sed -n 's/^  "backup_id": "\([^"]*\)",*$/\1/p' "${E}/backup.out" | head -n 1)"
[[ -n "${backup_id}" ]] || die "no backup id"
cp "${DRILL_DIR}/backups/sets/${backup_id}/manifest.json" "${E}/manifest.json"
mark offsite_s3 START
(cd "${REPO_ROOT}/Homayar" && HOMAYAR_BACKUP_TEXTFILE="${DRILL_DIR}/backups/metrics/homayar_backup.prom" \
  "${PY}" -m backend.backup --root "${DRILL_DIR}/backups" replicate "${backup_id}" s3://homayar-offsite/site-a) \
  > "${E}/offsite-s3.json"
mark offsite_s3 END
cp "${DRILL_DIR}/backups/metrics/homayar_backup.prom" "${E}/homayar_backup.prom"

# --- 3. after the backup ----------------------------------------------------------
sleep "${DRILL_GAP_SECONDS:-60}"
mark post_backup_changes START
"${PY}" "${SCRIPT_DIR}/drill/drill_client.py" mutate --base "http://127.0.0.1:${PROD_PORT}" \
  --tenants "${E}/tenants.json" > "${E}/changes.json"
mark post_backup_changes END
cp -a "${DRILL_DIR}/journal/." "${DRILL_DIR}/journal-replica/"

# --- 4. disaster ------------------------------------------------------------------
mark failure START
prod --profile onprem --profile ops --profile migrate down --volumes --remove-orphans
rm -rf "${DRILL_DIR}/journal" "${DRILL_DIR}/backups"
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

cp "${DRILL_DIR}/restore-backups/restores/${backup_id}/reconciliation-report.json" "${E}/"
cp "${DRILL_DIR}/restore-backups/restores/${backup_id}/restore-state.json" "${E}/"
cp "${DRILL_DIR}/restore-backups/restores/${backup_id}/timeline.log" "${E}/restore-timeline.log"
"${PY}" "${SCRIPT_DIR}/drill/summarize.py" "${E}" > "${E}/summary.json"
cat "${E}/summary.json"
