#!/usr/bin/env bash
# HOM-358: update the app.homayar.com stack to a given main commit.
#
# Run on homayar-local as user gpu (never with sudo), with stdin from /dev/null:
#   bash ~/homayar/ops/app-server-release.sh --check  --release <sha40> </dev/null
#   bash ~/homayar/ops/app-server-release.sh --apply  --release <sha40> </dev/null
# A slow server link can break the image build (apt or PyPI). Then build the
# images with the GitHub workflow .github/workflows/release-images.yml, load
# them with docker load, and run instead:
#   bash ~/homayar/ops/app-server-release.sh --check  --release <sha40> --use-side-image </dev/null
#   bash ~/homayar/ops/app-server-release.sh --apply  --release <sha40> --use-side-image </dev/null
# The runbook shows the detached --apply form:
#   setsid nohup bash ~/homayar/ops/app-server-release.sh --apply --release <sha40> \
#     </dev/null >~/homayar/ops/release-<sha12>.out 2>&1 &
#
# What --apply does: it verifies the target release folder (SOURCE_SHA,
# build-args.txt, one migration head that reaches the database version, the
# STORAGE_PRESIGNED_DELIVERY pin), checks the stack health and the public URL,
# builds (or accepts) the two release images, then, with the writers stopped:
# dumps the database and proves the dump restores, writes the settings plan
# (S1, S2, S3), switches current, migrates, runs the HOM-290 trend backfill,
# starts everything and runs the after-checks C1 to C10. Any failure from the
# stop on starts the rollback: restore the dump, restore settings, switch
# current back, restart on the start images.
#
# Exit codes: 0 done or already done; 1 rolled back; 2 refused, or preflight
# or build failed (nothing live changed); 3 rollback failed (manual action
# needed); 4 applied and the local checks pass, but the public check (C9) is
# worse than the baseline (decide by hand).
# Needs bash 4.4 or later (the server has bash 5). SIGHUP and SIGPIPE are
# ignored; SIGINT or SIGTERM before the stop exits 2, during the change they
# start the rollback.
set -euo pipefail
export LC_ALL=C
trap '' HUP PIPE

# APPREL_TEST_HOME is only for the test harness. On the server it is never set.
H="${APPREL_TEST_HOME:-/home/gpu/homayar}"
SETTINGS="$H/config/settings.env"
SECRETS_ENV="$H/secrets/secrets.env"
OPS="$H/ops"
LOCK_FILE="$OPS/app-server-release.lock"
OTHER_LOCKS=(hom345.lock hom349.lock hom350.lock)

PROXY="http://127.0.0.1:8080"
HOST_HEADER="Host: app.homayar.com"
PUBLIC="https://app.homayar.com:8080"
PUBLIC_APP_TARGET="https://app.homayar.com:8080/app/"
OLD_ORIGIN="https://app.homayar.com"
NEW_ORIGIN="https://app.homayar.com:8080"
TRUSTED_TEST_IP="198.51.100.45"   # documentation range, never a real visitor
RELEASE_EMAIL="release-check@example.com"

T_CALL=60 T_DUMP=600 T_RESTORE=900 T_MIGRATE=900 T_BACKFILL=900 T_UP=600 T_BUILD=1800 T_KILL=30
T_RUN_V=120                       # the time bound of the V1/V2 docker run probes
WAIT_SECONDS=180                  # the longest wait for a healthy stack after up
MIN_DISK_KB=$((10 * 1024 * 1024)) # 10 GiB
LONG_SERVICES=(proxy console-web api worker postgres redis minio)

MODE="" RELEASE40="" SIDE=""
TS="$(date -u +%Y%m%dT%H%M%SZ)"
TMP="" LOG="" SETTINGS_TMP="" STATE="preflight"
RUN_OUT="" STACK_ROWS="" STACK_BAD=""
S_R="" S_S12="" T_R="" T_S12=""
DB_START_VERSION="" DB_AFTER=""
T_HEAD=""
API_TAG="" CW_TAG="" API_T_ID="" CW_T_ID="" API_S_ID="" CW_S_ID=""
REG_STATE="" REG_OLD="" PO_OLD="" CO_OLD="" PO_NEW="" CO_NEW="" PLAN_UNCHANGED=0
SETTINGS_ARG="" BACKUP="" BUILD_LOG="" MIGRATE_STARTED=""
DOWNTIME="(not measured)" DOWNTIME_START=0
BACKFILL_LINE="(not run)"
C9_BASE_H="" C9_BASE_A="" C9_ATTENTION=""
ALREADY="" P12_MODE=""
RESULTS=()

# --- output and command helpers -------------------------------------------------

say() {  # a line to the log file first, then to stdout; a dead stdout is ignored
  if [[ -n "$LOG" ]]; then printf '%s\n' "$*" >>"$LOG" || true; fi
  printf '%s\n' "$*" 2>/dev/null || true
}

say_err() {  # like say, but never to stdout: for functions whose stdout is
  # captured with $( ); diagnostics there would become the captured value
  if [[ -n "$LOG" ]]; then printf '%s\n' "$*" >>"$LOG" || true; fi
  printf '%s\n' "$*" >&2 2>/dev/null || true
}

show() {  # show captured output: indented, long hex strings hidden (20 or more,
  # like the runbook: the MinIO access key is 20 hex characters)
  [[ -n "$1" ]] || return 0
  say "$(printf '%s\n' "$1" | sed -E 's/[0-9a-f]{20,}/<redacted>/g' | sed 's/^/    | /')"
}

run() {  # run <cmd...>: status; output captured in RUN_OUT and shown filtered
  local status=0
  RUN_OUT="$( "$@" 2>&1 </dev/null )" || status=$?
  show "$RUN_OUT"
  return "$status"
}

run_in() {  # run_in <stdin-file> <cmd...>: like run, but stdin comes from the file
  local status=0
  local inf="$1"
  shift
  RUN_OUT="$( "$@" <"$inf" 2>&1 )" || status=$?
  show "$RUN_OUT"
  return "$status"
}

cap() {  # cap <cmd...>: like run but silent (for values that are only used)
  RUN_OUT=""
  RUN_OUT="$( "$@" </dev/null 2>/dev/null )" || return 1
  return 0
}

shielded() {  # only as `run shielded <cmd>`: the command and its children must
  # survive a SIGINT or SIGTERM aimed at the script (an interrupted up would
  # race with the rollback's own up)
  trap '' INT TERM
  "$@"
}

now() { date +%s; }

