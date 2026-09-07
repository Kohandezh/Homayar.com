#!/usr/bin/env bash

if [[ -n "${HOMAYAR_COMPOSE_LIB_LOADED:-}" ]]; then
  return 0
fi
HOMAYAR_COMPOSE_LIB_LOADED=1

LIB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
INFRA_ROOT="$(cd -- "${LIB_DIR}/../.." && pwd -P)"
COMPOSE_FILE="${COMPOSE_FILE:-${INFRA_ROOT}/compose/compose.onprem.yaml}"
BACKUP_ROOT="${BACKUP_ROOT:-${INFRA_ROOT}/backups}"
BACKUP_UID="${BACKUP_UID:-$(id -u)}"
BACKUP_GID="${BACKUP_GID:-$(id -g)}"
export BACKUP_ROOT BACKUP_UID BACKUP_GID

compose_args=(--file "${COMPOSE_FILE}")
if [[ -n "${COMPOSE_ENV_FILE:-}" ]]; then
  [[ -r "${COMPOSE_ENV_FILE}" ]] || {
    printf 'Environment file is not readable: %s\n' "${COMPOSE_ENV_FILE}" >&2
    exit 2
  }
  compose_args=(--env-file "${COMPOSE_ENV_FILE}" "${compose_args[@]}")
fi
if [[ -n "${COMPOSE_PROJECT_NAME:-}" ]]; then
  compose_args=(--project-name "${COMPOSE_PROJECT_NAME}" "${compose_args[@]}")
fi

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

require_env() {
  local name="$1"
  [[ -n "${!name:-}" ]] || die "Required environment variable is empty: ${name}"
}

compose() {
  docker compose "${compose_args[@]}" "$@"
}

compose_onprem() {
  compose --profile onprem "$@"
}

utc_timestamp() {
  date -u +%Y%m%dT%H%M%SZ
}

file_sha256() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${file}" | awk '{print $1}'
  else
    shasum -a 256 "${file}" | awk '{print $1}'
  fi
}

write_checksum() {
  local file="$1"
  printf '%s  %s\n' "$(file_sha256 "${file}")" "$(basename -- "${file}")" > "${file}.sha256"
}

verify_checksum() {
  local file="$1"
  local checksum_file="${file}.sha256"
  local expected actual

  if [[ ! -r "${checksum_file}" ]]; then
    [[ "${ALLOW_UNVERIFIED_BACKUP:-0}" == "1" ]] ||
      die "Checksum is missing: ${checksum_file}. Set ALLOW_UNVERIFIED_BACKUP=1 only for an independently verified legacy backup."
    return 0
  fi

  expected="$(awk 'NR == 1 {print $1}' "${checksum_file}")"
  actual="$(file_sha256 "${file}")"
  [[ -n "${expected}" && "${expected}" == "${actual}" ]] ||
    die "Checksum mismatch for ${file}"
}

metadata_value() {
  local file="$1"
  local key="$2"
  awk -F= -v expected_key="${key}" '$1 == expected_key { sub(/^[^=]*=/, ""); print; exit }' "${file}"
}

canonical_existing_path() {
  local path="$1"
  local directory basename
  [[ -e "${path}" ]] || die "Path does not exist: ${path}"
  directory="$(cd -- "$(dirname -- "${path}")" && pwd -P)"
  basename="$(basename -- "${path}")"
  printf '%s/%s\n' "${directory}" "${basename}"
}

backup_relative_path() {
  local path root
  path="$(canonical_existing_path "$1")"
  root="$(canonical_existing_path "${BACKUP_ROOT}")"
  case "${path}" in
    "${root}"/*) printf '%s\n' "${path#"${root}"/}" ;;
    *) die "Backup path must be below BACKUP_ROOT (${root}): ${path}" ;;
  esac
}

require_writers_stopped() {
  local running
  running="$(compose_onprem ps --status running --services)"
  if grep -Eq '^(api|worker)$' <<<"${running}"; then
    die "Stop the api and worker services before restoring to prevent concurrent writes."
  fi
}

require_command docker
mkdir -p -- "${BACKUP_ROOT}"
