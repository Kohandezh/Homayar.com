#!/usr/bin/env bash
# Disposable MinIO target for the storage contract suite (HOM-36, ADR 0019).
#
#   minio-contract-target.sh up <host-port> <env-file>
#   minio-contract-target.sh down <host-port>
#
# `up` starts one container, homayar-minio-contract-<host-port>, published only on
# 127.0.0.1:<host-port>. It bootstraps the bucket like minio-bootstrap in
# infra/compose/compose.onprem.yaml (versioning on, anonymous access none, the
# homayar-app policy and an app user), adds a user with no policy ("denied") and a
# user whose policy covers only a bucket that is never created ("probe"), checks
# versioning and anonymous access with the root key, and writes the env file that
# backend/tests/_minio_s3.py reads. Every credential is random and new on each `up`.
# The root credentials stay inside this script. No credential value is printed.
#
# `down` removes that container and its anonymous volumes. It is safe to run
# twice and removes nothing else: `--volumes` only drops that container's
# anonymous volumes, never named volumes.
set -Eeuo pipefail

# The official quay.io/minio images need a login and the Docker Hub copies are gone.
# The on-prem Compose file ships these same two references as its defaults (HOM-286),
# and test_release_manifest.py fails when they differ; the digests pin them.
MINIO_IMAGE="bitnamilegacy/minio:2025.4.22@sha256:50cec18ac4184af4671a78aedd5554942c8ae105d51a465fa82037949046da01"
MC_IMAGE="bitnamilegacy/minio-client:2025.4.16@sha256:8a86e441decf053093c5977d49290298295f850c7843652f93e970ab15871dd1"
BUCKET="homayar-private"
HEALTH_TIMEOUT_SECONDS=60

usage() {
  printf 'usage: %s up <host-port> <env-file>\n       %s down <host-port>\n' "$0" "$0" >&2
  exit 2
}

container_name() {
  printf 'homayar-minio-contract-%s' "$1"
}

check_port() {
  if [[ ! "$1" =~ ^[0-9]+$ ]] || (("$1" < 1 || "$1" > 65535)); then
    printf 'ERROR: host port must be a number from 1 to 65535, got %s\n' "$1" >&2
    exit 2
  fi
}

# Same statements as the homayar-app policy of minio-bootstrap in
# infra/compose/compose.onprem.yaml; test_storage_contract.py fails when they differ.
app_policy() {
  printf '%s\n' \
    '{"Version":"2012-10-17","Statement":[' \
    '{"Effect":"Allow","Action":["s3:GetBucketLocation","s3:ListBucket","s3:ListBucketMultipartUploads"],"Resource":["arn:aws:s3:::'"$1"'"]},' \
    '{"Effect":"Allow","Action":["s3:AbortMultipartUpload","s3:DeleteObject","s3:GetObject","s3:ListMultipartUploadParts","s3:PutObject"],"Resource":["arn:aws:s3:::'"$1"'/*"]}' \
    ']}'
}

# Rights only on <bucket>-missing, which is never created, so the probe user sees
# NoSuchBucket instead of AccessDenied.
probe_policy() {
  printf '%s\n' \
    '{"Version":"2012-10-17","Statement":[' \
    '{"Effect":"Allow","Action":["s3:ListBucket"],"Resource":["arn:aws:s3:::'"$1"'-missing"]},' \
    '{"Effect":"Allow","Action":["s3:DeleteObject","s3:GetObject","s3:PutObject"],"Resource":["arn:aws:s3:::'"$1"'-missing/*"]}' \
    ']}'
}

# new_secret <variable> <bytes>: sets <variable> in this shell (no subshell, so the
# mask command reaches the GitHub log before the value is used anywhere).
new_secret() {
  local value
  value="$(openssl rand -hex "$2")"
  if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
    printf '::add-mask::%s\n' "${value}"
  fi
  printf -v "$1" '%s' "${value}"
}

# State for the EXIT trap of `up`.
UP_WORK=""
UP_CONTAINER=""

cleanup_up() {
  local status="$1"
  if [[ -n "${UP_WORK}" ]]; then
    rm -rf -- "${UP_WORK}"
  fi
  # On any failure, leave no container behind.
  if [[ "${status}" -ne 0 && -n "${UP_CONTAINER}" ]]; then
    docker rm --force --volumes "${UP_CONTAINER}" >/dev/null 2>&1 || true
  fi
}