release_link_target() {  # the symlink target for a release folder: relative
  # (releases/<name>, like B5 and the runbook) when it is under $H, else absolute
  case "$1" in
    "$H"/*) printf '%s' "${1#"$H"/}" ;;
    *) printf '%s' "$1" ;;
  esac
}

random_tag() { od -An -N6 -tx1 /dev/urandom | tr -d ' \n'; }

# The build identity is read with a real parser (python) inside the api image or
# container, never with grep: the keys are nested (.source.sha, .schema.migration_head).
read -r -d '' V1_PY <<'V1PY' || true
import json, sys
expected_sha, expected_head = sys.argv[1], sys.argv[2]
with open("/app/build-info.json") as handle:
    document = json.load(handle)
sha = document["source"]["sha"]
head = document["schema"]["migration_head"]
print("V1IDENTITY sha=" + sha + " head=" + head)
sys.exit(0 if (sha == expected_sha and head == expected_head) else 1)
V1PY

read -r -d '' C4_PY <<'C4PY' || true
import json, sys
try:
    document = json.load(sys.stdin)
    sha = document["source"]["sha"]
    head = document["schema"]["migration_head"]
except Exception:
    print("C4IDENTITY unreadable")
    sys.exit(1)
ok = sha == sys.argv[1] and head == sys.argv[2]
print("C4IDENTITY sha=" + sha + " head=" + head)
sys.exit(0 if ok else 1)
C4PY

# --- docker helpers --------------------------------------------------------------

image_id() {  # the image ID of a reference, or empty
  cap timeout --kill-after="$T_KILL" "$T_CALL" docker image inspect --format '{{.Id}}' "$1" || return 0
  head -n 1 <<<"$RUN_OUT"
}

image_label() {  # the org.opencontainers.image.revision label of a reference
  cap timeout --kill-after="$T_KILL" "$T_CALL" \
    docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$1" || return 0
  head -n 1 <<<"$RUN_OUT"
}

docker_root() {  # the Docker root directory, or empty
  cap timeout --kill-after="$T_KILL" "$T_CALL" docker info --format '{{.DockerRootDir}}' || return 0
  head -n 1 <<<"$RUN_OUT"
}

dc() {  # dc <seconds> <release-folder> <compose args...>: exactly the command
  # line ~/homayar/hc runs (docs/operations/app-server.md), but for an explicit
  # release folder. stdin is /dev/null unless the caller uses run_in.
  local t="$1" r="$2" s12
  shift 2
  s12="$(head -c 12 "$r/SOURCE_SHA")"
  (
    cd "$r" &&
      API_IMAGE="homayar/api:$s12" CONSOLE_WEB_IMAGE="homayar/console-web:$s12" \
        timeout --kill-after="$T_KILL" "$t" docker compose \
          --env-file "${SETTINGS_ARG:-$SETTINGS}" --env-file "$SECRETS_ENV" \
          --file infra/compose/compose.onprem.yaml \
          --file infra/compose/compose.onprem.app-server.yaml \
          --profile onprem "$@"
  )
}

stack_rows() {  # stack_rows <release>: the ps rows of every service in STACK_ROWS
  run dc "$T_CALL" "$1" ps --all --format '{{.Service}}|{{.State}}|{{.Health}}|{{.ExitCode}}'
  STACK_ROWS="$RUN_OUT"
}

svc_row() {  # svc_row <name>: the row of one service, or empty
  awk -F'|' -v s="$1" '$1 == s { print; exit }' <<<"$STACK_ROWS"
}

check_rows() {  # every service in its wanted state (rows in STACK_ROWS)
  local s row
  for s in "${LONG_SERVICES[@]}"; do
    row="$(svc_row "$s")"
    [[ "$row" == "$s|running|healthy|"* ]] || { STACK_BAD="$s is '$row'"; return 1; }
  done
  row="$(svc_row scheduler)"
  [[ "$row" == "scheduler|running||"* ]] || { STACK_BAD="scheduler is '$row'"; return 1; }
  row="$(svc_row minio-bootstrap)"
  [[ "$row" == "minio-bootstrap|exited||0" ]] || { STACK_BAD="minio-bootstrap is '$row'"; return 1; }
  return 0
}

running_image_id() {  # running_image_id <release> <service>: the image ID, or empty
  local cid
  cap dc "$T_CALL" "$1" ps --all -q "$2" || return 0
  cid="$(head -n 1 <<<"$RUN_OUT")"
  [[ -n "$cid" ]] || return 0
  cap timeout --kill-after="$T_KILL" "$T_CALL" docker inspect --format '{{.Image}}' "$cid" || return 0
  head -n 1 <<<"$RUN_OUT"
}

printenv_of() {  # printenv_of <release> <service> <name>: the value, empty when unset
  cap dc "$T_CALL" "$1" exec -T "$2" printenv "$3" || return 0
  head -n 1 <<<"$RUN_OUT"
}

disk_free_kb() {  # disk_free_kb <path>: free KiB, falling back to / when not readable
  local out
  out="$(df -Pk "$1" 2>/dev/null | awk 'NR == 2 { print $4 }')" || true
  [[ -n "$out" ]] || out="$(df -Pk / | awk 'NR == 2 { print $4 }')"
  printf '%s' "$out"
}

bad_char_count() {  # bad_char_count <file> <char class>: how often the class
  # appears, not counting one final newline of the file
  local f="$1" cls="$2" total keep
  total="$(wc -c <"$f" | tr -d '[:space:]')"
  keep="$total"
  if [[ "$total" -gt 0 ]]; then
    if [[ "$(tail -c 1 "$f" | od -An -tu1 | tr -d '[:space:]')" == 10 ]]; then
      keep=$((total - 1))
    fi
  fi
  head -c "$keep" "$f" | tr -cd "$cls" | wc -c | tr -d '[:space:]'
}

http_code() {  # http_code <seconds> <outfile> <curl args...>: the status, 000 when no answer
  local code
  code="$(curl -sS --max-time "$1" -o "$2" -w '%{http_code}' "${@:3}" </dev/null 2>/dev/null || true)"
  printf '%s' "${code:-000}"
}

http_w() {  # http_w <seconds> <format> <curl args...>: the formatted curl result
  curl -sS --max-time "$1" -o /dev/null -w "$2" "${@:3}" </dev/null 2>/dev/null || true
}

log_address() {  # the remote_addr of the proxy log line for this uri, empty if none.
  # silent (cap, not run): this function's stdout is captured by the caller
  local uri="$1" line="" _try
  for _try in 1 2 3 4 5; do
    cap dc "$T_CALL" "$T_R" logs --no-log-prefix --since 5m proxy || true
    line="$(grep -F "\"uri\":\"$uri\"" <<<"$RUN_OUT" | tail -n 1)" || true
    if [[ -n "$line" ]]; then break; fi
    sleep 1
  done
  printf '%s' "$line" | sed -n 's/.*"remote_addr":"\([^"]*\)".*/\1/p'
}

# --- the migration head of a release folder ---------------------------------------

revision_of() {  # revision_of <file> <which>: the revision or down_revision literal.
  # Accepts both quote styles the real migrations use ("x" and 'x'); the id
  # must not contain a quote. Tuples (merge migrations) would not match; there
  # are none on main today.
  sed -n "s/^[[:space:]]*$2[[:space:]]*\\(:[^=]*\\)\\{0,1\\}[[:space:]]*=[[:space:]]*\\([\"']\\)\\([^\"']*\\)\\2.*/\\3/p" "$1" | head -n 1
}

