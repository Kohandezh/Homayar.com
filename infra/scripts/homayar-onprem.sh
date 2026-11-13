#!/usr/bin/env bash
# HOM-110: one operator command for an on-prem (customer) install: install,
# upgrade with automatic rollback, manual rollback, backup, restore, a
# sanitized support bundle and status. ADR 0064; the runbook is
# docs/operations/onprem-lifecycle.md.
#
# Run on the install host, never with sudo, with stdin from /dev/null:
#   bash homayar-onprem.sh install  --package /path/to/homayar-<version>  </dev/null
#   bash homayar-onprem.sh upgrade  --package /path/to/homayar-<version>  </dev/null
#   bash homayar-onprem.sh rollback --to <state-id> --accept-data-loss-since <UTC ts>
#   bash homayar-onprem.sh preflight [--offline]
#   bash homayar-onprem.sh backup | restore | status
#   bash homayar-onprem.sh support-bundle --output /tmp/homayar-bundle.tar.gz
#   bash homayar-onprem.sh pack --source <checkout> --output <dir> \
#     --version <v> --api-image R --console-web-image R --egress-proxy-image R
#
# Install layout under HOMAYAR_HOME (default /opt/homayar):
#   releases/<version>/   one extracted package per version
#   current -> releases/<version>   relative symlink, switched atomically
#   config/onprem.env     operator settings and secrets (mode 600)
#   state/<state-id>/     upgrade records and pre-migration dumps (mode 700)
#   ops/                  lock and logs
#
# What upgrade does: it verifies the new package, checks the stack health,
# the free disk, that the new compose config renders, and that a verified
# full backup is fresh; extracts the release, loads its images, records the
# state, stops the writers, dumps the database and proves the dump restores;
# then switches current, migrates, starts the stack and checks health and
# identity. Any failure from the switch on rolls back automatically (restore
# the dump when needed, switch current back, restart on the old release). A
# failing rollback exits 3 with the exact manual steps. The database is
# never downgraded with alembic; it is only ever returned by restoring the
# pre-migration dump.
#
# Exit codes: 0 done (or already done); 1 failed and rolled back (data
# restored); 2 refused or failed before any live change; 3 rollback failed,
# manual action needed. Unknown command or bad flag: 2. `preflight` passes
# the model preflight exit code (0/1/2) through.
#
# Needs bash 4.4 or later. ONPREM_TEST_HOME is only for the test harness; on
# an install host it is never set. No value from config/onprem.env ever
# reaches the log or stdout.
set -Eeuo pipefail
export LC_ALL=C
trap '' HUP PIPE

H="${ONPREM_TEST_HOME:-${HOMAYAR_HOME:-/opt/homayar}}"
CONFIG="$H/config/onprem.env"
STATES="$H/state"
OPS="$H/ops"
LOCK="$OPS/homayar-onprem.lock"
RELEASES="$H/releases"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

T_CALL=120 T_DUMP=1800 T_RESTORE=3600 T_MIGRATE=1800 T_UP=900 T_KILL=30
WAIT_SECONDS=300
MIN_DISK_KB_DEFAULT=$((10 * 1024 * 1024)) # 10 GiB
MAX_BACKUP_HOURS_DEFAULT=24
WRITERS=(proxy console-web api worker scheduler)
HEALTH_SERVICES=(proxy console-web api worker postgres redis minio)
LOG_SERVICES=(proxy console-web api worker scheduler postgres redis minio)

TMP="$(mktemp -d)"
LOG=""
STATE="preflight" # preflight | changing | finished (a signal or an unexpected
# exit while changing starts the rollback, like the release script)
HELD_LOCK=""
STATE_ID=""
STOPPED_AT=""
CUR=""    # the current release folder
NEW=""    # the new release folder of the running upgrade
REC=""    # the record folder of the running upgrade
DUMP=""   # the pre-migration dump of the running upgrade
EXTRACT_TMP="" # a release folder this run must remove again unless it
# finishes (the staged temp extract, or the freshly renamed release of a
# failed install); cleanup() removes it on every exit path
RESULTS=()
STACK_ROWS=""
STACK_BAD=""

# --- output and command helpers -------------------------------------------------

say() { # a line to the log file first, then to stdout; a dead stdout is ignored
  if [[ -n "$LOG" ]]; then printf '%s\n' "$*" >>"$LOG" || true; fi
  printf '%s\n' "$*" 2>/dev/null || true
}

show() { # captured output, indented, runs of 20+ hex characters hidden
  [[ -n "$1" ]] || return 0
  say "$(printf '%s\n' "$1" | sed -E 's/[0-9a-f]{20,}/<redacted>/g' | sed 's/^/    | /')"
}

run() { # run <cmd...>: status; output captured in RUN_OUT and shown filtered
  local status=0
  RUN_OUT="$( "$@" 2>&1 </dev/null )" || status=$?
  show "$RUN_OUT"
  return "$status"
}

run_in() { # run_in <stdin-file> <cmd...>: like run, stdin from the file
  local status=0 inf="$1"
  shift
  RUN_OUT="$( "$@" <"$inf" 2>&1 )" || status=$?
  show "$RUN_OUT"
  return "$status"
}

cap() { # cap <cmd...>: like run but silent (values that are only used)
  RUN_OUT=""
  RUN_OUT="$( "$@" </dev/null 2>/dev/null )" || return 1
  return 0
}

now() { date +%s; }
utc() { date -u +%Y%m%dT%H%M%SZ; }

record() { # record <phase> <check> <result> <detail>
  RESULTS+=("$1 $2 $3: $4")
  say "  $2 $3: $4"
}

http_code() { # http_code <seconds> <outfile> <url>: the status, 000 when no answer
  local code
  code="$(curl -sS --max-time "$1" -o "$2" -w '%{http_code}' "$3" </dev/null 2>/dev/null || true)"
  printf '%s' "${code:-000}"
}

disk_free_kb() { # free KiB of the file system of a path, / as the fallback
  local out
  out="$(df -Pk "$1" 2>/dev/null | awk 'NR == 2 { print $4 }')" || true
  [[ -n "$out" ]] || out="$(df -Pk / | awk 'NR == 2 { print $4 }')"
  printf '%s' "$out"
}

json_escape() { # the only JSON this script writes holds operator-safe facts;
  # newlines (the nvidia-smi lines) become \n escapes
  sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e ':a' -e 'N' -e '$!ba' -e 's/\n/\\n/g' <<<"$1"
}

# --- lock and finish --------------------------------------------------------------

acquire_lock() {
  if ! (umask 077 && mkdir "$LOCK") 2>/dev/null; then
    say "REFUSED: another run holds $LOCK."
    say "Nothing was changed."
    exit 2
  fi
  HELD_LOCK=1
}

release_lock() {
  if [[ -n "$HELD_LOCK" ]]; then rmdir "$LOCK" 2>/dev/null || true; fi
  HELD_LOCK=""
}

refuse() { # exit 2: refused, nothing was changed
  say "REFUSED: $*"
  say "Nothing was changed."
  finish 2
}

finish() {
  local code="$1" line
  trap '' INT TERM
  STATE=finished
  say ""
  say "==== homayar-onprem $CMD result ===="
  say "command:    $CMD"
  say "state id:   ${STATE_ID:--}"
  if [[ "$CMD" == upgrade || "$CMD" == rollback ]]; then
    say "current:    $(readlink "$H/current" 2>/dev/null || printf '?')"
  fi
  if [[ "$code" == 3 ]]; then
    say "ATTENTION: the rollback failed; manual action is needed (the steps above)."
  fi
  say "checks:"
  for line in "${RESULTS[@]}"; do say "  $line"; done
  if [[ -n "$LOG" ]]; then say "log:        $LOG"; fi
  say "exit code:  $code"
  cleanup
  exit "$code"
}

cleanup() {
  release_lock
  if [[ -n "$TMP" ]]; then rm -rf -- "${TMP:?}"; fi
  TMP=""
  if [[ -n "$EXTRACT_TMP" ]]; then rm -rf -- "${EXTRACT_TMP:?}"; fi
  EXTRACT_TMP=""
}

rollback_on_death() { # a signal or an unexpected exit while changing
  local why="$1"
  say "ROLLBACK because: $why"
  if [[ -z "$STATE_ID" ]]; then
    say "no state record: the manual steps below are the only way"
    manual_steps
    return 1
  fi
  rollback_flow "$why" "$STATE_ID" automatic || return 1
  return 0
}

on_signal() {
  trap '' INT TERM
  case "$STATE" in
    preflight)
      say "STOPPED by a signal before any change."
      refuse "stopped by a signal"
      ;;
    changing)
      if rollback_on_death "stopped by a signal (SIGINT or SIGTERM)"; then
        finish 1
      fi
      finish 3
      ;;
    *)
      say "Signal ignored in state $STATE."
      ;;
  esac
}