# The Bitnami image first runs a temporary MinIO for its own setup, stops it, logs
# "** MinIO setup finished! **", then logs "** Starting MinIO **" (once) and starts the
# final server. A health answer before that line may come from the temporary server,
# and an mc call in the gap after it stops fails. So wait for the line first, then for
# health: any answer after the line comes from the final server.
wait_for_final_start() {
  local name="$1" port="$2" deadline=$((SECONDS + HEALTH_TIMEOUT_SECONDS)) phase="the final start"
  while :; do
    if [[ "$(docker inspect --format '{{.State.Running}}' "${name}" 2>/dev/null)" != "true" ]]; then
      printf 'ERROR: container %s stopped before MinIO was ready\n' "${name}" >&2
      exit 1
    fi
    # grep -c reads the whole log (no SIGPIPE under pipefail); the log is never printed.
    if [[ "${phase}" == "the final start" ]] &&
      docker logs "${name}" 2>&1 | grep -cF '** Starting MinIO **' >/dev/null; then
      phase="health after the final start"
    fi
    if [[ "${phase}" != "the final start" ]] &&
      curl -fsS --noproxy '*' --max-time 2 "http://127.0.0.1:${port}/minio/health/live" >/dev/null 2>&1; then
      return 0
    fi
    if ((SECONDS >= deadline)); then
      printf 'ERROR: MinIO on 127.0.0.1:%s: still waiting for %s after %s seconds\n' "${port}" "${phase}" "${HEALTH_TIMEOUT_SECONDS}" >&2
      exit 1
    fi
    sleep 1
  done
}

# Runs inside the mc container. Values arrive by environment name only. Steps that
# carry a user credential discard mc's output, because mc echoes access keys.
read -r -d '' BOOTSTRAP <<'SH' || true
step() {
  description="$1"
  shift
  if ! "$@" >/tmp/step.out 2>&1; then
    printf 'ERROR: MinIO bootstrap failed at: %s\n' "${description}" >&2
    if [ "${SHOW_OUTPUT}" = yes ]; then cat /tmp/step.out >&2; fi
    exit 1
  fi
}
SHOW_OUTPUT=yes
step "create the bucket" mc mb --ignore-existing "local/${MINIO_BUCKET}"
step "enable versioning" mc version enable "local/${MINIO_BUCKET}"
step "disable anonymous access" mc anonymous set none "local/${MINIO_BUCKET}"
step "create the homayar-app policy" mc admin policy create local homayar-app /work/homayar-app-policy.json
step "create the probe policy" mc admin policy create local homayar-contract-probe /work/probe-policy.json
SHOW_OUTPUT=no
step "add the app user" mc admin user add local "${APP_ACCESS_KEY}" "${APP_SECRET_KEY}"
step "attach homayar-app to the app user" mc admin policy attach local homayar-app --user "${APP_ACCESS_KEY}"
step "add the denied user" mc admin user add local "${DENIED_ACCESS_KEY}" "${DENIED_SECRET_KEY}"
step "add the probe user" mc admin user add local "${PROBE_ACCESS_KEY}" "${PROBE_SECRET_KEY}"
step "attach the probe policy" mc admin policy attach local homayar-contract-probe --user "${PROBE_ACCESS_KEY}"
SHOW_OUTPUT=yes
step "read versioning" mc version info --json "local/${MINIO_BUCKET}"
if ! grep -q '"status": *"Enabled"' /tmp/step.out; then
  printf 'ERROR: versioning is not Enabled on %s\n' "${MINIO_BUCKET}" >&2
  cat /tmp/step.out >&2
  exit 1
fi
step "read anonymous access" mc anonymous get --json "local/${MINIO_BUCKET}"
if ! grep -q '"permission": *"private"' /tmp/step.out; then
  printf 'ERROR: anonymous access is not none on %s\n' "${MINIO_BUCKET}" >&2
  cat /tmp/step.out >&2
  exit 1
fi
SH