compute_head() {  # compute_head <release-folder>: 0 ok; sets T_HEAD; prints why not
  local dir="$1/Homayar/backend/migrations/versions" f rev down
  local revs="$TMP/revs.txt"
  : >"$revs"
  [[ -d "$dir" ]] || { say "no migrations folder: $dir"; return 1; }
  for f in "$dir"/*.py; do
    [[ -e "$f" ]] || continue
    rev="$(revision_of "$f" revision)"
    down="$(revision_of "$f" down_revision)"
    [[ -n "$rev" ]] || { say "no revision literal in $f"; return 1; }
    printf '%s %s\n' "$rev" "${down:-ROOT}" >>"$revs"
  done
  if [[ "$(cut -d' ' -f1 "$revs" | sort | uniq -d | wc -l | tr -d '[:space:]')" != 0 ]]; then
    say "a revision id appears in more than one migration file"
    return 1
  fi
  cut -d' ' -f1 "$revs" | sort >"$TMP/revs.a"
  grep -vx ROOT "$revs" | cut -d' ' -f2 | sort -u >"$TMP/revs.b"
  local heads n cur next steps max
  heads="$(comm -23 "$TMP/revs.a" "$TMP/revs.b")"
  n="$(wc -l <<<"$heads" | tr -d '[:space:]')"
  [[ "$n" == 1 ]] || { say "the target has $n migration heads; expected exactly 1"; return 1; }
  T_HEAD="$(head -n 1 <<<"$heads")"
  # walk the chain from the head back to ROOT; the database version must be in it
  max="$(wc -l <"$revs" | tr -d '[:space:]')"
  cur="$T_HEAD" steps=0
  while [[ "$cur" != ROOT ]]; do
    steps=$((steps + 1))
    if [[ "$steps" -gt "$max" ]]; then say "the down_revision chain loops"; return 1; fi
    if [[ "$cur" == "$DB_START_VERSION" ]]; then
      return 0
    fi
    next="$(awk -v r="$cur" '$1 == r { print $2; exit }' "$revs")"
    [[ -n "$next" ]] || { say "down_revision $next of $cur is not a known revision"; return 1; }
    cur="$next"
  done
  say "the database version $DB_START_VERSION is not in the target chain"
  return 1
}

# --- the settings plan (rules S1 to S3) -------------------------------------------

plan_settings() {  # 0 ok; sets REG_STATE PO_OLD CO_OLD PO_NEW CO_NEW PLAN_UNCHANGED
  local k n
  for k in REGISTRATION_ENABLED PUBLIC_ORIGIN CORS_ORIGINS; do
    n="$(grep -c "^$k=" "$SETTINGS" || true)"
    [[ "$n" -le 1 ]] || { say "REFUSED: $SETTINGS has $n $k lines; expected at most 1"; return 1; }
  done
  PO_OLD="$(sed -n 's/^PUBLIC_ORIGIN=//p' "$SETTINGS")"
  CO_OLD="$(sed -n 's/^CORS_ORIGINS=//p' "$SETTINGS")"
  case "$PO_OLD|$CO_OLD" in
    "$OLD_ORIGIN|$OLD_ORIGIN")
      PO_NEW="$NEW_ORIGIN" CO_NEW="$NEW_ORIGIN" ;;
    "$NEW_ORIGIN|$NEW_ORIGIN")
      PO_NEW="$PO_OLD" CO_NEW="$CO_OLD" ;;
    *)
      say "REFUSED: PUBLIC_ORIGIN '$PO_OLD' and CORS_ORIGINS '$CO_OLD' are mixed or unexpected;"
      say "expected both $OLD_ORIGIN (before HOM-374) or both $NEW_ORIGIN (after it)."
      return 1
      ;;
  esac
  if grep -q '^REGISTRATION_ENABLED=false$' "$SETTINGS"; then
    REG_STATE=unchanged
  elif grep -q '^REGISTRATION_ENABLED=' "$SETTINGS"; then
    REG_STATE="set"
  else
    REG_STATE=append
  fi
  # the old value is saved here: after B4 writes the plan, the live file can
  # no longer answer what it was
  if [[ "$REG_STATE" == append ]]; then
    REG_OLD="(absent)"
  else
    REG_OLD="$(sed -n 's/^REGISTRATION_ENABLED=//p' "$SETTINGS")"
  fi
  if [[ "$REG_STATE" == unchanged && "$PO_NEW" == "$PO_OLD" && "$CO_NEW" == "$CO_OLD" ]]; then
    PLAN_UNCHANGED=1
  fi
  return 0
}

show_plan() {
  if [[ -z "$REG_STATE" ]]; then
    say "  (no settings plan was computed)"
    return 0
  fi
  if [[ "$REG_STATE" == append ]]; then
    say "  REGISTRATION_ENABLED: absent -> false"
  elif [[ "$REG_STATE" == "set" ]]; then
    say "  REGISTRATION_ENABLED: $REG_OLD -> false"
  else
    say "  REGISTRATION_ENABLED: unchanged"
  fi
  if [[ "$PO_NEW" == "$PO_OLD" ]]; then
    say "  PUBLIC_ORIGIN: unchanged"
  else
    say "  PUBLIC_ORIGIN: $PO_OLD -> $PO_NEW"
  fi
  if [[ "$CO_NEW" == "$CO_OLD" ]]; then
    say "  CORS_ORIGINS: unchanged"
  else
    say "  CORS_ORIGINS: $CO_OLD -> $CO_NEW"
  fi
}

apply_plan() {  # apply_plan <src>: the new settings content on stdout; nothing
  # else changes, every other line and the line order are kept
  if [[ "$REG_STATE" == append ]]; then
    sed -e "s|^PUBLIC_ORIGIN=.*\$|PUBLIC_ORIGIN=$PO_NEW|" -e "s|^CORS_ORIGINS=.*\$|CORS_ORIGINS=$CO_NEW|" "$1"
    printf 'REGISTRATION_ENABLED=false\n'
  else
    sed -e "s|^PUBLIC_ORIGIN=.*\$|PUBLIC_ORIGIN=$PO_NEW|" \
      -e "s|^CORS_ORIGINS=.*\$|CORS_ORIGINS=$CO_NEW|" \
      -e "s|^REGISTRATION_ENABLED=.*\$|REGISTRATION_ENABLED=false|" "$1"
  fi
}

write_settings() {  # the atomic write (temp file in the same folder, mode 600, mv)
  local tmpf
  tmpf="$(mktemp "$H/config/.settings.release.XXXXXX")"
  if ! apply_plan "$SETTINGS" >"$tmpf"; then
    rm -f -- "$tmpf"
    return 1
  fi
  chmod 600 "$tmpf"
  mv -f -- "$tmpf" "$SETTINGS"
  # read back
  [[ "$(sed -n 's/^PUBLIC_ORIGIN=//p' "$SETTINGS")" == "$PO_NEW" &&
    "$(sed -n 's/^CORS_ORIGINS=//p' "$SETTINGS")" == "$CO_NEW" &&
    "$(sed -n 's/^REGISTRATION_ENABLED=//p' "$SETTINGS")" == false ]]
}

# --- checks -----------------------------------------------------------------------

record() {  # record <phase> <check> <result> <detail>
  RESULTS+=("$1 $2 $3: $4")
  say "  $2 $3: $4"
}

db_version() {  # db_version <release>: the alembic version row (verified: exactly one);
  # prints only the value, so callers can capture it (diagnostics to stderr)
  cap dc "$T_CALL" "$1" exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select version_num from alembic_version"' || {
    say_err "the alembic_version query failed"
    return 1
  }
  local rows
  rows="$(grep -c . <<<"$RUN_OUT" || true)"
  [[ "$rows" == 1 ]] || { say_err "the alembic_version query returned $rows rows"; return 1; }
  tail -n 1 <<<"$RUN_OUT"
}

wait_healthy() {  # wait_healthy <release> <downtime-var>: 0 when healthy and /readyz 200
  local deadline ready="" start
  start="$(now)"
  deadline=$((start + WAIT_SECONDS))
  while [[ "$(now)" -lt "$deadline" ]]; do
    if [[ -z "$ready" ]]; then
      if [[ "$(http_code 5 "$TMP/body" -H "$HOST_HEADER" "$PROXY/readyz")" == 200 ]]; then
        ready=1
        printf -v "$2" '%s s' "$(( $(now) - DOWNTIME_START ))"
      fi
    fi
    if [[ -n "$ready" ]]; then
      stack_rows "$1"
      if check_rows; then return 0; fi
    fi
    sleep 1
  done
  say "the stack was not healthy with /readyz 200 within $WAIT_SECONDS s"
  return 1
}

verify_images() {  # V1 and V2; when $1 is "differ", the new IDs must differ
  local mode="${1:-}" lab
  API_T_ID="$(image_id "$API_TAG")"
  CW_T_ID="$(image_id "$CW_TAG")"
  if [[ -z "$API_T_ID" || -z "$CW_T_ID" ]]; then
    say "V1/V2 FAIL: $API_TAG and $CW_TAG must both exist here"
    return 1
  fi
  say "Target images: $API_TAG $API_T_ID, $CW_TAG $CW_T_ID"
  if run timeout --kill-after="$T_KILL" "$T_RUN_V" docker run --rm --network none \
    --entrypoint python "$API_TAG" -c "$V1_PY" "$RELEASE40" "$T_HEAD"; then
    say "V1 PASS: api build-info .source.sha and .schema.migration_head match the release"
  else
    say "V1 FAIL: api build-info .source.sha or .schema.migration_head does not match"
    return 1
  fi
  lab="$(image_label "$API_TAG")"
  if [[ "$lab" == "$RELEASE40" ]]; then
    say "V1 PASS: api revision label matches the release"
  else
    say "V1 FAIL: api revision label is '$lab'"
    return 1
  fi
  lab="$(image_label "$CW_TAG")"
  if [[ "$lab" == "$RELEASE40" ]]; then
    say "V2 label PASS: console-web revision label matches the release"
  else
    say "V2 label FAIL: console-web revision label is '$lab'"
    return 1
  fi
  if run timeout --kill-after="$T_KILL" "$T_RUN_V" docker run --rm --network none \
    --entrypoint sh "$CW_TAG" -c 'test -f /usr/share/nginx/html/app/index.html'; then
    say "V2 PASS: console-web serves /usr/share/nginx/html/app/index.html"
  else
    say "V2 FAIL: no /usr/share/nginx/html/app/index.html in $CW_TAG"
    return 1
  fi
  if [[ "$mode" == differ ]]; then
    if [[ "$API_T_ID" == "$API_S_ID" || "$CW_T_ID" == "$CW_S_ID" ]]; then
      say "V1/V2 FAIL: a target image ID equals the start release running image ID"
      say "  api $API_T_ID (start $API_S_ID), console-web $CW_T_ID (start $CW_S_ID)"
      return 1
    fi
  fi
  return 0
}

# --- preflight ----------------------------------------------------------------------

usage() {
  echo "usage: bash $0 --check|--apply --release <sha40> [--use-side-image] </dev/null"
  echo "  --check           read-only preflight and plan; changes nothing"
  echo "  --apply           update the stack to the release, with a built-in rollback"
  echo "  --release <sha40> the full commit sha of the staged target release"
  echo "  --use-side-image  do not build; require the images loaded by the"
  echo "                    release-images.yml workflow (docker load)"
}

refuse() {  # exit 2: nothing was changed
  say "REFUSED: $*"
  say "Nothing was changed."
  finish 2
}

side_image_help() {
  say "When the build cannot run on the server, build the images on GitHub instead:"
  say "  1. dispatch the workflow .github/workflows/release-images.yml with the ref"
  say "     $RELEASE40 (Actions tab, 'Release images', Run workflow)."
  say "  2. download the artifact release-images-$T_S12 (gh run download, or a"
  say "     resumable curl --continue-at - loop) and check SHA256SUMS."
  say "  3. load both images: gunzip -c homayar-api-$T_S12.tar.gz | docker load"
  say "     and the same for homayar-console-web-$T_S12.tar.gz"
  say "  4. rerun this script with --check --use-side-image, then"
  say "     --apply --use-side-image."
}

preflight() {
  local uid user f n line root free1 free2

  # P1: identity
  uid="$(id -u)"
  [[ "$uid" != 0 ]] || { say "REFUSED: running as root; run as gpu, without sudo"; say "Nothing was changed."; exit 2; }
  user="$(id -un)"
  [[ "$user" == gpu ]] || { say "REFUSED: running as '$user'; run as gpu"; say "Nothing was changed."; exit 2; }
  [[ -d "$OPS" ]] || { say "REFUSED: $OPS does not exist"; say "Nothing was changed."; exit 2; }
  LOG="$OPS/release-${RELEASE40:0:12}-$TS.log"
  (umask 077 && : >>"$LOG") 2>/dev/null || { echo "REFUSED: cannot write the log file in $OPS" >&2; exit 2; }
  say "HOM-358 release update $MODE, release ${RELEASE40:0:12}, log $LOG"
  exec 9>>"$LOCK_FILE"
  if ! flock -n 9; then
    say "REFUSED: another run holds $LOCK_FILE."
    say "Nothing was changed."
    exit 2
  fi
  for f in "${OTHER_LOCKS[@]}"; do
    if [[ -e "$OPS/$f" ]]; then
      if ! flock -n "$OPS/$f" true 2>/dev/null; then
        say "REFUSED: $OPS/$f is held by another process (an earlier ops script may run)."
        say "Nothing was changed."
        exit 2
      fi
    fi
  done
  record P P1 PASS "uid $uid ($user), bash ${BASH_VERSINFO[0]}.${BASH_VERSINFO[1]}, args valid, lock free"

  # P2: the start release
  S_R="$(readlink -f "$H/current" 2>/dev/null || true)"
  [[ -n "$S_R" && -d "$S_R" && -f "$S_R/SOURCE_SHA" ]] || refuse "$H/current does not resolve to a release folder with SOURCE_SHA"
  S_S12="$(head -c 12 "$S_R/SOURCE_SHA")"
  [[ "$S_S12" =~ ^[0-9a-f]{12}$ ]] || refuse "SOURCE_SHA of $S_R does not start with 12 lowercase hex characters"
  record P P2 PASS "start release $S_R ($S_S12)"

  # P3: the target release folder
  T_R="$H/releases/${RELEASE40:0:12}"
  [[ -d "$T_R" ]] || refuse "the target release folder $T_R does not exist; stage it first (runbook, update step 1)"
  [[ -f "$T_R/SOURCE_SHA" ]] || refuse "$T_R has no SOURCE_SHA"
  [[ "$(tr -d '[:space:]' <"$T_R/SOURCE_SHA")" == "$RELEASE40" ]] ||
    refuse "SOURCE_SHA of $T_R does not equal --release"
  [[ -f "$T_R/build-args.txt" ]] || refuse "$T_R has no build-args.txt (stage it with release_manifest.py build-args)"
  n="$(grep -c . "$T_R/build-args.txt" || true)"
  [[ "$n" == 6 ]] || refuse "$T_R/build-args.txt has $n non-empty lines; expected exactly 6"
  for f in SOURCE_SHA SOURCE_DATE_EPOCH SOURCE_DATE RELEASE_VERSION RELEASE_PROFILE INSTALL_AI_LOCAL; do
    grep -q "^$f=" "$T_R/build-args.txt" || refuse "build-args.txt has no $f line"
  done
  n="$(cut -d= -f1 "$T_R/build-args.txt" | sort | uniq | wc -l | tr -d '[:space:]')"
  [[ "$n" == 6 ]] || refuse "build-args.txt has a duplicate or an unknown name"
  grep -q '^SOURCE_SHA='"$RELEASE40"'$' "$T_R/build-args.txt" ||
    refuse "the SOURCE_SHA in build-args.txt does not equal --release"
  grep -q '^INSTALL_AI_LOCAL=false$' "$T_R/build-args.txt" ||
    refuse "INSTALL_AI_LOCAL in build-args.txt is not false; this script builds the standard image"
  T_S12="${RELEASE40:0:12}"
  record P P3 PASS "target release $T_R with a valid build-args.txt"

  # P5 first (P4 needs the database version)
  DB_START_VERSION="$(db_version "$S_R")" || refuse "could not read the database version"
  record P P5 PASS "database version $DB_START_VERSION"

  # P4: the target migration head and the chain
  if compute_head "$T_R"; then
    record P P4 PASS "one head $T_HEAD, the chain reaches the database version"
  else
    record P P4 FAIL "the target migrations do not fit this database"
    refuse "the target migration chain does not fit the database"
  fi

  # P6: the stack health of the start release
  API_S_ID="$(running_image_id "$S_R" api)"
  CW_S_ID="$(running_image_id "$S_R" console-web)"
  [[ -n "$API_S_ID" && -n "$CW_S_ID" ]] || refuse "could not read the running image IDs of api and console-web"
  stack_rows "$S_R"
  if check_rows; then
    record P P6 PASS "every service running healthy (scheduler running, minio-bootstrap exited 0)"
  else
    record P P6 FAIL "$STACK_BAD"
    refuse "the stack is not healthy on the start release"
  fi
  n="$(http_code 10 "$TMP/body" -H "$HOST_HEADER" "$PROXY/healthz")"
  [[ "$n" == 200 ]] || { record P P6 FAIL "local /healthz answers $n"; refuse "local /healthz is not 200"; }
  n="$(http_code 10 "$TMP/body" -H "$HOST_HEADER" "$PROXY/readyz")"
  [[ "$n" == 200 ]] || { record P P6 FAIL "local /readyz answers $n"; refuse "local /readyz is not 200"; }
  record P P6 PASS "local /healthz and /readyz answer 200"

  # P7: disk
  root="$(docker_root)"
  free1="$(disk_free_kb "${root:-/nonexistent}")"
  free2="$(disk_free_kb "$H")"
  [[ "$free1" =~ ^[0-9]+$ && "$free1" -ge "$MIN_DISK_KB" ]] ||
    refuse "free space under the Docker root '${root:-/}' is ${free1:-?} KiB; need $MIN_DISK_KB"
  [[ "$free2" =~ ^[0-9]+$ && "$free2" -ge "$MIN_DISK_KB" ]] ||
    refuse "free space under $H is ${free2:-?} KiB; need $MIN_DISK_KB"
  record P P7 PASS "free ${free1} KiB under ${root:-/}, ${free2} KiB under $H"

  # P8: the MinIO keys, counts only (HOM-340 rollout step 2)
  for f in minio_root_user minio_root_password; do
    n="$(bad_char_count "$H/secrets/$f" ':\r\n')"
    [[ "$n" == 0 ]] || refuse "secrets/$f has $n forbidden characters (':', a line break or a carriage return); rotate the key first"
  done
  for f in storage_access_key storage_secret_key; do
    n="$(bad_char_count "$H/secrets/$f" '\r\n')"
    [[ "$n" == 0 ]] || refuse "secrets/$f has $n forbidden characters (a line break or a carriage return); rotate the key first"
  done
  record P P8 PASS "the MinIO key files hold no forbidden characters (counts only, never printed)"

  # P9: the target compose file pins streamed delivery
  n="$(grep -c 'STORAGE_PRESIGNED_DELIVERY: "false"' "$T_R/infra/compose/compose.onprem.yaml" || true)"
  [[ "$n" == 1 ]] || refuse "grep -c STORAGE_PRESIGNED_DELIVERY in the target compose file printed $n, expected 1"
  record P P9 PASS 'the target compose file pins STORAGE_PRESIGNED_DELIVERY: "false"'

  # P10: the settings plan
  if plan_settings; then
    record P P10 PASS "the settings plan is computable"
    show_plan
  else
    record P P10 FAIL "the settings plan conflicts with $SETTINGS"
    refuse "the settings plan conflicts with $SETTINGS"
  fi

  # P11: the target config renders with the planned settings
  SETTINGS_TMP="$(mktemp "$H/config/.settings.plan.XXXXXX")"
  apply_plan "$SETTINGS" >"$SETTINGS_TMP"
  chmod 600 "$SETTINGS_TMP"
  SETTINGS_ARG="$SETTINGS_TMP"
  if ! run dc "$T_CALL" "$T_R" config --quiet; then
    SETTINGS_ARG=""
    rm -f -- "$SETTINGS_TMP"
    SETTINGS_TMP=""
    record P P11 FAIL "docker compose config --quiet failed"
    refuse "the target compose config does not render"
  fi
  SETTINGS_ARG=""
  rm -f -- "$SETTINGS_TMP"
  SETTINGS_TMP=""
  record P P11 PASS "the target compose config renders with the planned settings"

  # P12: the images
  API_TAG="homayar/api:$T_S12"
  CW_TAG="homayar/console-web:$T_S12"
  if [[ -n "$(image_id "$API_TAG")" && -n "$(image_id "$CW_TAG")" ]]; then
    if verify_images; then
      P12_MODE=present
      record P P12 PASS "both target images are present and pass V1/V2 (a rerun; no build)"
    elif [[ -n "$SIDE" ]]; then
      record P P12 FAIL "the present side images fail V1/V2"
      refuse "--use-side-image: $API_TAG and $CW_TAG are present but fail V1/V2; load the correct side images first"
    elif [[ "$S_R" == "$T_R" ]]; then
      record P P12 FAIL "the running release equals the target and its images fail V1/V2"
      refuse "current already points to the target ($T_R) and its images fail V1/V2; a rebuild would re-tag the live image, so decide by hand (rebuild or roll back without this script)"
    else
      P12_MODE=build
      record P P12 PASS "the present target images fail V1/V2; a rebuild is planned (the build re-tags)"
    fi
  else
    if [[ -n "$SIDE" ]]; then
      record P P12 FAIL "--use-side-image needs both target images"
      refuse "--use-side-image needs $API_TAG and $CW_TAG present (load the side images first, see the runbook)"
    fi
    P12_MODE=build
    record P P12 PASS "a target image is missing; a build is planned"
  fi

  # P13: the public baseline (record only, never a refusal)
  C9_BASE_H="$(http_w 15 '%{http_code} %{ssl_verify_result}' "$PUBLIC/healthz")"
  C9_BASE_A="$(http_w 15 '%{http_code} %{redirect_url}' "$PUBLIC/app")"
  if [[ "$C9_BASE_H" == "200 0" ]]; then
    record P P13 PASS "public /healthz '$C9_BASE_H', /app '$C9_BASE_A'"
  else
    record P P13 PASS "record only: public /healthz '$C9_BASE_H', /app '$C9_BASE_A'"
  fi

  # P14: already done?
  if [[ "$S_R" == "$T_R" && "$DB_START_VERSION" == "$T_HEAD" && "$PLAN_UNCHANGED" == 1 ]]; then
    ALREADY=1
  fi
}

public_check() {  # C9: never a rollback reason; sets C9_ATTENTION when worse.
  # /healthz and /app are judged separately, each against its own P13 baseline.
  local phase="$1" h a h_ok a_ok detail=""
  h="$(http_w 15 '%{http_code} %{ssl_verify_result}' "$PUBLIC/healthz")"
  a="$(http_w 15 '%{http_code} %{redirect_url}' "$PUBLIC/app")"
  if [[ "$h" == "200 0" ]]; then
    h_ok=1
  elif [[ "$C9_BASE_H" != "200 0" && "$h" == "$C9_BASE_H" ]]; then
    h_ok=1  # same as an already broken baseline: not worse
  else
    h_ok=""
    detail="public /healthz '$h' is worse than its baseline '$C9_BASE_H'"
  fi
  if [[ "$a" == "308 $PUBLIC_APP_TARGET" ]]; then
    a_ok=1
  elif [[ "$C9_BASE_A" != "308 $PUBLIC_APP_TARGET" && "$a" == "$C9_BASE_A" ]]; then
    a_ok=1  # same as an already broken baseline: not worse
  else
    a_ok=""
    detail="${detail:+$detail; }public /app '$a' is worse than its baseline '$C9_BASE_A'"
  fi
  if [[ -n "$h_ok" && -n "$a_ok" ]]; then
    record "$phase" C9 PASS "public /healthz '$h', /app '$a' (each at least as good as its baseline)"
    return 0
  fi
  record "$phase" C9 FAIL "$detail"
  C9_ATTENTION=1
  return 0
}

# --- phase C ------------------------------------------------------------------------

c1_images() {  # c1_images <phase> <release> <api image id> <console image id>
  local phase="$1" rel="$2" want_api="$3" want_cw="$4" s got fail=0
  stack_rows "$rel"
  if check_rows; then
    :
  else
    record "$phase" C1 FAIL "$STACK_BAD"
    return 1
  fi
  for s in api worker scheduler; do
    got="$(running_image_id "$rel" "$s")"
    if [[ -z "$got" || "$got" != "$want_api" ]]; then
      record "$phase" C1 FAIL "$s runs '$got', expected $want_api"
      fail=1
    fi
  done
  got="$(running_image_id "$rel" console-web)"
  if [[ -z "$got" || "$got" != "$want_cw" ]]; then
    record "$phase" C1 FAIL "console-web runs '$got', expected $want_cw"
    fail=1
  fi
  if [[ "$fail" == 1 ]]; then return 1; fi
  record "$phase" C1 PASS "api, worker, scheduler on $want_api, console-web on $want_cw, all healthy"
  return 0
}

run_after_checks() {  # C1 to C10; 0 when C1 to C8 pass
  local fail=0 code body="$TMP/body" r tag addr svc val
  say "Checks (after):"

  c1_images after "$T_R" "$API_T_ID" "$CW_T_ID" || fail=1

  code="$(http_code 10 "$body" -H "$HOST_HEADER" "$PROXY/healthz")"
  if [[ "$code" == 200 ]]; then record after C2 PASS "local /healthz $code"; else
    record after C2 FAIL "local /healthz $code"; fail=1
  fi

  code="$(http_code 10 "$body" -H "$HOST_HEADER" "$PROXY/readyz")"
  if [[ "$code" == 200 ]] && grep -q database "$body" && grep -q redis "$body" && grep -qi ready "$body"; then
    record after C3 PASS "local /readyz $code, database and redis ready"
  else
    record after C3 FAIL "local /readyz $code"
    fail=1
  fi

  if run dc "$T_CALL" "$T_R" exec -T api sh -c \
    'python -m backend.observability.release show | python -c "$0" "$1" "$2"' \
    "$C4_PY" "$RELEASE40" "$T_HEAD"; then
    record after C4 PASS "the running api reports the release sha and the migration head"
  else
    record after C4 FAIL "release show does not report the release sha and migration head"
    fail=1
  fi

  for svc in api worker; do
    val="$(printenv_of "$T_R" "$svc" STORAGE_PRESIGNED_DELIVERY)"
    [[ "$val" == false ]] || { record after C5 FAIL "$svc STORAGE_PRESIGNED_DELIVERY is '$val'"; fail=1; }
    val="$(printenv_of "$T_R" "$svc" REGISTRATION_ENABLED)"
    [[ "$val" == false ]] || { record after C5 FAIL "$svc REGISTRATION_ENABLED is '$val'"; fail=1; }
    val="$(printenv_of "$T_R" "$svc" PUBLIC_URL)"
    [[ "$val" == "$PO_NEW" ]] || { record after C5 FAIL "$svc PUBLIC_URL is '$val', expected $PO_NEW"; fail=1; }
    val="$(printenv_of "$T_R" "$svc" ASR_PROFILE)"
    [[ -z "$val" ]] || { record after C5 FAIL "$svc ASR_PROFILE is '$val', expected empty"; fail=1; }
    if run dc "$T_CALL" "$T_R" exec -T "$svc" sh -c 'test -z "$WEBHOOK_SECRET_KEYS"'; then
      :
    else
      record after C5 FAIL "$svc WEBHOOK_SECRET_KEYS is not empty"
      fail=1
    fi
  done
  [[ "$fail" == 1 ]] || record after C5 PASS "api and worker run the planned env (delivery, sign-up, origin, empty ASR and webhook keys)"

  code="$(http_code 10 "$body" -H "$HOST_HEADER" "$PROXY/api/v1/auth/methods")"
  if [[ "$code" == 200 ]] && grep -Eq '"registration"[[:space:]]*:[[:space:]]*false' "$body"; then
    printf '{"email":"%s","password":"release-check-P4ssw0rd","name":"Release Check"}\n' "$RELEASE_EMAIL" >"$TMP/reg.json"
    code="$(http_code 15 "$body" -H "$HOST_HEADER" -H 'Content-Type: application/json' \
      --data-binary "@$TMP/reg.json" "$PROXY/api/v1/auth/register")"
    if [[ "$code" == 403 ]] && grep -q registration_closed "$body"; then
      record after C6 PASS "sign-up closed: methods says registration false, register answers 403 registration_closed"
    else
      record after C6 FAIL "register answered $code (expected 403 registration_closed)"
      fail=1
    fi
  else
    record after C6 FAIL "/api/v1/auth/methods answered $code without registration false; no register POST was sent"
    fail=1
  fi

  r="$(http_w 10 '%{http_code} %{content_type}' -H "$HOST_HEADER" "$PROXY/app/")"
  if [[ "$r" == "200 text/html"* ]]; then
    record after C7 PASS "/app/ answers 200 text/html"
  else
    record after C7 FAIL "/app/ answers '$r'"
    fail=1
  fi
  r="$(http_w 10 '%{http_code} %{redirect_url}' -H "$HOST_HEADER" "$PROXY/app")"
  if [[ "$r" == "308 "*"/app/" ]]; then
    record after C7 PASS "/app answers 308 to $r"
  else
    record after C7 FAIL "/app answers '$r'"
    fail=1
  fi

  printf '{"email":"%s","password":"wrong-password-123A"}\n' "$RELEASE_EMAIL" >"$TMP/login.json"
  code="$(http_code 15 "$body" -H "$HOST_HEADER" -H 'Content-Type: application/json' \
    --data-binary "@$TMP/login.json" "$PROXY/api/v1/auth/login")"
  if [[ "$code" == 401 ]] && grep -q invalid_credentials "$body"; then
    record after C8 PASS "login with a wrong password answers 401 invalid_credentials"
  else
    record after C8 FAIL "login answered $code"
    fail=1
  fi
  head -c $((17 * 1024 * 1024)) /dev/zero >"$TMP/cap17"
  head -c $((1 * 1024 * 1024)) /dev/zero >"$TMP/cap1"
  code="$(http_code 30 /dev/null -H "$HOST_HEADER" -H 'Content-Type: application/octet-stream' \
    --data-binary "@$TMP/cap17" "$PROXY/api/v1/hom358-cap-probe")"
  r="$(http_code 30 /dev/null -H "$HOST_HEADER" -H 'Content-Type: application/octet-stream' \
    --data-binary "@$TMP/cap1" "$PROXY/api/v1/hom358-cap-probe")"
  if [[ "$code" == 413 && "$r" != 413 && "$r" != 000 ]]; then
    record after C8 PASS "the 16 MiB body cap holds (17 MiB -> $code, 1 MiB -> $r)"
  else
    record after C8 FAIL "body cap: 17 MiB -> $code, 1 MiB -> $r"
    fail=1
  fi
  tag="$(random_tag)"
  code="$(http_code 10 /dev/null -H "$HOST_HEADER" -H "X-Real-IP: $TRUSTED_TEST_IP" "$PROXY/api/v1/hom358-ip-$tag")"
  addr="$(log_address "/api/v1/hom358-ip-$tag")"
  if [[ "$addr" == "$TRUSTED_TEST_IP" ]]; then
    record after C8 PASS "the trusted X-Real-IP $addr is logged as remote_addr (kept from HOM-350)"
  else
    record after C8 FAIL "remote_addr '$addr', expected $TRUSTED_TEST_IP"
    fail=1
  fi

  public_check after

  code=0
  run dc "$T_BACKFILL" "$T_R" run --rm -T --no-deps api \
    python -m backend.jobs.model_preflight --host --topology single-host-p40 || code=$?
  record after C10 PASS "host preflight exit $code recorded (exit 2 intelligence_provider_unconfigured is expected today)"

  return "$fail"
}

# --- phase A ------------------------------------------------------------------------

build_images() {  # one build at a time, capped like the runbook, output to the build log
  local args=() line status
  BUILD_LOG="$OPS/release-$T_S12-$TS.build.txt"
  : >"$BUILD_LOG"
  chmod 600 "$BUILD_LOG"

  say "Building $API_TAG in $T_R (at most $T_BUILD s; output in $BUILD_LOG)"
  while IFS= read -r line; do
    args+=(--build-arg "$line")
  done <"$T_R/build-args.txt"
  status=0
  (cd "$T_R" && DOCKER_BUILDKIT=0 timeout --kill-after="$T_KILL" "$T_BUILD" \
    docker build --memory 3g --memory-swap 3g --cpu-period 100000 --cpu-quota 400000 \
      -f infra/docker/api.Dockerfile "${args[@]}" -t "$API_TAG" .) \
    </dev/null 2>&1 | sed -E 's/[0-9a-f]{20,}/<redacted>/g' >"$BUILD_LOG" || status=$?
  if [[ "$status" != 0 ]]; then
    say "The api build failed or timed out (exit $status). Last lines:"
    show "$(tail -n 15 "$BUILD_LOG")"
    return 1
  fi

  say "Building $CW_TAG in $T_R (at most $T_BUILD s; output in $BUILD_LOG)"
  status=0
  (cd "$T_R" && DOCKER_BUILDKIT=0 timeout --kill-after="$T_KILL" "$T_BUILD" \
    docker build --memory 3g --memory-swap 3g --cpu-period 100000 --cpu-quota 400000 \
      -f infra/docker/console-web.Dockerfile \
      --build-arg VITE_API_URL=/api/v1 --build-arg VITE_BASE_PATH=/app/ \
      --label "org.opencontainers.image.revision=$RELEASE40" \
      -t "$CW_TAG" .) \
    </dev/null 2>&1 | sed -E 's/[0-9a-f]{20,}/<redacted>/g' >>"$BUILD_LOG" || status=$?
  if [[ "$status" != 0 ]]; then
    say "The console-web build failed or timed out (exit $status)."
    return 1
  fi
  return 0
}

# --- phase B ------------------------------------------------------------------------

phase_b() {
  local err="$TMP/dump.err" status=0 s cid id scratch_v scratch_t live_t

  # B1: stop the writers; the downtime starts here
  STATE=changing
  DOWNTIME_START="$(now)"
  say "B1: stopping the writers (compose stop api worker scheduler); downtime starts"
  run dc "$T_UP" "$S_R" stop api worker scheduler || rollback "compose stop failed"

  # B2: the backup folder and its files
  BACKUP="$H/backups/release-$T_S12-$TS"
  say "B2: backup to $BACKUP"
  (umask 077 && mkdir -p "$BACKUP") || rollback "cannot create $BACKUP"
  status=0
  ( dc "$T_DUMP" "$S_R" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
    >"$BACKUP/db.dump" ) 2>"$err" </dev/null || status=$?
  if [[ "$status" != 0 ]]; then
    show "$(cat "$err" || true)"
    rollback "the database dump failed (exit $status)"
  fi
  chmod 600 "$BACKUP/db.dump"
  cp -p "$SETTINGS" "$BACKUP/settings.env" && chmod 600 "$BACKUP/settings.env"
  printf '%s\n' "$S_R" >"$BACKUP/current.txt"
  : >"$BACKUP/images.txt"
  for s in api worker scheduler console-web; do
    cid=""
    cap dc "$T_CALL" "$S_R" ps --all -q "$s" || true
    cid="$(head -n 1 <<<"$RUN_OUT")"
    id=""
    if [[ -n "$cid" ]]; then
      cap timeout --kill-after="$T_KILL" "$T_CALL" docker inspect --format '{{.Image}}' "$cid" || true
      id="$(head -n 1 <<<"$RUN_OUT")"
    fi
    printf '%s %s\n' "$s" "${id:-unknown}" >>"$BACKUP/images.txt"
  done
  printf '%s\n' "$DB_START_VERSION" >"$BACKUP/alembic.txt"
  (cd "$BACKUP" && sha256sum db.dump settings.env current.txt images.txt alembic.txt >SHA256SUMS)
  chmod 600 "$BACKUP"/*

  # B3: prove the backup restores
  say "B3: checking the backup"
  [[ -s "$BACKUP/db.dump" ]] || rollback "db.dump is empty"
  run_in "$BACKUP/db.dump" dc "$T_CALL" "$S_R" exec -T postgres sh -c 'pg_restore --list' ||
    rollback "pg_restore --list of the dump failed"
  grep -q 'TABLE DATA public alembic_version' <<<"$RUN_OUT" ||
    rollback "the dump listing has no TABLE DATA public alembic_version"
  run dc "$T_CALL" "$S_R" exec -T postgres sh -c \
    'dropdb --force -U "$POSTGRES_USER" homayar_restorecheck 2>/dev/null || true; createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" homayar_restorecheck' ||
    rollback "the scratch database homayar_restorecheck could not be created"
  run_in "$BACKUP/db.dump" dc "$T_RESTORE" "$S_R" exec -T postgres sh -c \
    'pg_restore --exit-on-error --no-owner -U "$POSTGRES_USER" -d homayar_restorecheck' ||
    rollback "the dump does not restore into homayar_restorecheck"
  scratch_v="$(db_scratch_version)" || rollback "could not read the scratch database version"
  scratch_t="$(db_scratch_tables)" || rollback "could not count the scratch tables"
  live_t="$(db_live_tables)" || rollback "could not count the live tables"
  [[ "$scratch_v" == "$DB_START_VERSION" ]] ||
    rollback "the scratch database is at '$scratch_v', the live one at '$DB_START_VERSION'"
  [[ "$scratch_t" == "$live_t" ]] ||
    rollback "the scratch database has $scratch_t public tables, the live one $live_t"
  run dc "$T_CALL" "$S_R" exec -T postgres sh -c 'dropdb --force -U "$POSTGRES_USER" homayar_restorecheck' ||
    rollback "the scratch database could not be dropped"
  [[ "$(sha256sum "$BACKUP/settings.env" | awk '{ print $1 }')" == "$(sha256sum "$SETTINGS" | awk '{ print $1 }')" ]] ||
    rollback "the settings copy does not match the live file"

  # B4: the settings plan
  say "B4: writing the settings plan"
  write_settings || rollback "writing or reading back the settings plan failed"
  say "Settings written and read back:"
  show_plan

  # B5: switch current atomically
  say "B5: switching current to releases/$T_S12"
  ln -sfn "releases/$T_S12" "$H/.current.$TS.new" || rollback "ln -sfn failed"
  mv -T "$H/.current.$TS.new" "$H/current" || rollback "mv -T of the new current failed"

  # B6: migrate
  say "B6: running the migrations (target head $T_HEAD)"
  MIGRATE_STARTED=1
  run dc "$T_MIGRATE" "$T_R" --profile migrate run --rm -T migrate ||
    rollback "the migration run failed"
  DB_AFTER="$(db_version "$T_R")" || rollback "could not read the database version after the migration"
  [[ "$DB_AFTER" == "$T_HEAD" ]] || rollback "the database is at '$DB_AFTER' after the migration, expected $T_HEAD"

  # B7: the HOM-290 trend backfill (idempotent)
  say "B7: running the trend judgment backfill (HOM-290, idempotent)"
  run dc "$T_BACKFILL" "$T_R" run --rm -T --no-deps api \
    python -m backend.services.trend_judgment_store backfill ||
    rollback "the trend backfill failed"
  BACKFILL_LINE="$(grep -E '^\{.*\}$' <<<"$RUN_OUT" | tail -n 1)"
  [[ -n "$BACKFILL_LINE" ]] || rollback "the trend backfill printed no JSON counts line"
  say "backfill counts: $BACKFILL_LINE"

  # B8: up and wait
  say "B8: up -d --no-build --pull never (target)"
  run shielded dc "$T_UP" "$T_R" up -d --no-build --pull never ||
    rollback "compose up of the target failed"
  wait_healthy "$T_R" DOWNTIME || rollback "the target stack did not become healthy"
  say "The stack is healthy; downtime was $DOWNTIME (stop until /readyz answered 200)"
}

db_scratch_version() {
  cap dc "$T_CALL" "$S_R" exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d homayar_restorecheck -Atc "select version_num from alembic_version"' || return 1
  tail -n 1 <<<"$RUN_OUT"
}

db_scratch_tables() {
  cap dc "$T_CALL" "$S_R" exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d homayar_restorecheck -Atc \"select count(*) from pg_tables where schemaname = 'public'\"" || return 1
  tail -n 1 <<<"$RUN_OUT"
}

db_live_tables() {
  cap dc "$T_CALL" "$S_R" exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc \"select count(*) from pg_tables where schemaname = 'public'\"" || return 1
  tail -n 1 <<<"$RUN_OUT"
}

# --- rollback -----------------------------------------------------------------------

manual_steps() {
  say "Run these by hand, as gpu, in this order, and check each result:"
  say "  0. $H/hc stop api worker scheduler"
  if [[ -n "$MIGRATE_STARTED" && -n "$BACKUP" && -s "$BACKUP/db.dump" ]]; then
    say "  1. cat $BACKUP/db.dump | $H/hc exec -T postgres sh -c 'dropdb --force -U \"\$POSTGRES_USER\" \"\$POSTGRES_DB\"; createdb -U \"\$POSTGRES_USER\" -O \"\$POSTGRES_USER\" \"\$POSTGRES_DB\"; pg_restore --exit-on-error -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"'"
  fi
  if [[ -n "$BACKUP" && -f "$BACKUP/settings.env" ]]; then
    say "  2. cp -p $BACKUP/settings.env $SETTINGS && chmod 600 $SETTINGS"
  fi
  say "  3. ln -sfn $(release_link_target "$S_R") $H/.current.manual && mv -T $H/.current.manual $H/current"
  say "  4. $H/hc up -d --no-build --pull never"
  say "  5. the health checks of docs/operations/app-server.md (local /healthz and /readyz)"
}

rollback() {  # R1 to R5 with the gating of Addendum A3: R2 (the database
  # restore) only after R1 (the stop) succeeded; R3 and R4 always (they are
  # file operations); R5 (start the start release) only when R1 succeeded and
  # R2 succeeded or was not needed. Exit 1 only when every needed step passed;
  # otherwise exit 3 with the manual steps.
  local rb_r rb_fail=0 r1_ok=0 r2_needed=0 r2_ok=0 v
  trap '' INT TERM
  say "ROLLBACK because: $1"
  rb_r="$(readlink -f "$H/current" 2>/dev/null || true)"
  [[ -n "$rb_r" ]] || rb_r="$S_R"

  say "R1: stopping the writers on $rb_r"
  if run dc "$T_UP" "$rb_r" stop api worker scheduler; then
    r1_ok=1
    record rollback R1 PASS "compose stop api worker scheduler ran"
  else
    record rollback R1 FAIL "compose stop api worker scheduler failed"
    rb_fail=1
  fi

  if [[ -n "$MIGRATE_STARTED" ]]; then
    r2_needed=1
    if [[ "$r1_ok" != 1 ]]; then
      record rollback R2 SKIPPED "R1 failed"
      rb_fail=1
    elif [[ ! -s "$BACKUP/db.dump" ]]; then
      record rollback R2 FAIL "no usable dump in $BACKUP"
      rb_fail=1
    elif ! run dc "$T_RESTORE" "$rb_r" exec -T postgres sh -c \
      'dropdb --force -U "$POSTGRES_USER" "$POSTGRES_DB"; createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" "$POSTGRES_DB"'; then
      record rollback R2 FAIL "could not reset the database"
      rb_fail=1
    elif ! run_in "$BACKUP/db.dump" dc "$T_RESTORE" "$rb_r" exec -T postgres sh -c \
      'pg_restore --exit-on-error -U "$POSTGRES_USER" -d "$POSTGRES_DB"'; then
      record rollback R2 FAIL "pg_restore failed"
      rb_fail=1
    else
      v="$(db_version "$rb_r")" || v=""
      if [[ "$v" == "$DB_START_VERSION" ]]; then
        r2_ok=1
        record rollback R2 PASS "the database is back at $DB_START_VERSION"
      else
        record rollback R2 FAIL "the database is at '$v', expected $DB_START_VERSION"
        rb_fail=1
      fi
    fi
  else
    record rollback R2 PASS "not needed: the migration never started"
  fi

  if [[ -n "$BACKUP" && -f "$BACKUP/settings.env" ]]; then
    say "R3: restoring $SETTINGS from the backup"
    if cp -p "$BACKUP/settings.env" "$H/config/.settings.restore.$$" &&
      chmod 600 "$H/config/.settings.restore.$$" &&
      mv -f "$H/config/.settings.restore.$$" "$SETTINGS" &&
      [[ "$(sha256sum "$SETTINGS" | awk '{ print $1 }')" == "$(sha256sum "$BACKUP/settings.env" | awk '{ print $1 }')" ]]; then
      record rollback R3 PASS "$SETTINGS restored"
    else
      rm -f "$H/config/.settings.restore.$$"
      record rollback R3 FAIL "could not restore $SETTINGS"
      rb_fail=1
    fi
  else
    record rollback R3 PASS "not needed: no settings backup (nothing was written)"
  fi

  say "R4: current must point back to $S_R"
  if [[ "$(readlink -f "$H/current" 2>/dev/null || true)" == "$S_R" ]]; then
    record rollback R4 PASS "current already points to $S_R"
  elif ln -sfn "$(release_link_target "$S_R")" "$H/.current.$TS.back" && mv -T "$H/.current.$TS.back" "$H/current"; then
    record rollback R4 PASS "current switched back to $S_R"
  else
    rm -f "$H/.current.$TS.back"
    record rollback R4 FAIL "could not switch current back"
    rb_fail=1
  fi

  if [[ "$r1_ok" == 1 && ( "$r2_needed" != 1 || "$r2_ok" == 1 ) ]]; then
    say "R5: up -d --no-build --pull never (start release), then C1 to C3 on it"
    if ! run shielded dc "$T_UP" "$S_R" up -d --no-build --pull never; then
      record rollback R5 FAIL "compose up of the start release failed"
      rb_fail=1
    elif ! wait_healthy "$S_R" ROLLBACK_DOWNTIME; then
      record rollback R5 FAIL "the start release did not become healthy within $WAIT_SECONDS s"
      rb_fail=1
    elif ! c1_images rollback "$S_R" "$API_S_ID" "$CW_S_ID"; then
      record rollback R5 FAIL "the start images do not run"
      rb_fail=1
    elif [[ "$(http_code 10 "$TMP/body" -H "$HOST_HEADER" "$PROXY/healthz")" != 200 ]]; then
      record rollback R5 FAIL "local /healthz is not 200"
      rb_fail=1
    elif [[ "$(http_code 10 "$TMP/body" -H "$HOST_HEADER" "$PROXY/readyz")" != 200 ]]; then
      record rollback R5 FAIL "local /readyz is not 200"
      rb_fail=1
    else
      record rollback R5 PASS "start release up, healthy, C1 to C3 pass"
    fi
  elif [[ "$r1_ok" != 1 ]]; then
    record rollback R5 SKIPPED "R1 failed; run the manual steps (they start with the stop)"
    rb_fail=1
  else
    record rollback R5 SKIPPED "the database is not restored; the writers stay stopped"
    rb_fail=1
  fi

  if [[ "$rb_fail" == 0 ]]; then
    say "ROLLED BACK: $1"
    finish 1
  fi
  say "ROLLBACK FAILED: manual action is needed (the steps above)."
  manual_steps
  finish 3
}

# --- finish -------------------------------------------------------------------------

finish() {
  local code="$1" line
  trap '' INT TERM
  if [[ "$STATE" == changing || -n "$ALREADY" ]]; then
    DB_AFTER="$(db_version_quiet "$T_R")"
  fi
  STATE=finished
  say ""
  say "==== HOM-358 result ($MODE) ===="
  say "start release:     $S_R"
  say "target release:    $T_R"
  say "api image ID:      before ${API_S_ID:-?}, after ${API_T_ID:-?}"
  say "console image ID:  before ${CW_S_ID:-?}, after ${CW_T_ID:-?}"
  say "alembic version:   before ${DB_START_VERSION:-?}, after ${DB_AFTER:-?}"
  say "backfill counts:   $BACKFILL_LINE"
  say "settings plan:"
  show_plan
  say "downtime:          $DOWNTIME (from the stop until /readyz answered 200)"
  say "backup folder:     ${BACKUP:-none}"
  say "build log:         ${BUILD_LOG:-none}"
  if [[ "$code" == 4 ]]; then
    say "ATTENTION: applied, the local checks pass, but the public check C9 is worse"
    say "than the baseline; decide by hand whether to keep or undo it."
  fi
  if [[ "$code" == 3 ]]; then
    say "ATTENTION: the rollback failed; manual action is needed (steps above)."
  fi
  say "HOM-340 note: the host nginx recording location (app-server.md) is a"
  say "separate owner sudo step (HOM-366): add it, run nginx -t, then a graceful"
  say "reload. Leaving it out is safe; it only spends host disk on slow downloads."
  say "checks:"
  for line in "${RESULTS[@]}"; do say "  $line"; done
  if [[ -n "$LOG" ]]; then say "log:               $LOG"; fi
  say "exit code:         $code"
  if [[ "$MODE" == apply ]]; then
    printf 'EXIT %s %s %s\n' "$code" "$(date -u +%Y%m%dT%H%M%SZ)" "$MODE" \
      >"$OPS/release-$T_S12-status" 2>/dev/null || true
  fi
  cleanup
  exit "$code"
}

db_version_quiet() {
  cap dc "$T_CALL" "$1" exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select version_num from alembic_version"' || return 0
  tail -n 1 <<<"$RUN_OUT"
}

cleanup() {  # remove the temp folder and the temp settings file; safe to run twice
  if [[ -n "$TMP" ]]; then rm -rf -- "$TMP"; fi
  TMP=""
  if [[ -n "$SETTINGS_TMP" ]]; then rm -f -- "$SETTINGS_TMP"; fi
  SETTINGS_TMP=""
  rm -f -- "$H/.current.$TS.new" "$H/.current.$TS.back" "$H/config/.settings.restore.$$" 2>/dev/null || true
}

on_signal() {  # SIGINT or SIGTERM: exit 2 before the change, roll back during it
  trap '' INT TERM
  case "$STATE" in
    preflight)
      say "STOPPED by a signal before any change."
      refuse "stopped by a signal"
      ;;
    changing)
      rollback "stopped by a signal (SIGINT or SIGTERM)"
      ;;
    *)
      say "Signal ignored in state $STATE."
      ;;
  esac
}

on_exit() {  # cleanup, and the true exit code after an unexpected stop
  local status=$?
  set +e
  trap '' INT TERM
  if [[ "$STATE" == changing ]]; then
    rollback "stopped unexpectedly during the change (exit $status)"
  fi
  cleanup
  if [[ "$STATE" == preflight && "$status" != 0 && "$status" != 2 ]]; then
    say "STOPPED UNEXPECTEDLY before any change (exit $status). Nothing was changed."
    exit 2
  fi
}

# --- main ---------------------------------------------------------------------------

main() {
  local mode_arg
  mode_arg=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --check|--apply)
        [[ -z "$mode_arg" ]] || { usage; exit 2; }
        mode_arg="$1"
        MODE="${1#--}"
        shift
        ;;
      --release)
        [[ $# -ge 2 ]] || { usage; exit 2; }
        RELEASE40="$2"
        shift 2
        ;;
      --use-side-image)
        SIDE=1
        shift
        ;;
      *)
        usage
        exit 2
        ;;
    esac
  done
  if [[ -z "$MODE" || ! "$RELEASE40" =~ ^[0-9a-f]{40}$ ]]; then
    usage
    exit 2
  fi
  if (( BASH_VERSINFO[0] < 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 4) )); then
    echo "needs bash >= 4.4" >&2
    exit 2
  fi
  TMP="$(mktemp -d)"

  preflight

  if [[ -n "$ALREADY" ]]; then
    say "Already done: current is the target release, the database is at its head"
    say "and the settings plan has no change. Running the after-checks only."
    if run_after_checks; then
      if [[ -n "$C9_ATTENTION" ]]; then finish 4; fi
      finish 0
    else
      if [[ "$MODE" == check ]]; then
        refuse "already done, but the after-checks fail; --check changes nothing"
      else
        rollback "the after-checks failed in the already-done state"
      fi
    fi
  fi

  if [[ "$MODE" == check ]]; then
    say "Preflight passes. --apply would:"
    if [[ "$P12_MODE" == present ]]; then
      say "  1. use the present images $API_TAG and $CW_TAG (a rerun; no build)"
    elif [[ -n "$SIDE" ]]; then
      say "  1. use the loaded side images $API_TAG and $CW_TAG (no build)"
    else
      say "  1. build $API_TAG and $CW_TAG in $T_R (at most $T_BUILD s each,"
      say "     capped: 3 GiB, 4 CPUs; output in the ops build log)"
    fi
    say "  2. stop api worker scheduler (the downtime starts)"
    say "  3. back up: pg_dump to $H/backups/release-$T_S12-<ts>/db.dump, settings.env,"
    say "     current.txt, images.txt, alembic.txt, SHA256SUMS; prove the dump restores"
    say "  4. write the settings plan to $SETTINGS (atomic) and read it back"
    say "  5. switch current to releases/$T_S12 (atomic)"
    say "  6. run the migrations (target head $T_HEAD)"
    say "  7. run the trend judgment backfill (HOM-290; idempotent)"
    say "  8. up -d --no-build --pull never and wait for health (downtime ends at /readyz 200)"
    say "  9. run the after-checks C1 to C10; any failure of C1 to C8 rolls back"
    say "Settings plan:"
    show_plan
    say "Expected downtime: about 3 to 5 minutes (an estimate until measured;"
    say "from the stop until /readyz answers 200; the database is 15 MB)."
    finish 0
  fi

  # phase A: the images (no downtime; a failure changes nothing live)
  if [[ "$P12_MODE" != present && -z "$SIDE" ]]; then
    if ! build_images; then
      say "Only the failed build's dangling layers may remain."
      side_image_help
      say "REFUSED: the image build failed"
      say "Nothing live changed."
      finish 2
    fi
  fi
  if verify_images "$([[ "$S_R" == "$T_R" ]] && echo "" || echo differ)"; then
    say "Phase A done: the target images are verified."
  else
    say "Nothing live changed."
    side_image_help
    say "REFUSED: the target images failed V1/V2"
    say "Nothing live changed."
    finish 2
  fi

  # phases B and C. STATE stays "changing" through the after-checks: from B1
  # until finish, a SIGINT or SIGTERM (or an unexpected stop) starts the
  # rollback (SPEC 4.6, 4.7, review F6).
  phase_b
  if run_after_checks; then
    if [[ -n "$C9_ATTENTION" ]]; then finish 4; fi
    finish 0
  fi
  rollback "the after-checks C1 to C8 failed"
}

trap on_exit EXIT
trap on_signal INT TERM
main "$@"