on_exit() {
  local status=$?
  set +e
  trap '' INT TERM
  if [[ "$STATE" == changing ]]; then
    if rollback_on_death "stopped unexpectedly during the change (exit $status)"; then
      cleanup
      exit 1
    fi
    cleanup
    exit 3
  fi
  cleanup
  if [[ "$STATE" != finished && "$status" != 0 && "$status" != 2 ]]; then
    say "STOPPED UNEXPECTEDLY before any change (exit $status). Nothing was changed."
    exit 2
  fi
}

# --- package handling --------------------------------------------------------------

package_env() { # package_env <dir> <key>: the release.env value, or empty.
  # release.env is parsed as data, never sourced.
  local value=""
  value="$(sed -n "s/^$2=//p" "$1/release.env" 2>/dev/null | head -n 1)" || true
  printf '%s' "$value"
}

verify_package_signature() { # the HOM-107 seam: package signing comes there
  say "WARNING: package is hash-checked, not signed (HOM-107)"
  return 0
}

verify_package() { # verify_package <dir>: 0 ok, else 1 with the reason said
  local pkg="$1" line hash path f sum
  local sums="$TMP/sums.txt" paths="$TMP/paths.txt"
  [[ -d "$pkg" ]] || { say "the package folder $pkg does not exist"; return 1; }
  [[ -f "$pkg/SHA256SUMS" ]] || { say "the package has no SHA256SUMS"; return 1; }
  [[ -f "$pkg/release.env" ]] || { say "the package has no release.env"; return 1; }
  : >"$sums"
  : >"$paths"
  local n=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    n=$((n + 1))
    if [[ "$line" != *"  "* ]]; then
      say "SHA256SUMS line $n is not a '<sha256>  <path>' line"
      return 1
    fi
    hash="${line%%  *}"
    path="${line#*  }"
    if [[ ! "$hash" =~ ^[0-9a-f]{64}$ ]]; then
      say "SHA256SUMS line $n has a bad hash for $path"
      return 1
    fi
    if [[ -z "$path" || "$path" == /* || "/$path/" == *"/../"* || "/$path/" == *"/./"* ]]; then
      say "SHA256SUMS line $n has a forbidden path: $path"
      return 1
    fi
    [[ "$path" != "SHA256SUMS" ]] || { say "SHA256SUMS must not list itself"; return 1; }
    printf '%s %s\n' "$hash" "$path" >>"$sums"
    printf '%s\n' "$path" >>"$paths"
  done <"$pkg/SHA256SUMS"
  [[ -s "$sums" ]] || { say "SHA256SUMS lists no files"; return 1; }

  # release.env: exactly the allowed keys with valid values
  local key version sha ref
  if [[ "$(cut -d= -f1 "$pkg/release.env" | sort -u | grep -c . || true)" != 5 ]]; then
    say "release.env must hold exactly the five allowed keys"
    return 1
  fi
  for key in HOMAYAR_RELEASE_VERSION HOMAYAR_RELEASE_SHA API_IMAGE CONSOLE_WEB_IMAGE EGRESS_PROXY_IMAGE; do
    [[ -n "$(package_env "$pkg" "$key")" ]] || { say "release.env has no $key"; return 1; }
  done
  version="$(package_env "$pkg" HOMAYAR_RELEASE_VERSION)"
  sha="$(package_env "$pkg" HOMAYAR_RELEASE_SHA)"
  [[ "$version" =~ ^[0-9A-Za-z][0-9A-Za-z._-]*$ ]] || { say "HOMAYAR_RELEASE_VERSION is not a version: $version"; return 1; }
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { say "HOMAYAR_RELEASE_SHA is not 40 lowercase hex characters"; return 1; }
  for key in API_IMAGE CONSOLE_WEB_IMAGE EGRESS_PROXY_IMAGE; do
    ref="$(package_env "$pkg" "$key")"
    if [[ ! "$ref" =~ ^[^[:space:]/]+(/[^[:space:]/]+)*:[^[:space:]/]+$ ]]; then
      say "$key is not a versioned image reference (name[:port][/repo]:tag): $ref"
      return 1
    fi
    case "$ref" in
      *:local | *:latest)
        say "$key uses the tag '${ref##*:}'; a versioned tag is required"
        return 1
        ;;
    esac
  done

  # every listed file exists and matches its hash
  while read -r hash path; do
    f="$pkg/$path"
    if [[ ! -f "$f" ]]; then
      say "SHA256SUMS lists a missing file: $path"
      return 1
    fi
    sum="$(sha256sum "$f" | awk '{ print $1 }')"
    [[ "$sum" == "$hash" ]] || { say "hash mismatch for $path"; return 1; }
  done <"$sums"

  # only regular files and folders: no symlinks or devices
  while IFS= read -r f; do
    say "the package holds an entry that is not a regular file or folder: ${f#"$pkg"/}"
    return 1
  done < <(find "$pkg" ! -type f ! -type d -print)

  # every file of the package is listed (SHA256SUMS itself is never listed)
  while IFS= read -r -d '' f; do
    path="${f#"$pkg"/}"
    [[ "$path" == "SHA256SUMS" ]] && continue
    if ! grep -qxF "$path" "$paths"; then
      say "the package holds an unlisted file: $path"
      return 1
    fi
  done < <(find "$pkg" -type f -print0)
  return 0
}

stage_package() { # stage_package <path> <staging-dir>: prints the folder to
  # verify (a package folder is used in place; a .tar is extracted first)
  local src="$1" dst="$2"
  if [[ -d "$src" ]]; then
    printf '%s' "$src"
    return 0
  fi
  if [[ "$src" == *.tar && -f "$src" ]]; then
    mkdir -p "$dst"
    if ! run tar -xf "$src" -C "$dst"; then
      return 1
    fi
    printf '%s' "$dst"
    return 0
  fi
  say_err "the package is neither a folder nor a .tar: $src"
  return 1
}

verify_or_refuse() { # verify_or_refuse <staged-package>
  local reason=""
  if ! reason="$(verify_package "$1")"; then
    refuse "the package does not verify: ${reason:-no reason given}"
  fi
}

load_images() { # load_images <release-dir>: docker load every images/*.tar
  local r="$1" f rc=0
  [[ -d "$r/images" ]] || { say "$r has no images folder"; return 1; }
  for f in "$r"/images/*.tar; do
    [[ -e "$f" ]] || continue
    run docker load --input "$f" || rc=1
  done
  return "$rc"
}

extract_release() { # extract_release <staged> <target-dir>
  local src="$1" dst="$2"
  if [[ "$src" == "$TMP/pkg" ]]; then
    run cp -a "$src" "$dst"
  else
    run mkdir -p "$dst" && run cp -a "$src"/. "$dst"/
  fi
}

# --- docker helpers ----------------------------------------------------------------

image_id() { # the image ID of a reference, or empty
  cap timeout --kill-after="$T_KILL" "$T_CALL" docker image inspect --format '{{.Id}}' "$1" || return 0
  head -n 1 <<<"$RUN_OUT"
}

docker_root() {
  cap timeout --kill-after="$T_KILL" "$T_CALL" docker info --format '{{.DockerRootDir}}' || return 0
  head -n 1 <<<"$RUN_OUT"
}

dc() { # dc <seconds> <release-folder> <compose args...>: one compose command
  # line against a release folder, with the images of its release.env and the
  # operator env file. stdin is /dev/null unless the caller uses run_in.
  local t="$1" r="$2" api cw eg
  shift 2
  api="$(package_env "$r" API_IMAGE)"
  cw="$(package_env "$r" CONSOLE_WEB_IMAGE)"
  eg="$(package_env "$r" EGRESS_PROXY_IMAGE)"
  (
    cd "$r" &&
      API_IMAGE="$api" CONSOLE_WEB_IMAGE="$cw" EGRESS_PROXY_IMAGE="$eg" \
        timeout --kill-after="$T_KILL" "$t" docker compose \
          --env-file "$CONFIG" \
          --file infra/compose/compose.onprem.yaml \
          --profile onprem "$@"
  )
}

http_target() { # host:port the health checks curl. HTTP_PORT and BIND_ADDRESS
  # come from the environment or config/onprem.env (quotes stripped, as
  # Compose does); a 0.0.0.0 bind is not a curl target, loopback is; an IPv6
  # address is wrapped in brackets for the URL.
  local port bind
  port="$(env_value HTTP_PORT)"
  [[ "$port" =~ ^[0-9]+$ ]] || port=8080
  bind="$(env_value BIND_ADDRESS)"
  if [[ -z "$bind" || "$bind" == 0.0.0.0 ]]; then bind=127.0.0.1; fi
  if [[ "$bind" == *:* ]]; then bind="[$bind]"; fi
  printf '%s:%s' "$bind" "$port"
}

stack_rows() { # the ps rows of every service, in STACK_ROWS
  run dc "$T_CALL" "$1" ps --all --format '{{.Service}}|{{.State}}|{{.Health}}|{{.ExitCode}}'
  STACK_ROWS="$RUN_OUT"
}

svc_row() { awk -F'|' -v s="$1" '$1 == s { print; exit }' <<<"$STACK_ROWS"; }

check_rows() { # every service in its wanted state
  local s row
  for s in "${HEALTH_SERVICES[@]}"; do
    row="$(svc_row "$s")"
    [[ "$row" == "$s|running|healthy|"* ]] || { STACK_BAD="$s is '$row'"; return 1; }
  done
  row="$(svc_row scheduler)"
  [[ "$row" == "scheduler|running|"* ]] || { STACK_BAD="scheduler is '$row'"; return 1; }
  row="$(svc_row minio-bootstrap)"
  [[ "$row" == "minio-bootstrap|exited||0"* ]] || { STACK_BAD="minio-bootstrap is '$row'"; return 1; }
  return 0
}

running_image_id() { # running_image_id <release> <service>: the image ID, or empty
  local cid
  cap dc "$T_CALL" "$1" ps --all -q "$2" || return 0
  cid="$(head -n 1 <<<"$RUN_OUT")"
  [[ -n "$cid" ]] || return 0
  cap timeout --kill-after="$T_KILL" "$T_CALL" docker inspect --format '{{.Image}}' "$cid" || return 0
  head -n 1 <<<"$RUN_OUT"
}

db_version() { # the alembic version row; prints only the value
  cap dc "$T_CALL" "$1" exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select version_num from alembic_version"' || {
    say_err "the alembic_version query failed"
    return 1
  }
  local rows
  rows="$(grep -c . <<<"$RUN_OUT" || true)"
  [[ "$rows" == 1 ]] || { say_err "the alembic_version query returned $rows rows"; return 1; }
  tail -n 1 <<<"$RUN_OUT"
}

wait_healthy() { # wait_healthy <release>: 0 when healthy and /healthz and /readyz 200
  local release="$1" deadline start code base
  base="$(http_target)"
  start="$(now)"
  deadline=$((start + WAIT_SECONDS))
  while [[ "$(now)" -lt "$deadline" ]]; do
    code="$(http_code 5 "$TMP/body" "http://$base/healthz")"
    [[ "$code" == 200 ]] || { sleep 1; continue; }
    code="$(http_code 5 "$TMP/body" "http://$base/readyz")"
    [[ "$code" == 200 ]] || { sleep 1; continue; }
    stack_rows "$release"
    if check_rows; then return 0; fi
    sleep 1
  done
  say "the stack was not healthy with /healthz and /readyz 200 within $WAIT_SECONDS s"
  return 1
}

image_head() { # image_head <api-image>: the migration head from build-info.json
  cap timeout --kill-after="$T_KILL" "$T_CALL" docker run --rm --network none \
    --entrypoint python "$1" \
    -c 'import json; print(json.load(open("/app/build-info.json"))["schema"]["migration_head"])' ||
    return 1
  head -n 1 <<<"$RUN_OUT"
}

current_release() { # the resolved current folder, or empty
  local r
  r="$(readlink -f "$H/current" 2>/dev/null || true)"
  [[ -n "$r" && -d "$r" ]] || return 0
  printf '%s' "$r"
}

require_layout() { # the install layout, the operator env file and current
  [[ -d "$OPS" ]] || { say "REFUSED: $OPS does not exist"; say "Nothing was changed."; exit 2; }
  [[ -r "$CONFIG" ]] || { say "REFUSED: $CONFIG is missing or not readable"; say "Nothing was changed."; exit 2; }
  CUR="$(current_release)"
  [[ -n "$CUR" ]] || { say "REFUSED: $H/current does not resolve to a release folder"; say "Nothing was changed."; exit 2; }
  say "current release: $CUR ($(package_env "$CUR" HOMAYAR_RELEASE_VERSION))"
}

env_value() { # env_value <NAME>: from the environment, else from onprem.env;
  # one pair of matching surrounding quotes is stripped, as Compose does
  local name="$1" value first last
  value="${!name:-}"
  if [[ -z "$value" && -r "$CONFIG" ]]; then
    value="$(sed -n "s/^$name=//p" "$CONFIG" | head -n 1)" || true
  fi
  if [[ ${#value} -ge 2 ]]; then
    first="${value:0:1}"
    last="${value: -1}"
    if [[ ($first == '"' && $last == '"') || ($first == "'" && $last == "'") ]]; then
      value="${value:1:${#value}-2}"
    fi
  fi
  printf '%s' "$value"
}

gauge_timestamp() { # gauge_timestamp <dir>: the newest backup freshness value.
  # metrics.py writes the value with three decimals (1794264000.250), so the
  # fraction is dropped before the integer check.
  local f="$1/homayar_backup.prom" line v best="" whole
  [[ -f "$f" ]] || return 0
  while IFS= read -r line; do
    [[ "$line" == homayar_backup_last_success_timestamp_seconds* ]] || continue
    v="${line##* }"
    whole="${v%%.*}"
    [[ "$whole" =~ ^[0-9]+$ ]] || continue
    if [[ -z "$best" || "$whole" -gt "$best" ]]; then best="$whole"; fi
  done <"$f"
  printf '%s' "$best"
}

switch_current() { # switch_current <release-dir>: the atomic ln + mv -T
  local link="${1##*/}"
  ln -sfn "releases/$link" "$H/.current.$TS.new" || return 1
  mv -T "$H/.current.$TS.new" "$H/current" || { rm -f -- "$H/.current.$TS.new"; return 1; }
}

# --- usage ---------------------------------------------------------------------------

usage() {
  cat <<'EOF'
usage: bash homayar-onprem.sh <command> [flags] </dev/null

commands:
  pack --source <checkout> --output <dir> --version <v>
       --api-image R --console-web-image R --egress-proxy-image R
       build an unsigned package from a checkout and built images
  preflight [--offline]
       compose config, free disk, and the api-container host preflight
       (its exit code 0/1/2 is passed through)
  install --package <dir-or-tar>
       first install of a verified package; refuses when current exists
  upgrade --package <dir-or-tar> [--max-backup-age-hours N]
       [--skip-backup-check "<reason>"] [--min-disk-gib N]
       data-preserving upgrade with a pre-migration dump and rollback
  rollback --to <state-id> --accept-data-loss-since <UTC ts>
       manual rollback of a finished upgrade (the ts is the record's stopped_at)
  backup [backup.sh args]             run the release's backup.sh
  restore [restore-isolated.sh args]  run the release's restore-isolated.sh
  support-bundle --output <file.tar.gz>
       sanitized, scanned bundle (fails closed; the scan cannot be skipped)
  status                              read-only overview, no secrets

environment: HOMAYAR_HOME (default /opt/homayar), NODE_EXPORTER_TEXTFILE_DIR
       (the backup freshness gauge). The health checks read HTTP_PORT and
       BIND_ADDRESS from the environment or config/onprem.env (loopback and
       8080 when unset).

exit codes: 0 done (or already done); 1 failed and rolled back (data
restored); 2 refused, or failed before any live change; 3 rollback failed,
manual action needed. Unknown command or bad flag: 2.
EOF
}

# --- pack -----------------------------------------------------------------------------

cmd_pack() {
  local src="" out="" api="" cw="" eg="" version="" sha ref part
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --source) [[ $# -ge 2 ]] || { usage; exit 2; }; src="$2"; shift 2 ;;
      --output) [[ $# -ge 2 ]] || { usage; exit 2; }; out="$2"; shift 2 ;;
      --version) [[ $# -ge 2 ]] || { usage; exit 2; }; version="$2"; shift 2 ;;
      --api-image) [[ $# -ge 2 ]] || { usage; exit 2; }; api="$2"; shift 2 ;;
      --console-web-image) [[ $# -ge 2 ]] || { usage; exit 2; }; cw="$2"; shift 2 ;;
      --egress-proxy-image) [[ $# -ge 2 ]] || { usage; exit 2; }; eg="$2"; shift 2 ;;
      *)
        usage
        exit 2
        ;;
    esac
  done
  if [[ -z "$src" || -z "$out" || -z "$api" || -z "$cw" || -z "$eg" || -z "$version" ]]; then
    usage
    exit 2
  fi
  [[ "$version" =~ ^[0-9A-Za-z][0-9A-Za-z._-]*$ ]] || { echo "REFUSED: bad --version: $version" >&2; exit 2; }
  [[ -d "$src" ]] || { echo "REFUSED: --source is not a folder: $src" >&2; exit 2; }
  for ref in "$api" "$cw" "$eg"; do
    [[ "$ref" == *:* ]] || { echo "REFUSED: $ref has no tag; use a versioned tag" >&2; exit 2; }
    case "$ref" in
      *:local | *:latest)
        echo "REFUSED: $ref uses a non-versioned tag; use a versioned tag" >&2
        exit 2
        ;;
    esac
  done
  if [[ -d "$out" ]] && [[ -n "$(ls -A "$out" 2>/dev/null)" ]]; then
    echo "REFUSED: the output folder is not empty: $out" >&2
    exit 2
  fi
  mkdir -p "$out/infra" "$out/images"
  echo "pack: source $src, version $version, output $out"

  if [[ -n "$(git -C "$src" status --porcelain 2>/dev/null)" ]]; then
    echo "REFUSED: the checkout $src is dirty; commit or stash first" >&2
    exit 2
  fi
  sha="$(git -C "$src" rev-parse HEAD 2>/dev/null || true)"
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "REFUSED: cannot read the HEAD sha of $src" >&2; exit 2; }

  for part in compose scripts nginx; do
    [[ -d "$src/infra/$part" ]] || { echo "REFUSED: $src/infra/$part is missing" >&2; exit 2; }
  done
  cp -a "$src/infra/compose" "$src/infra/scripts" "$src/infra/nginx" "$out/infra/"

  run docker save --output "$out/images/api.tar" "$api" || exit 2
  run docker save --output "$out/images/console-web.tar" "$cw" || exit 2
  run docker save --output "$out/images/egress-proxy.tar" "$eg" || exit 2

  {
    printf 'HOMAYAR_RELEASE_VERSION=%s\n' "$version"
    printf 'HOMAYAR_RELEASE_SHA=%s\n' "$sha"
    printf 'API_IMAGE=%s\n' "$api"
    printf 'CONSOLE_WEB_IMAGE=%s\n' "$cw"
    printf 'EGRESS_PROXY_IMAGE=%s\n' "$eg"
  } >"$out/release.env"

  # SHA256SUMS last: every file of the package, except itself
  (cd "$out" && find . -type f -not -name SHA256SUMS -printf '%P\0' | LC_ALL=C sort -z |
    xargs -0 sha256sum >SHA256SUMS) || exit 2
  verify_package_signature "$out"
  verify_or_refuse_quiet "$out" || exit 2
  echo "pack: wrote $out (version $version, sha ${sha:0:12})"
  exit 0
}

verify_or_refuse_quiet() { # pack has no log file; verify with the reason echoed
  local reason=""
  if ! reason="$(verify_package "$1")"; then
    echo "REFUSED: the packed package does not verify (a bug in pack): ${reason:-}" >&2
    return 1
  fi
  return 0
}

# --- preflight ---------------------------------------------------------------------------

cmd_preflight() {
  local offline="" rc=0 free root
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --offline) offline=1; shift ;;
      *)
        usage
        exit 2
        ;;
    esac
  done
  [[ -d "$OPS" ]] || { echo "REFUSED: $OPS does not exist" >&2; exit 2; }
  LOG="$OPS/homayar-onprem-$TS.log"
  (umask 077 && : >>"$LOG") 2>/dev/null || { echo "REFUSED: cannot write the log file in $OPS" >&2; exit 2; }
  acquire_lock
  require_layout
  say "homayar-onprem preflight, log $LOG"

  if ! run dc "$T_CALL" "$CUR" config --quiet; then
    record pre P1 FAIL "docker compose config --quiet failed with the operator env file"
    refuse "the compose config does not render with $CONFIG"
  fi
  record pre P1 PASS "compose config renders with the operator env file"
  root="$(docker_root)"
  free="$(disk_free_kb "${root:-/nonexistent}")"
  [[ "$free" =~ ^[0-9]+$ && "$free" -ge "$MIN_DISK_KB_DEFAULT" ]] ||
    refuse "free space under the Docker root '${root:-/}' is ${free:-?} KiB; need $MIN_DISK_KB_DEFAULT"
  record pre P2 PASS "free ${free} KiB under ${root:-/}"

  say "running the host preflight in the api container (--host)"
  if [[ -n "$offline" ]]; then
    run dc "$T_UP" "$CUR" run --rm -T --no-deps api python -m backend.jobs.model_preflight --host --offline || rc=$?
  else
    run dc "$T_UP" "$CUR" run --rm -T --no-deps api python -m backend.jobs.model_preflight --host || rc=$?
  fi
  (( rc > 2 )) && rc=2
  record pre P3 PASS "host preflight exit $rc (0 ready, 1 not ready, 2 configuration)"
  finish "$rc"
}

# --- install ------------------------------------------------------------------------------

cmd_install() {
  local pkg="" staging release version api cw eg free root rc rtmp
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --package) [[ $# -ge 2 ]] || { usage; exit 2; }; pkg="$2"; shift 2 ;;
      *)
        usage
        exit 2
        ;;
    esac
  done
  [[ -n "$pkg" ]] || { usage; exit 2; }
  mkdir -p "$RELEASES" "$STATES" "$OPS" "$H/config"
  touch "$CONFIG" 2>/dev/null || { echo "REFUSED: cannot write $CONFIG" >&2; exit 2; }
  chmod 600 "$CONFIG"
  LOG="$OPS/homayar-onprem-$TS.log"
  (umask 077 && : >>"$LOG") 2>/dev/null || { echo "REFUSED: cannot write the log file in $OPS" >&2; exit 2; }
  acquire_lock
  say "homayar-onprem install, package $pkg, log $LOG"

  if [[ -e "$H/current" || -L "$H/current" ]]; then
    say "REFUSED: $H/current already exists; this is not a first install"
    say "use upgrade instead"
    say "Nothing was changed."
    finish 2
  fi
  [[ -s "$CONFIG" ]] || refuse "$CONFIG is empty; write the operator settings first (docs/operations/deployment.md)"

  verify_package_signature "$pkg" || refuse "the package signature check failed"
  staging="$(stage_package "$pkg" "$TMP/pkg")" || refuse "the package could not be read"
  verify_or_refuse "$staging"
  record install V1 PASS "package verified (hash list and release.env)"

  # Stage in a temp folder for the checks that start nothing; then rename
  # BEFORE the first compose call that creates a container. Compose resolves
  # the release's relative bind mounts to absolute host paths when it creates
  # the containers, so a container created from the temp folder could not be
  # restarted after the rename.
  version="$(package_env "$staging" HOMAYAR_RELEASE_VERSION)"
  release="$RELEASES/$version"
  [[ -e "$release" ]] && refuse "releases/$version already exists"
  rtmp="$RELEASES/.$version.tmp.$$"
  extract_release "$staging" "$rtmp" || refuse "extracting the package failed"
  EXTRACT_TMP="$rtmp"
  [[ -f "$rtmp/release.env" ]] || refuse "extracting the package failed"

  load_images "$rtmp" || refuse "docker load of the package images failed"
  api="$(package_env "$rtmp" API_IMAGE)"
  cw="$(package_env "$rtmp" CONSOLE_WEB_IMAGE)"
  eg="$(package_env "$rtmp" EGRESS_PROXY_IMAGE)"
  [[ -n "$(image_id "$api")" && -n "$(image_id "$cw")" && -n "$(image_id "$eg")" ]] ||
    refuse "one of $api, $cw, $eg is not present after docker load"
  record install I1 PASS "images present: $api, $cw, $eg"

  # from here on the release has its final name; EXTRACT_TMP keeps pointing
  # at it so any later failure of this install removes it again (current does
  # not exist yet, and no data volume, image, config/ or state/ is touched)
  mv "$rtmp" "$release" || refuse "moving releases/$version into place failed"
  EXTRACT_TMP="$release"
  record install E1 PASS "release in place: releases/$version"

  run dc "$T_CALL" "$release" config --quiet || refuse "the compose config of the new release does not render with $CONFIG"
  record install P1 PASS "compose config renders"
  root="$(docker_root)"
  free="$(disk_free_kb "${root:-/nonexistent}")"
  [[ "$free" =~ ^[0-9]+$ && "$free" -ge "$MIN_DISK_KB_DEFAULT" ]] ||
    refuse "free space under the Docker root '${root:-/}' is ${free:-?} KiB; need $MIN_DISK_KB_DEFAULT"
  record install P2 PASS "free ${free} KiB under ${root:-/}"

  rc=0
  run dc "$T_UP" "$release" run --rm -T --no-deps api python -m backend.jobs.model_preflight --host || rc=$?
  [[ "$rc" == 0 ]] || refuse "the host preflight failed with exit $rc (fix the host first)"
  record install P3 PASS "host preflight passed"

  run dc "$T_UP" "$release" up -d --no-build --pull never postgres redis minio minio-bootstrap ||
    refuse "starting the stores failed"
  record install S1 PASS "stores up (postgres, redis, minio, minio-bootstrap)"

  run dc "$T_MIGRATE" "$release" --profile migrate run --rm -T migrate ||
    refuse "the migration run failed"
  record install M1 PASS "migrations ran"

  run dc "$T_UP" "$release" up -d --no-build --pull never ||
    refuse "starting the stack failed"
  wait_healthy "$release" || refuse "the stack did not become healthy"
  record install H1 PASS "stack healthy (/healthz and /readyz 200)"

  # the install is done: from here nothing removes the release again
  EXTRACT_TMP=""
  switch_current "$release" || refuse "pointing current at releases/$version failed"
  record install C1 PASS "current -> releases/$version"
  say "Install done. To upgrade later: homayar-onprem.sh upgrade --package <new package>"
  finish 0
}

# --- record handling ---------------------------------------------------------------------

record_value() { # record_value <file> <key>
  local value=""
  value="$(sed -n "s/^$2=//p" "$1" 2>/dev/null | head -n 1)" || true
  printf '%s' "$value"
}

manual_steps() {
  say "Run these by hand, in this order, and check each result:"
  if [[ -n "$STATE_ID" ]]; then
    say "  0. bash $0 rollback --to $STATE_ID --accept-data-loss-since ${STOPPED_AT:-<stopped_at of the record>}"
  fi
  say "  1. stop the writers:"
  say "       docker compose --env-file $CONFIG --file <release>/infra/compose/compose.onprem.yaml --profile onprem stop ${WRITERS[*]}"
  if [[ -n "$DUMP" && -s "$DUMP" ]]; then
    say "  2. restore the database from $DUMP:"
    say "       cat $DUMP | docker compose --env-file $CONFIG --file <release>/infra/compose/compose.onprem.yaml --profile onprem exec -T postgres sh -c 'dropdb --force -U \"\$POSTGRES_USER\" \"\$POSTGRES_DB\"; createdb -U \"\$POSTGRES_USER\" -O \"\$POSTGRES_USER\" \"\$POSTGRES_DB\"; pg_restore --exit-on-error -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"'"
  fi
  say "  3. switch current back and start the stack:"
  say "       ln -sfn releases/<old-version> $H/.current.manual && mv -T $H/.current.manual $H/current"
  say "       docker compose --env-file $CONFIG --file $H/current/infra/compose/compose.onprem.yaml --profile onprem up -d --no-build --pull never"
  say "  4. curl http://$(http_target)/healthz and /readyz must answer 200"
}

rollback_flow() { # rollback_flow <reason> <state-id> <automatic|manual>: 0 ok.
  # R1 stop the writers; R2 restore the dump (always for manual; for automatic
  # only when the alembic version moved from ALEMBIC_BEFORE); R3 switch current
  # back; R4 start the old stack; R5 health and version checks. The caller
  # decides the exit code (1 for an automatic rollback, 0 for manual).
  local reason="$1" sid="$2" mode="$3"
  local rec from_ver from_rel before cur_rel live_v v fail=0 r1_ok=0 r2_needed=0 r2_ok=0
  trap '' INT TERM
  say "ROLLBACK because: $reason"
  if [[ -z "$sid" ]]; then
    say "no state record: the manual steps below are the only way"
    manual_steps
    return 1
  fi
  rec="$STATES/$sid"
  from_ver="$(record_value "$rec/record.env" FROM_VERSION)"
  from_rel="$RELEASES/$from_ver"
  before="$(record_value "$rec/record.env" ALEMBIC_BEFORE)"
  cur_rel="$(current_release)"
  [[ -n "$cur_rel" ]] || cur_rel="$from_rel"

  say "R1: stopping the writers on $cur_rel"
  if run dc "$T_UP" "$cur_rel" stop "${WRITERS[@]}"; then
    r1_ok=1
    record rollback R1 PASS "compose stop of the writers ran"
  else
    record rollback R1 FAIL "compose stop of the writers failed"
    fail=1
  fi

  live_v="$(db_version "$cur_rel")" || live_v=""
  if [[ "$mode" == manual ]]; then
    r2_needed=1
  elif [[ -z "$live_v" || "$live_v" != "$before" ]]; then
    # an unreadable version is an unknown database state: restore the dump
    r2_needed=1
  fi
  if [[ "$r2_needed" != 1 ]]; then
    record rollback R2 PASS "not needed: the database is still at $live_v"
  elif [[ "$r1_ok" != 1 ]]; then
    record rollback R2 SKIPPED "R1 failed"
    fail=1
  elif [[ ! -s "$rec/db.dump" ]]; then
    record rollback R2 FAIL "no usable dump in $rec"
    fail=1
  else
    say "R2: restoring the database from $rec/db.dump"
    if ! run dc "$T_RESTORE" "$cur_rel" exec -T postgres sh -c \
      'dropdb --force -U "$POSTGRES_USER" "$POSTGRES_DB"; createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" "$POSTGRES_DB"'; then
      record rollback R2 FAIL "could not reset the database"
      fail=1
    elif ! run_in "$rec/db.dump" dc "$T_RESTORE" "$cur_rel" exec -T postgres sh -c \
      'pg_restore --exit-on-error -U "$POSTGRES_USER" -d "$POSTGRES_DB"'; then
      record rollback R2 FAIL "pg_restore failed"
      fail=1
    else
      v="$(db_version "$cur_rel")" || v=""
      if [[ "$v" == "$before" ]]; then
        r2_ok=1
        record rollback R2 PASS "the database is back at $before"
      else
        record rollback R2 FAIL "the database is at '$v', expected $before"
        fail=1
      fi
    fi
  fi

  say "R3: current must point back to releases/$from_ver"
  if [[ "$(current_release)" == "$from_rel" ]]; then
    record rollback R3 PASS "current already points to releases/$from_ver"
  elif switch_current "$from_rel"; then
    record rollback R3 PASS "current switched back to releases/$from_ver"
  else
    record rollback R3 FAIL "could not switch current back"
    fail=1
  fi

  if [[ "$r1_ok" == 1 && ("$r2_needed" != 1 || "$r2_ok" == 1) ]]; then
    say "R4: up -d --no-build --pull never on releases/$from_ver"
    if ! run dc "$T_UP" "$from_rel" up -d --no-build --pull never; then
      record rollback R4 FAIL "compose up of the old release failed"
      fail=1
    else
      record rollback R4 PASS "compose up of the old release ran"
      say "R5: waiting for health and checking the database version"
      if ! wait_healthy "$from_rel"; then
        record rollback R5 FAIL "the old release did not become healthy within $WAIT_SECONDS s"
        fail=1
      else
        v="$(db_version "$from_rel")" || v=""
        if [[ "$v" == "$before" ]]; then
          record rollback R5 PASS "old release healthy, database at $before"
        else
          record rollback R5 FAIL "the database is at '$v', expected $before"
          fail=1
        fi
      fi
    fi
  elif [[ "$r1_ok" != 1 ]]; then
    record rollback R4 SKIPPED "R1 failed; run the manual steps (they start with the stop)"
    record rollback R5 SKIPPED "R4 did not run"
    fail=1
  else
    record rollback R4 SKIPPED "the database is not restored; the writers stay stopped"
    record rollback R5 SKIPPED "R4 did not run"
    fail=1
  fi

  if [[ "$fail" == 0 ]]; then
    say "ROLLED BACK: $reason"
    return 0
  fi
  say "ROLLBACK FAILED: manual action is needed (the steps below)."
  manual_steps
  return 1
}

# --- upgrade --------------------------------------------------------------------------------

dump_failed_restart() { # the dump or its proof failed: writers back on the
  # old release, current unchanged, exit 2
  say "starting the writers again on the OLD release $(package_env "$CUR" HOMAYAR_RELEASE_VERSION)"
  run dc "$T_UP" "$CUR" up -d --no-build --pull never || true
  refuse "the pre-migration dump failed; the writers were restarted on $CUR, current is unchanged"
}

upg_fail() { # a failure from the switch on: automatic rollback, exit 1 or 3
  local why="$1"
  say "UPGRADE FAILED: $why"
  if rollback_flow "$why" "$STATE_ID" automatic; then
    finish 1
  fi
  finish 3
}

db_scratch_version() {
  cap dc "$T_CALL" "$1" exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d homayar_restorecheck -Atc "select version_num from alembic_version"' || return 1
  tail -n 1 <<<"$RUN_OUT"
}

db_scratch_tables() {
  cap dc "$T_CALL" "$1" exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d homayar_restorecheck -Atc \"select count(*) from pg_tables where schemaname = 'public'\"" || return 1
  tail -n 1 <<<"$RUN_OUT"
}

db_live_tables() {
  cap dc "$T_CALL" "$1" exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc \"select count(*) from pg_tables where schemaname = 'public'\"" || return 1
  tail -n 1 <<<"$RUN_OUT"
}

cmd_upgrade() {
  local pkg="" max_hours="$MAX_BACKUP_HOURS_DEFAULT" skip_reason="" min_disk_kb="$MIN_DISK_KB_DEFAULT"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --package) [[ $# -ge 2 ]] || { usage; exit 2; }; pkg="$2"; shift 2 ;;
      --max-backup-age-hours) [[ $# -ge 2 ]] || { usage; exit 2; }; max_hours="$2"; shift 2 ;;
      --skip-backup-check) [[ $# -ge 2 ]] || { usage; exit 2; }; skip_reason="$2"; shift 2 ;;
      --min-disk-gib) [[ $# -ge 2 ]] || { usage; exit 2; }; [[ "$2" =~ ^[0-9]+$ ]] || { usage; exit 2; }; min_disk_kb=$(("$2" * 1024 * 1024)); shift 2 ;;
      *)
        usage
        exit 2
        ;;
    esac
  done
  [[ -n "$pkg" && "$max_hours" =~ ^[0-9]+$ ]] || { usage; exit 2; }
  if [[ -n "$skip_reason" && "$skip_reason" =~ [[:cntrl:]] ]]; then
    echo "REFUSED: the --skip-backup-check reason must not contain a newline or other control character" >&2
    exit 2
  fi
  mkdir -p "$RELEASES" "$STATES" "$OPS"
  LOG="$OPS/homayar-onprem-$TS.log"
  (umask 077 && : >>"$LOG") 2>/dev/null || { echo "REFUSED: cannot write the log file in $OPS" >&2; exit 2; }
  acquire_lock
  require_layout
  say "homayar-onprem upgrade, package $pkg, log $LOG"

  local staging new_ver new_sha cur_ver cur_sha gauge="" textdir nowts age
  verify_package_signature "$pkg" || refuse "the package signature check failed"
  staging="$(stage_package "$pkg" "$TMP/pkg")" || refuse "the package could not be read"
  verify_or_refuse "$staging"
  record upgrade V1 PASS "package verified"

  new_ver="$(package_env "$staging" HOMAYAR_RELEASE_VERSION)"
  new_sha="$(package_env "$staging" HOMAYAR_RELEASE_SHA)"
  cur_ver="$(package_env "$CUR" HOMAYAR_RELEASE_VERSION)"
  cur_sha="$(package_env "$CUR" HOMAYAR_RELEASE_SHA)"
  if [[ "$new_ver" == "$cur_ver" ]]; then
    if [[ "$new_sha" == "$cur_sha" ]]; then
      say "Already on $new_ver (the same release SHA); nothing to do."
      finish 0
    fi
    refuse "the package version equals the current version but the release SHA differs; use a new version"
  fi
  record upgrade P0 PASS "$cur_ver -> $new_ver"

  # the preflight, before any change
  stack_rows "$CUR"
  if ! check_rows; then
    record upgrade P1 FAIL "$STACK_BAD"
    refuse "the stack is not healthy on the current release"
  fi
  local code
  code="$(http_code 10 "$TMP/body" "http://$(http_target)/readyz")"
  [[ "$code" == 200 ]] || refuse "/readyz answers $code, not 200"
  record upgrade P1 PASS "stack healthy, /readyz 200"

  local free root
  root="$(docker_root)"
  free="$(disk_free_kb "${root:-/nonexistent}")"
  [[ "$free" =~ ^[0-9]+$ && "$free" -ge "$min_disk_kb" ]] ||
    refuse "free space under the Docker root '${root:-/}' is ${free:-?} KiB; need $min_disk_kb"
  free="$(disk_free_kb "$H")"
  [[ "$free" =~ ^[0-9]+$ && "$free" -ge "$min_disk_kb" ]] ||
    refuse "free space under $H is ${free:-?} KiB; need $min_disk_kb"
  record upgrade P2 PASS "free disk above $min_disk_kb KiB (Docker root and $H)"

  run dc "$T_CALL" "$staging" config --quiet ||
    refuse "the compose config of the new release does not render with $CONFIG"
  record upgrade P3 PASS "new release compose config renders"

  # the current database version, read before anything changes
  local alembic_before=""
  alembic_before="$(db_version "$CUR")" || refuse "could not read the alembic version"
  record upgrade P5 PASS "database at $alembic_before"

  if [[ -z "$skip_reason" ]]; then
    textdir="$(env_value NODE_EXPORTER_TEXTFILE_DIR)"
    if [[ -n "$textdir" ]]; then
      gauge="$(gauge_timestamp "$textdir")"
    fi
    nowts="$(now)"
    if [[ -z "$gauge" ]]; then
      say "REFUSED: no fresh verified backup: no homayar_backup_last_success_timestamp_seconds"
      say "value in ${textdir:-NODE_EXPORTER_TEXTFILE_DIR}/homayar_backup.prom."
      say "Run homayar-onprem.sh backup first, or pass --skip-backup-check \"<reason>\"."
      say "Nothing was changed."
      finish 2
    fi
    age=$((nowts - gauge))
    if ((age > max_hours * 3600)); then
      say "REFUSED: the last verified backup is $((age / 3600)) h old (limit $max_hours h)."
      say "Run homayar-onprem.sh backup first, or pass --skip-backup-check \"<reason>\"."
      say "Nothing was changed."
      finish 2
    fi
    record upgrade P4 PASS "verified backup is $((age / 60)) min old (limit $max_hours h)"
  else
    record upgrade P4 PASS "backup check skipped by the operator: $skip_reason"
  fi

  # extract and load (old images are never removed or pruned). The release
  # is renamed into releases/<version> only after every load-time check
  # passed, so a refused upgrade never blocks its own retry.
  NEW="$RELEASES/$new_ver"
  [[ -e "$NEW" ]] && refuse "releases/$new_ver already exists"
  local rtmp="$RELEASES/.$new_ver.tmp.$$"
  extract_release "$staging" "$rtmp" || refuse "extracting the package failed"
  EXTRACT_TMP="$rtmp"
  [[ -f "$rtmp/release.env" ]] || refuse "extracting the package failed"
  if ! load_images "$rtmp"; then
    refuse "docker load of the package images failed"
  fi
  local new_api new_cw new_eg new_api_id t_head=""
  new_api="$(package_env "$rtmp" API_IMAGE)"
  new_cw="$(package_env "$rtmp" CONSOLE_WEB_IMAGE)"
  new_eg="$(package_env "$rtmp" EGRESS_PROXY_IMAGE)"
  new_api_id="$(image_id "$new_api")"
  [[ -n "$new_api_id" && -n "$(image_id "$new_cw")" && -n "$(image_id "$new_eg")" ]] ||
    refuse "one of the new images is not present after docker load"
  # the migration head of the new image, still in preflight: a failure here
  # is a clean refusal, not a mid-change exit
  t_head="$(image_head "$new_api")" || refuse "could not read the migration head of $new_api"
  mv "$rtmp" "$NEW" || refuse "moving releases/$new_ver into place failed"
  EXTRACT_TMP=""
  record upgrade E1 PASS "extracted releases/$new_ver (loaded and checked as a temp folder first)"
  record upgrade I1 PASS "new images loaded, migration head $t_head"

  # the state record
  STATE_ID="$TS"
  REC="$STATES/$STATE_ID"
  (umask 077 && mkdir -p "$REC")
  local cur_api cur_cw cur_eg
  cur_api="$(package_env "$CUR" API_IMAGE)"
  cur_cw="$(package_env "$CUR" CONSOLE_WEB_IMAGE)"
  cur_eg="$(package_env "$CUR" EGRESS_PROXY_IMAGE)"
  {
    printf 'STATE_ID=%s\n' "$STATE_ID"
    printf 'FROM_VERSION=%s\n' "$cur_ver"
    printf 'FROM_SHA=%s\n' "$cur_sha"
    printf 'FROM_API_IMAGE=%s\n' "$cur_api"
    printf 'FROM_API_IMAGE_ID=%s\n' "$(image_id "$cur_api")"
    printf 'FROM_CONSOLE_WEB_IMAGE=%s\n' "$cur_cw"
    printf 'FROM_CONSOLE_WEB_IMAGE_ID=%s\n' "$(image_id "$cur_cw")"
    printf 'FROM_EGRESS_PROXY_IMAGE=%s\n' "$cur_eg"
    printf 'FROM_EGRESS_PROXY_IMAGE_ID=%s\n' "$(image_id "$cur_eg")"
    printf 'TO_VERSION=%s\n' "$new_ver"
    printf 'TO_SHA=%s\n' "$new_sha"
    printf 'TO_API_IMAGE=%s\n' "$new_api"
    printf 'ALEMBIC_BEFORE=%s\n' "$alembic_before"
    printf 'BACKUP_GAUGE_TS=%s\n' "$gauge"
    printf 'SKIP_BACKUP_REASON=%s\n' "$skip_reason"
  } >"$REC/record.env"
  chmod 600 "$REC/record.env"
  DUMP="$REC/db.dump"
  record upgrade W1 PASS "state record $STATE_ID written"

  # stop the writers, then dump and prove the dump restores
  STATE=changing
  STOPPED_AT="$(utc)"
  printf 'STOPPED_AT=%s\n' "$STOPPED_AT" >>"$REC/record.env"
  say "U1: stopping the writers (${WRITERS[*]}); the stores keep running"
  if ! run dc "$T_UP" "$CUR" stop "${WRITERS[@]}"; then
    record upgrade U1 FAIL "compose stop of the writers failed"
    upg_fail "the writers could not be stopped"
  fi
  record upgrade U1 PASS "writers stopped at $STOPPED_AT"

  say "U2: dumping the database to $DUMP and proving it restores"
  local err="$TMP/dump.err" status=0
  (dc "$T_DUMP" "$CUR" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
    >"$DUMP") 2>"$err" </dev/null || status=$?
  if [[ "$status" != 0 ]]; then
    show "$(cat "$err" || true)"
    record upgrade U2 FAIL "the database dump failed (exit $status)"
    dump_failed_restart
  fi
  chmod 600 "$DUMP"
  if [[ ! -s "$DUMP" ]]; then
    record upgrade U2 FAIL "the dump is empty"
    dump_failed_restart
  fi
  run_in "$DUMP" dc "$T_CALL" "$CUR" exec -T postgres sh -c 'pg_restore --list' ||
    { record upgrade U2 FAIL "pg_restore --list of the dump failed"; dump_failed_restart; }
  grep -q 'TABLE DATA public alembic_version' <<<"$RUN_OUT" ||
    { record upgrade U2 FAIL "the dump listing has no TABLE DATA public alembic_version"; dump_failed_restart; }
  run dc "$T_CALL" "$CUR" exec -T postgres sh -c \
    'dropdb --force -U "$POSTGRES_USER" homayar_restorecheck 2>/dev/null || true; createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" homayar_restorecheck' ||
    { record upgrade U2 FAIL "the scratch database homayar_restorecheck could not be created"; dump_failed_restart; }
  run_in "$DUMP" dc "$T_RESTORE" "$CUR" exec -T postgres sh -c \
    'pg_restore --exit-on-error --no-owner -U "$POSTGRES_USER" -d homayar_restorecheck' ||
    { record upgrade U2 FAIL "the dump does not restore into homayar_restorecheck"; dump_failed_restart; }
  local scratch_v scratch_t live_t
  scratch_v="$(db_scratch_version "$CUR")" || { record upgrade U2 FAIL "could not read the scratch database version"; dump_failed_restart; }
  scratch_t="$(db_scratch_tables "$CUR")" || { record upgrade U2 FAIL "could not count the scratch tables"; dump_failed_restart; }
  live_t="$(db_live_tables "$CUR")" || { record upgrade U2 FAIL "could not count the live tables"; dump_failed_restart; }
  [[ "$scratch_v" == "$alembic_before" ]] ||
    { record upgrade U2 FAIL "the scratch database is at '$scratch_v', the live one at $alembic_before"; dump_failed_restart; }
  [[ "$scratch_t" == "$live_t" ]] ||
    { record upgrade U2 FAIL "the scratch database has $scratch_t public tables, the live one $live_t"; dump_failed_restart; }
  run dc "$T_CALL" "$CUR" exec -T postgres sh -c 'dropdb --force -U "$POSTGRES_USER" homayar_restorecheck' ||
    { record upgrade U2 FAIL "the scratch database could not be dropped"; dump_failed_restart; }
  record upgrade U2 PASS "dump made before any migration and proven to restore"

  # switch, migrate, start, verify (t_head was read during preflight)
  local db_after="" run_api_id
  say "U3: switching current to releases/$new_ver and migrating to head $t_head"
  switch_current "$NEW" || upg_fail "switching current failed"
  record upgrade U3 PASS "current -> releases/$new_ver"
  run dc "$T_MIGRATE" "$NEW" --profile migrate run --rm -T migrate ||
    upg_fail "the migration run failed"
  db_after="$(db_version "$NEW")" || upg_fail "could not read the database version after the migration"
  [[ "$db_after" == "$t_head" ]] || upg_fail "the database is at '$db_after' after the migration, expected $t_head"
  record upgrade U4 PASS "migrations at $db_after"
  run dc "$T_UP" "$NEW" up -d --no-build --pull never ||
    upg_fail "compose up of the new release failed"
  wait_healthy "$NEW" || upg_fail "the new stack did not become healthy"
  run_api_id="$(running_image_id "$NEW" api)"
  [[ -n "$run_api_id" && "$run_api_id" == "$new_api_id" ]] ||
    upg_fail "the running api image is '$run_api_id', expected $new_api_id"
  db_after="$(db_version "$NEW")" || upg_fail "could not read the database version for the final check"
  [[ "$db_after" == "$t_head" ]] || upg_fail "the database is at '$db_after', expected $t_head"
  record upgrade U5 PASS "stack healthy on $new_ver, api on the loaded image, database at $t_head"

  STATE=finished
  say "Upgrade to $new_ver done. State id: $STATE_ID"
  say "To roll back manually: bash $0 rollback --to $STATE_ID --accept-data-loss-since $STOPPED_AT"
  finish 0
}

# --- rollback ----------------------------------------------------------------------------------

cmd_rollback() {
  local sid="" ts="" rec from_ver before to_ver key ref id
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --to) [[ $# -ge 2 ]] || { usage; exit 2; }; sid="$2"; shift 2 ;;
      --accept-data-loss-since) [[ $# -ge 2 ]] || { usage; exit 2; }; ts="$2"; shift 2 ;;
      *)
        usage
        exit 2
        ;;
    esac
  done
  [[ -n "$sid" && -n "$ts" ]] || { usage; exit 2; }
  [[ -d "$OPS" ]] || { echo "REFUSED: $OPS does not exist" >&2; exit 2; }
  LOG="$OPS/homayar-onprem-$TS.log"
  (umask 077 && : >>"$LOG") 2>/dev/null || { echo "REFUSED: cannot write the log file in $OPS" >&2; exit 2; }
  acquire_lock
  require_layout
  say "homayar-onprem rollback to state $sid, log $LOG"

  rec="$STATES/$sid"
  [[ -f "$rec/record.env" ]] || refuse "no state record at $rec/record.env"
  STATE_ID="$sid"
  STOPPED_AT="$(record_value "$rec/record.env" STOPPED_AT)"
  [[ -n "$STOPPED_AT" ]] || refuse "the record has no STOPPED_AT; it holds an unfinished upgrade"
  if [[ "$ts" != "$STOPPED_AT" ]]; then
    say "REFUSED: --accept-data-loss-since is $ts, but the writers of state $sid stopped at $STOPPED_AT."
    say "Writes made after that time are lost by this rollback; pass the exact value."
    say "Nothing was changed."
    finish 2
  fi
  from_ver="$(record_value "$rec/record.env" FROM_VERSION)"
  before="$(record_value "$rec/record.env" ALEMBIC_BEFORE)"
  to_ver="$(record_value "$rec/record.env" TO_VERSION)"
  DUMP="$rec/db.dump"
  [[ "$(package_env "$CUR" HOMAYAR_RELEASE_VERSION)" == "$to_ver" ]] ||
    refuse "current is $(package_env "$CUR" HOMAYAR_RELEASE_VERSION), not the record's to-version $to_ver; this record does not describe this install"
  [[ -s "$DUMP" ]] || refuse "the dump of state $sid is missing"
  [[ -d "$RELEASES/$from_ver" ]] || refuse "the from-release releases/$from_ver is missing"
  for key in API_IMAGE CONSOLE_WEB_IMAGE EGRESS_PROXY_IMAGE; do
    ref="$(record_value "$rec/record.env" "FROM_${key}")"
    id="$(record_value "$rec/record.env" "FROM_${key}_ID")"
    [[ -n "$(image_id "$id")" ]] || refuse "the from-image id $id no longer exists"
    if [[ "$(image_id "$ref")" != "$id" ]]; then
      refuse "the from-image $ref now resolves to $(image_id "$ref"), not the recorded $id; the tag moved"
    fi
  done
  record rollback R0 PASS "record $sid validated (stopped_at exact, dump, from-release and from-image ids present)"

  STATE=changing
  if rollback_flow "manual rollback to state $sid" "$sid" manual; then
    STATE=finished
    say "Manual rollback to $from_ver done (state $sid)."
    finish 0
  fi
  finish 3
}

# --- backup / restore ---------------------------------------------------------------------------

cmd_backup() {
  require_layout
  [[ -x "$CUR/infra/scripts/backup.sh" ]] || { echo "REFUSED: $CUR/infra/scripts/backup.sh is missing" >&2; exit 2; }
  echo "exec $CUR/infra/scripts/backup.sh $*"
  exec env COMPOSE_ENV_FILE="$CONFIG" "$CUR/infra/scripts/backup.sh" "$@"
}

cmd_restore() {
  require_layout
  [[ -x "$CUR/infra/scripts/restore-isolated.sh" ]] || { echo "REFUSED: $CUR/infra/scripts/restore-isolated.sh is missing" >&2; exit 2; }
  echo "exec $CUR/infra/scripts/restore-isolated.sh $*"
  exec env COMPOSE_ENV_FILE="$CONFIG" "$CUR/infra/scripts/restore-isolated.sh" "$@"
}

# --- support bundle -------------------------------------------------------------------------------

cmd_support_bundle() {
  local out="" api rc kinds f digest svc total warned line
  local docker_v compose_v root free_h free_d nvidia
  local s_service s_state s_health s_exit
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --output) [[ $# -ge 2 ]] || { usage; exit 2; }; out="$2"; shift 2 ;;
      *)
        usage
        exit 2
        ;;
    esac
  done
  [[ -n "$out" ]] || { usage; exit 2; }
  mkdir -p "$OPS"
  LOG="$OPS/homayar-onprem-$TS.log"
  (umask 077 && : >>"$LOG") 2>/dev/null || { echo "REFUSED: cannot write the log file in $OPS" >&2; exit 2; }
  acquire_lock
  require_layout
  say "homayar-onprem support-bundle, output $out, log $LOG"

  local bundle
  bundle="$(mktemp -d "$OPS/bundle.XXXXXX")"
  chmod 700 "$bundle"

  # host facts: no hostname, no IP addresses, no process list
  say "collecting host facts"
  stack_rows "$CUR" || true
  cap docker --version || true
  docker_v="$(head -n 1 <<<"$RUN_OUT")"
  cap docker compose version || true
  compose_v="$(head -n 1 <<<"$RUN_OUT")"
  root="$(docker_root)"
  free_h="$(disk_free_kb "$H")"
  free_d="$(disk_free_kb "${root:-/nonexistent}")"
  nvidia=""
  if command -v nvidia-smi >/dev/null 2>&1; then
    cap nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used --format=csv,noheader || true
    nvidia="$RUN_OUT"
  fi
  api="$(package_env "$CUR" API_IMAGE)"
  {
    printf '{\n'
    printf '  "format_version": 1,\n'
    printf '  "uname": "%s",\n' "$(json_escape "$(uname -srm)")"
    printf '  "docker_version": "%s",\n' "$(json_escape "$docker_v")"
    printf '  "compose_version": "%s",\n' "$(json_escape "$compose_v")"
    printf '  "free_disk_kib": {"homayar_home": %s, "docker_root": %s},\n' "${free_h:-0}" "${free_d:-0}"
    printf '  "nvidia": "%s",\n' "$(json_escape "$nvidia")"
    printf '  "api_image": "%s",\n' "$(json_escape "$api")"
    printf '  "api_image_id": "%s",\n' "$(json_escape "$(image_id "$api")")"
    printf '  "services": [\n'
    while IFS= read -r line; do
      [[ -n "$line" ]] || continue
      IFS='|' read -r s_service s_state s_health s_exit <<<"$line"
      printf '    {"service": "%s", "state": "%s", "health": "%s", "exit_code": "%s"},\n' \
        "$(json_escape "$s_service")" "$(json_escape "$s_state")" "$(json_escape "$s_health")" "$(json_escape "$s_exit")"
    done <<<"$STACK_ROWS"
    printf '  ]\n'
    printf '}\n'
  } >"$bundle/host.json"

  say "running the host preflight"
  rc=0
  run dc "$T_UP" "$CUR" run --rm -T --no-deps api python -m backend.jobs.model_preflight --host || rc=$?
  printf '%s\n' "$RUN_OUT" >"$bundle/preflight.json"

  say "summarizing the service logs of the last 24 h (counts only, no text)"
  {
    printf '{\n'
    printf '  "window": "24h",\n'
    local logs_first=1
    for svc in "${LOG_SERVICES[@]}"; do
      cap dc "$T_CALL" "$CUR" logs --since 24h --no-log-prefix "$svc" || true
      total=0
      warned=0
      if [[ -n "$RUN_OUT" ]]; then
        total="$(wc -l <<<"$RUN_OUT" | tr -d '[:space:]')"
        warned="$(grep -cE 'ERROR|WARNING|CRITICAL' <<<"$RUN_OUT" || true)"
      fi
      [[ "$logs_first" == 1 ]] || printf ',\n'
      logs_first=0
      printf '  "%s": {"lines": %s, "error_warning_critical": %s}' "$svc" "${total:-0}" "${warned:-0}"
    done
    printf '\n}\n'
  } >"$bundle/logs-summary.json"

  say "collecting the container-side facts"
  rc=0
  cap dc "$T_UP" "$CUR" run --rm -T --no-deps api python -m backend.support_bundle collect || rc=$?
  if [[ "$rc" != 0 || -z "$RUN_OUT" ]]; then
    rm -rf -- "${bundle:?}"
    refuse "the container-side collect failed"
  fi
  printf '%s\n' "$RUN_OUT" >"$bundle/container.json"

  say "writing the manifest"
  {
    printf '{\n'
    printf '  "format_version": 1,\n'
    printf '  "created_at": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '  "files": [\n'
    local manifest_first=1
    while IFS= read -r -d '' f; do
      digest="$(sha256sum "$f" | awk '{ print $1 }')"
      [[ "$manifest_first" == 1 ]] || printf ',\n'
      manifest_first=0
      printf '    {"name": "%s", "sha256": "%s"}' "$(json_escape "${f#"$bundle"/}")" "$digest"
    done < <(find "$bundle" -type f -print0)
    printf '\n  ]\n'
    printf '}\n'
  } >"$bundle/MANIFEST.json"

  say "scanning the bundle (fail closed; the scan cannot be skipped)"
  # --user: the scan runs as the operator, who owns the bundle folder (mode
  # 700) and the env file (mode 600); the api image's own uid cannot read them
  rc=0
  run docker run --rm --network none \
    --user "$(id -u):$(id -g)" \
    --volume "$bundle:/bundle:ro" \
    --volume "$CONFIG:/onprem.env:ro" \
    --entrypoint python "$api" -m backend.support_bundle scan /bundle --env-file /onprem.env || rc=$?
  if [[ "$rc" == 1 ]]; then
    kinds="$(grep -oE 'kind=[a-z_]+' <<<"$RUN_OUT" | sort -u | tr '\n' ' ' || true)"
    say "REFUSED: the bundle scan found: ${kinds:-findings}"
    rm -rf -- "${bundle:?}"
    say "Nothing was written; no bundle exists."
    finish 2
  fi
  if [[ "$rc" == 2 ]]; then
    say "REFUSED: the bundle scan failed with a usage or read error (exit 2)"
    rm -rf -- "${bundle:?}"
    say "Nothing was written; no bundle exists."
    finish 2
  fi
  if [[ "$rc" != 0 ]]; then
    say "REFUSED: the scan failed (exit $rc)"
    rm -rf -- "${bundle:?}"
    say "Nothing was written; no bundle exists."
    finish 2
  fi
  record bundle S1 PASS "scan clean"

  # write with a restrictive umask to a temp name first, then move into
  # place: a failed tar leaves no partial bundle
  if ! (umask 077 && tar -czf "$out.tmp" -C "$bundle" .); then
    rm -f -- "${out:?}.tmp"
    rm -rf -- "${bundle:?}"
    refuse "packing $out failed"
  fi
  if ! mv -f -- "$out.tmp" "$out"; then
    rm -f -- "${out:?}.tmp"
    rm -rf -- "${bundle:?}"
    refuse "moving $out into place failed"
  fi
  chmod 600 "$out"
  rm -rf -- "${bundle:?}"
  say "Support bundle written to $out (mode 600, scanned clean)."
  finish 0
}

# --- status ----------------------------------------------------------------------------------------

cmd_status() {
  local sid rec="" ref
  [[ -d "$OPS" ]] || { echo "REFUSED: $OPS does not exist" >&2; exit 2; }
  require_layout
  say "current release: $(package_env "$CUR" HOMAYAR_RELEASE_VERSION) sha $(package_env "$CUR" HOMAYAR_RELEASE_SHA)"
  say "current folder: $CUR"
  for ref in API_IMAGE CONSOLE_WEB_IMAGE EGRESS_PROXY_IMAGE; do
    say "$ref: $(package_env "$CUR" "$ref") -> $(image_id "$(package_env "$CUR" "$ref")")"
  done
  say "compose ps:"
  stack_rows "$CUR" || true
  show "$STACK_ROWS"
  say "alembic version: $(db_version "$CUR" || printf 'unreadable')"
  for sid in "$STATES"/*; do
    [[ -f "$sid/record.env" ]] || continue
    sid="${sid##*/}"
    if [[ -z "$rec" || "$sid" > "$rec" ]]; then rec="$sid"; fi
  done
  if [[ -n "$rec" ]]; then
    say "latest state record: $rec"
    show "$(cat "$STATES/$rec/record.env" 2>/dev/null || true)"
  else
    say "latest state record: none"
  fi
  exit 0
}

# --- main -------------------------------------------------------------------------------------------

if ((BASH_VERSINFO[0] < 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 4))); then
  echo "needs bash >= 4.4" >&2
  exit 2
fi

CMD="${1:-}"
[[ -n "$CMD" ]] || { usage; exit 2; }
shift || true

trap on_exit EXIT
trap on_signal INT TERM

case "$CMD" in
--help | -h | help)
  usage
  exit 0
  ;;
pack) CMD="pack"; cmd_pack "$@" ;;
preflight) CMD="preflight"; cmd_preflight "$@" ;;
install) CMD="install"; cmd_install "$@" ;;
upgrade) CMD="upgrade"; cmd_upgrade "$@" ;;
rollback) CMD="rollback"; cmd_rollback "$@" ;;
backup) CMD="backup"; cmd_backup "$@" ;;
restore) CMD="restore"; cmd_restore "$@" ;;
support-bundle) CMD="support-bundle"; cmd_support_bundle "$@" ;;
status) CMD="status"; cmd_status "$@" ;;
*)
  usage
  exit 2
  ;;
esac