up() {
  local port="$1" env_file="$2" name
  check_port "${port}"
  name="$(container_name "${port}")"
  if [[ -n "$(docker ps -aq --filter "name=^${name}$")" ]]; then
    printf 'ERROR: container %s already exists; run "%s down %s" first\n' "${name}" "$0" "${port}" >&2
    exit 1
  fi

  local root_user root_password
  local app_access_key app_secret_key denied_access_key denied_secret_key
  local probe_access_key probe_secret_key
  new_secret root_user 10
  new_secret root_password 20
  new_secret app_access_key 10
  new_secret app_secret_key 20
  new_secret denied_access_key 10
  new_secret denied_secret_key 20
  new_secret probe_access_key 10
  new_secret probe_secret_key 20

  trap 'cleanup_up $?' EXIT
  UP_WORK="$(mktemp -d)"
  # The policies are not secret; the mc container (uid 1001) reads them.
  app_policy "${BUCKET}" >"${UP_WORK}/homayar-app-policy.json"
  probe_policy "${BUCKET}" >"${UP_WORK}/probe-policy.json"
  chmod 0755 "${UP_WORK}"
  chmod 0644 "${UP_WORK}"/*.json

  MINIO_ROOT_USER="${root_user}" MINIO_ROOT_PASSWORD="${root_password}" \
    docker run --detach --rm --name "${name}" \
    --publish "127.0.0.1:${port}:9000" \
    --env MINIO_ROOT_USER --env MINIO_ROOT_PASSWORD \
    "${MINIO_IMAGE}" >/dev/null
  UP_CONTAINER="${name}"

  wait_for_final_start "${name}" "${port}"

  MC_HOST_local="http://${root_user}:${root_password}@127.0.0.1:9000" \
    MINIO_BUCKET="${BUCKET}" \
    APP_ACCESS_KEY="${app_access_key}" APP_SECRET_KEY="${app_secret_key}" \
    DENIED_ACCESS_KEY="${denied_access_key}" DENIED_SECRET_KEY="${denied_secret_key}" \
    PROBE_ACCESS_KEY="${probe_access_key}" PROBE_SECRET_KEY="${probe_secret_key}" \
    docker run --rm --network "container:${name}" \
    --env MC_HOST_local --env MC_CONFIG_DIR=/tmp/.mc --env MINIO_BUCKET \
    --env APP_ACCESS_KEY --env APP_SECRET_KEY \
    --env DENIED_ACCESS_KEY --env DENIED_SECRET_KEY \
    --env PROBE_ACCESS_KEY --env PROBE_SECRET_KEY \
    --volume "${UP_WORK}:/work:ro" \
    --entrypoint /bin/sh \
    "${MC_IMAGE}" -ec "${BOOTSTRAP}"

  (
    umask 077
    {
      printf 'HOMAYAR_MINIO_TEST=1\n'
      printf 'HOMAYAR_MINIO_ENDPOINT_URL=http://127.0.0.1:%s\n' "${port}"
      printf 'HOMAYAR_MINIO_BUCKET=%s\n' "${BUCKET}"
      printf 'HOMAYAR_MINIO_ACCESS_KEY=%s\n' "${app_access_key}"
      printf 'HOMAYAR_MINIO_SECRET_KEY=%s\n' "${app_secret_key}"
      printf 'HOMAYAR_MINIO_DENIED_ACCESS_KEY=%s\n' "${denied_access_key}"
      printf 'HOMAYAR_MINIO_DENIED_SECRET_KEY=%s\n' "${denied_secret_key}"
      printf 'HOMAYAR_MINIO_PROBE_ACCESS_KEY=%s\n' "${probe_access_key}"
      printf 'HOMAYAR_MINIO_PROBE_SECRET_KEY=%s\n' "${probe_secret_key}"
    } >"${env_file}"
  )
  chmod 0600 "${env_file}"
  printf 'MinIO contract target %s is live on http://127.0.0.1:%s (bucket %s); env file written to %s\n' \
    "${name}" "${port}" "${BUCKET}" "${env_file}"
}

down() {
  local port="$1" name
  check_port "${port}"
  name="$(container_name "${port}")"
  docker rm --force --volumes "${name}" >/dev/null 2>&1 || true
  if [[ -n "$(docker ps -aq --filter "name=^${name}$")" ]]; then
    printf 'ERROR: container %s is still present\n' "${name}" >&2
    exit 1
  fi
  printf 'MinIO contract target %s removed\n' "${name}"
}

case "${1:-}" in
  up)
    [[ $# -eq 3 ]] || usage
    up "$2" "$3"
    ;;
  down)
    [[ $# -eq 2 ]] || usage
    down "$2"
    ;;
  *)
    usage
    ;;
esac
