#!/usr/bin/env bash
# HOM-383: build, seed and remove the demo stack of demo.homayar.com.
#
# Run on homayar-local as user gpu (never with sudo), stdin from /dev/null:
#   bash /home/gpu/homayar-demo/src/infra/scripts/demo-site.sh --check  </dev/null
#   bash /home/gpu/homayar-demo/src/infra/scripts/demo-site.sh --apply  </dev/null
#   bash /home/gpu/homayar-demo/src/infra/scripts/demo-site.sh --undo     </dev/null  (plan only)
#   bash /home/gpu/homayar-demo/src/infra/scripts/demo-site.sh --undo --yes </dev/null
# Runbook: docs/operations/demo-site.md.
#
# The demo is a second Homayar Compose project, homayar-demo, that shares
# nothing with production (homayar-onprem, /home/gpu/homayar): its own demo
# home /home/gpu/homayar-demo (this script refuses to run from anywhere
# else), its own subnets 172.29.30.0/24 to 172.29.34.0/24, its own volumes
# and secrets, one published port 127.0.0.1:8090, and its own image tags
# homayar-demo/api:<tag> and homayar-demo/console-web:<tag>. It is filled
# with the demo seed (HOM-365: 270 scripted customer calls plus 30 console
# meetings), so sample data never mixes with production data.
#
# --apply order: preflight (= every --check item) -> demo home and config
# (only when missing) -> the demo image tags -> postgres redis minio --wait
# -> minio-bootstrap -> migrate -> the homayar_api role (once) -> the
# tombstone volume (once) -> the demo seed (once) -> the demo login password
# (once) -> proxy nginx -t -> up -d --wait -> health checks -> summary.
# Every finished step leaves a marker in config/.steps/, so a second --apply
# on a healthy demo changes no data and reprints the summary.
#
# The seed refuses every environment except development and test, so it runs
# once as a one-shot api container with ENVIRONMENT=development set only for
# that container, against the same database, MinIO and secrets as the
# production-mode stack (so every row it writes stays valid). The seed's
# stdout prints the public seed password: it is captured to a 600 temp file,
# never shown and never logged; only the workspace name and meeting count
# lines are kept. Before the proxy first starts, --apply also sets one new
# random password on the four seed accounts and writes it to
# secrets/demo-login (the script prints the path, never the password).
#
# Exit codes: --check: 0 ready (or already applied and healthy), 1 not ready,
# 2 usage error. --apply: 0 done (or already healthy), 1 failed during the
# apply, 2 refused (nothing changed). --undo: 0 clean, 1 failed, 2 without
# --yes (nothing changed) or usage error.
#
# Test seams (test-only, never set on the server): DEMO_TEST_HOME replaces
# the demo home path, DEMO_API_IMAGE and DEMO_CONSOLE_WEB_IMAGE replace the
# two image defaults. The selftest infra/ci/tests/test-demo-site.sh stubs id,
# docker, ss, ip, free, df and curl on PATH.
set -euo pipefail
export LC_ALL=C
trap '' HUP PIPE

H="${DEMO_TEST_HOME:-/home/gpu/homayar-demo}"
PROJECT=homayar-demo

DEMO_API_IMAGE="${DEMO_API_IMAGE:-homayar/api:d8e39a994f8b}"
DEMO_CONSOLE_WEB_IMAGE="${DEMO_CONSOLE_WEB_IMAGE:-homayar/console-web:d8e39a994f8b}"

POSTGRES_DIGEST='postgres:17-alpine@sha256:266d3c257594e195b309ae9d1805e54c788f1d7b375f56bf6c367aac6c917e7b'
REDIS_DIGEST='redis:7.4-alpine@sha256:8da19c7399dca900c6822a1cf30f9d2285585e2c4b457ff4095b0f4500054d3f'
NGINX_DIGEST='nginx:1.30-alpine-slim@sha256:dede8895648b461178c3bc604f7eed4423efe5220b2c4d839f5eb3d34ac3f8f7'
MINIO_DIGEST='bitnamilegacy/minio:2025.4.22@sha256:50cec18ac4184af4671a78aedd5554942c8ae105d51a465fa82037949046da01'
MINIO_MC_DIGEST='bitnamilegacy/minio-client:2025.4.16@sha256:8a86e441decf053093c5977d49290298295f850c7843652f93e970ab15871dd1'

DEMO_PORT=8090
DEMO_NETS=(172.29.30.0/24 172.29.31.0/24 172.29.32.0/24 172.29.33.0/24 172.29.34.0/24)
MIN_MEM_BYTES=$((4 * 1024 * 1024 * 1024))
MIN_DISK_BYTES=$((5 * 1024 * 1024 * 1024))
SEED_MEM_LIMIT="${DEMO_SEED_MEM_LIMIT:-1536m}"

T_CALL=60 T_STORES=600 T_BOOT=120 T_MIGRATE=900 T_ROLE=60 T_TOMB=120 T_SEED=5400 T_LOGIN=300 T_UP=900 T_DOWN=300

MODE="" YES="" REPO=""
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="" CHECK_READY=0 ALREADY_APPLIED="" SUBNET_BAD="" MEM_FREE="" DISK_FREE=""
RUN_OUT=""
RESULTS=()
SECRETS_NEW=()

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

record() {  # record <check> <result> <detail>
  RESULTS+=("$1 $2: $3")
  say "  $1 $2: $3"
}

refuse() {  # exit 2: nothing was changed
  say "REFUSED: $*"
  say "Nothing was changed."
  finish 2
}

hex_rand() {  # hex_rand <bytes>: n bytes from /dev/urandom as hex (no openssl needed)
  od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n'
}

# --- identity, home, images ------------------------------------------------------

check_identity() {  # every mode runs as gpu, never root; 0 ok
  local uid user
  uid="$(id -u)"
  user="$(id -un)"
  if [[ "$uid" == 0 ]]; then
    say_err "REFUSED: running as root; run as gpu, without sudo"
    return 1
  fi
  if [[ "$user" != gpu ]]; then
    say_err "REFUSED: running as '$user'; run as gpu"
    return 1
  fi
  return 0
}

repo_root() {  # the repository root this script lives in
  local dir
  dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  printf '%s' "$(cd -- "$dir/../.." && pwd -P)"
}

check_repo_under_home() {  # the script must run from the demo home, never from
  # /home/gpu/homayar or anywhere else
  local root
  root="$(repo_root)"
  if [[ "$root" != "$H" && "$root" != "$H"/* ]]; then
    say_err "REFUSED: this script lives in '$root', outside the demo home $H."
    say_err "Copy the source tree to $H/src and run it from there (runbook, gpu step 1)."
    return 1
  fi
  REPO="$root"
  return 0
}

image_tag_part() {  # image_tag_part <ref>: the tag of repo:tag; fails on a digest ref
  local ref="$1"
  if [[ "$ref" != *:* || "$ref" == *@* ]]; then
    say_err "REFUSED: '$ref' is not a repo:tag image reference (digests are not supported here)"
    return 1
  fi
  printf '%s' "${ref##*:}"
}

image_id() {  # the image ID of a reference, or empty
  cap docker image inspect --format '{{.Id}}' "$1" || return 0
  head -n 1 <<<"$RUN_OUT"
}

# --- docker and compose ----------------------------------------------------------

image_present() { [[ -n "$(image_id "$1")" ]]; }

compose_version_ok() {  # docker and the compose plugin answer
  command -v docker >/dev/null 2>&1 || return 1
  cap docker compose version || return 1
  return 0
}

dc() {  # dc <seconds> <compose args...>: exactly the command line $H/dc runs
  # (the one Compose command line of the demo home), with a timeout
  local t="$1"
  shift
  timeout --kill-after=15 "$t" bash "$H/dc" "$@"
}

port_held_by_demo() {  # 0 when some container of this project publishes 127.0.0.1:$DEMO_PORT
  cap docker ps --filter "label=com.docker.compose.project=$PROJECT" \
    --format '{{.Names}} {{.Ports}}' || return 1
  grep -qF "127.0.0.1:$DEMO_PORT" <<<"$RUN_OUT"
}

port_free_or_ours() {  # 0 when $DEMO_PORT is free, or held by this demo's proxy
  local listeners=""
  listeners="$(ss -H -ltn 2>/dev/null | awk -v p=":$DEMO_PORT\$" '$4 ~ p { print $4 }' || true)"
  [[ -z "$listeners" ]] && return 0
  port_held_by_demo
}

ip_to_int() {  # ip_to_int <dotted-quad>: the 32-bit value on stdout
  local a b c d
  IFS=. read -r a b c d <<<"$1"
  printf '%s' "$(( (a << 24) + (b << 16) + (c << 8) + d ))"
}

cidrs_overlap() {  # cidrs_overlap <a> <b>: 0 when the two networks share an address
  local anet="" aprefix="" bnet="" bprefix="" mask
  IFS=/ read -r anet aprefix <<<"$1"
  IFS=/ read -r bnet bprefix <<<"$2"
  [[ -n "$aprefix" ]] || aprefix=32
  [[ -n "$bprefix" ]] || bprefix=32
  if (( aprefix < bprefix )); then mask="$aprefix"; else mask="$bprefix"; fi
  (( $(ip_to_int "$anet") >> (32 - mask) == $(ip_to_int "$bnet") >> (32 - mask) ))
}

demo_bridge_subnets() {  # the subnets of this project's docker networks, one per line
  local ids id found=""
  ids="$(docker network ls --filter "label=com.docker.compose.project=$PROJECT" -q 2>/dev/null || true)"
  [[ -n "$ids" ]] || return 0
  while IFS= read -r id; do
    [[ -n "$id" ]] || continue
    found="$found$(docker network inspect "$id" --format '{{range .IPAM.Config}}{{.Subnet}}|{{end}}' 2>/dev/null || true)"
  done <<<"$ids"
  printf '%s' "$found" | tr '|' '\n'
}

subnets_free() {  # 0 when no route (except this demo's own bridges) overlaps the five subnets
  local routes line dest net bad="" own=""
  routes="$(ip -4 route show 2>/dev/null || true)"
  own="$(demo_bridge_subnets | grep . | sort -u || true)"
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    dest="${line%%[[:space:]]*}"
    case "$dest" in
      default|local|broadcast|multicast|unreachable|prohibit|blackhole|throw|anycast) continue ;;
      *"("*|*:*) continue ;;
    esac
    for net in "${DEMO_NETS[@]}"; do
      if cidrs_overlap "$dest" "$net"; then
        if grep -qxF "$dest" <<<"$own"; then
          continue 2
        fi
        bad="${bad:+$bad }$dest"
      fi
    done
  done <<<"$routes"
  if [[ -n "$bad" ]]; then
    SUBNET_BAD="$bad"
    return 1
  fi
  return 0
}

mem_ok() {  # at least MIN_MEM_BYTES available (free -b, available column)
  local out=""
  out="$(free -b 2>/dev/null | awk '/^Mem:/ { print $7 }')" || true
  if [[ "$out" =~ ^[0-9]+$ && "$out" -ge "$MIN_MEM_BYTES" ]]; then
    MEM_FREE="$out"
    return 0
  fi
  MEM_FREE="${out:-unknown}"
  return 1
}

disk_ok() {  # at least MIN_DISK_BYTES free on the demo home's file system (df -PB1)
  local out=""
  out="$(df -PB1 "$H" 2>/dev/null | awk 'NR == 2 { print $4 }')" || true
  if [[ "$out" =~ ^[0-9]+$ && "$out" -ge "$MIN_DISK_BYTES" ]]; then
    DISK_FREE="$out"
    return 0
  fi
  DISK_FREE="${out:-unknown}"
  return 1
}

demo_config_complete() {  # the dc wrapper and both env files exist
  [[ -f "$H/dc" && -f "$H/config/settings.env" && -f "$H/secrets/secrets.env" ]]
}

stack_rows() {  # the ps rows of every demo service, or empty
  RUN_OUT=""
  demo_config_complete || return 0
  cap dc "$T_CALL" ps --all --format '{{.Service}}|{{.State}}|{{.Health}}|{{.ExitCode}}' || return 0
  printf '%s\n' "$RUN_OUT"
}

stack_healthy() {  # every demo service in its wanted state, like the runbook.
  # Compose prints exit code 0 for a running container (5.x; older ones may
  # print nothing), so the exit code counts only for the exited one-shot.
  local rows="" missing=0 s
  rows="$(stack_rows)"
  [[ -n "$rows" ]] || return 1
  for s in proxy console-web api worker postgres redis minio; do
    grep -qxE "$s[|]running[|]healthy[|]0?" <<<"$rows" || missing=1
  done
  grep -qxE 'scheduler[|]running[|][|]0?' <<<"$rows" || missing=1
  grep -qxF 'minio-bootstrap|exited||0' <<<"$rows" || missing=1
  return "$missing"
}

healthz_ok() {  # the demo answers on 127.0.0.1:$DEMO_PORT
  local _
  for _ in 1 2 3 4 5; do
    if curl -fsS --max-time 5 "http://127.0.0.1:$DEMO_PORT/healthz" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  return 1
}

app_ok() {  # the console answers
  local code=000
  for _ in 1 2 3 4 5; do
    code="$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$DEMO_PORT/app/" 2>/dev/null || true)"
    [[ "$code" == 200 ]] && return 0
    sleep 2
  done
  return 1
}

# --- --check: the read-only items -------------------------------------------------

run_checks() {  # every --check item; sets CHECK_READY and ALREADY_APPLIED;
  # never writes a file, never runs a mutating Docker command
  local ready=1 api_tag cw_tag third net missing3="" already=""
  local imgs_missing=()
  CHECK_READY=0
  ALREADY_APPLIED=""

  if check_identity; then
    record C1 PASS "uid $(id -u) ($(id -un))"
  else
    record C1 FAIL "not running as gpu (or root)"
    ready=0
  fi

  if check_repo_under_home; then
    record C2 PASS "repository root $REPO is under the demo home $H"
  else
    record C2 FAIL "the script runs outside the demo home $H"
    ready=0
  fi

  if compose_version_ok; then
    record C3 PASS "docker and the compose plugin answer"
  else
    record C3 FAIL "docker or the compose plugin is missing"
    ready=0
  fi

  api_tag="$(demo_tag_of "$DEMO_API_IMAGE")"
  cw_tag="$(demo_tag_of "$DEMO_CONSOLE_WEB_IMAGE")"
  if image_present "$api_tag" && image_present "$cw_tag"; then
    record C4 PASS "the demo images are present ($api_tag, $cw_tag)"
  elif image_present "$DEMO_API_IMAGE" && image_present "$DEMO_CONSOLE_WEB_IMAGE"; then
    record C4 PASS "the source images are present ($DEMO_API_IMAGE, $DEMO_CONSOLE_WEB_IMAGE)"
  else
    [[ -n "$(image_id "$api_tag")" ]] || imgs_missing+=("$api_tag")
    [[ -n "$(image_id "$DEMO_API_IMAGE")" ]] || imgs_missing+=("$DEMO_API_IMAGE")
    [[ -n "$(image_id "$cw_tag")" ]] || imgs_missing+=("$cw_tag")
    [[ -n "$(image_id "$DEMO_CONSOLE_WEB_IMAGE")" ]] || imgs_missing+=("$DEMO_CONSOLE_WEB_IMAGE")
    record C4 FAIL "missing image(s): ${imgs_missing[*]}"
    ready=0
  fi
  for third in "$POSTGRES_DIGEST" "$REDIS_DIGEST" "$NGINX_DIGEST" "$MINIO_DIGEST" "$MINIO_MC_DIGEST"; do
    image_present "$third" || missing3="$missing3 $third"
  done
  if [[ -z "$missing3" ]]; then
    record C5 PASS "the pinned third-party images are present"
  else
    record C5 FAIL "missing pinned image(s):$missing3"
    ready=0
  fi

  if port_free_or_ours; then
    record C6 PASS "port $DEMO_PORT is free or held by this demo's proxy"
  else
    record C6 FAIL "port $DEMO_PORT is busy and not held by this demo's proxy"
    ready=0
  fi

  if subnets_free; then
    record C7 PASS "no foreign route overlaps ${DEMO_NETS[*]}"
  else
    record C7 FAIL "a route overlaps the demo subnets: $SUBNET_BAD"
    ready=0
  fi

  # Asked before the memory and disk gates: a running demo uses part of that
  # memory itself, so on an applied, healthy demo a low value is not a blocker.
  if stack_healthy && healthz_ok; then already=1; fi

  if mem_ok; then
    record C8 PASS "$MEM_FREE bytes of memory available (need $MIN_MEM_BYTES)"
  elif [[ -n "$already" ]]; then
    record C8 INFO "$MEM_FREE bytes of memory available; the running demo uses part of it (gate only before the first --apply)"
  else
    record C8 FAIL "only $MEM_FREE bytes of memory available (need $MIN_MEM_BYTES)"
    ready=0
  fi

  if disk_ok; then
    record C9 PASS "$DISK_FREE bytes free under $H (need $MIN_DISK_BYTES)"
  elif [[ -n "$already" ]]; then
    record C9 INFO "$DISK_FREE bytes free under $H; the running demo uses part of it (gate only before the first --apply)"
  else
    record C9 FAIL "only $DISK_FREE bytes free under $H (need $MIN_DISK_BYTES)"
    ready=0
  fi

  if [[ -n "$already" ]]; then
    record C10 PASS "the demo stack is up and healthy (already applied)"
  else
    record C10 INFO "the demo stack is not up (fine before the first --apply)"
  fi
  ALREADY_APPLIED="$already"
  if [[ "$ready" == 1 ]]; then CHECK_READY=1; else CHECK_READY=0; fi
}

print_plan() {
  say ""
  say "Plan (what --apply would do, in order):"
  say "  1. create the demo home under $H (only what is missing)"
  say "  2. tag $DEMO_API_IMAGE and $DEMO_CONSOLE_WEB_IMAGE as homayar-demo/*"
  say "  3. up postgres redis minio (--wait), then minio-bootstrap, then migrate"
  say "  4. create the homayar_api role and the tombstone volume (once each)"
  say "  5. run the demo seed once (270 calls, HOM-365)"
  say "  6. set the demo login password once (secrets/demo-login)"
  say "  7. nginx -t of the proxy, up -d --wait, health checks, summary"
  if [[ -n "$ALREADY_APPLIED" ]]; then
    say "The demo is already applied and healthy: --apply would only reprint the summary."
  fi
}

# --- the demo home and its files -------------------------------------------------

write_dc() {  # the one Compose command line of the demo home (like ~/homayar/hc)
  cat >"$H/dc" <<DCSCRIPT
#!/bin/bash
# The one Compose command line for the demo stack (docs/operations/demo-site.md).
set -euo pipefail
H='$H'
cd "\$H/src"
exec docker compose --project-name $PROJECT \\
  --env-file "\$H/config/settings.env" --env-file "\$H/secrets/secrets.env" \\
  --file infra/compose/compose.onprem.yaml --file infra/compose/compose.onprem.demo.yaml \\
  --profile onprem "\$@"
DCSCRIPT
  chmod 700 "$H/dc"
}

write_settings() {  # config/settings.env (600), only when missing; 0 written, 1 kept
  if [[ -f "$H/config/settings.env" ]]; then return 1; fi
  cat >"$H/config/settings.env" <<SETTINGS
# Demo stack settings (HOM-383, docs/operations/demo-site.md). No secret here.
APP_ENVIRONMENT=production
PUBLIC_ORIGIN=https://demo.homayar.com:8080
CORS_ORIGINS=https://demo.homayar.com:8080
# 127.0.0.1: the api container health check calls http://127.0.0.1:8000/healthz.
TRUSTED_HOSTS=demo.homayar.com,127.0.0.1
SECURE_COOKIES=true
# Informational: the overlay pins the published port to 127.0.0.1:8090 anyway.
BIND_ADDRESS=127.0.0.1
HTTP_PORT=$DEMO_PORT
POSTGRES_DB=homayar
POSTGRES_USER=homayar
# Empty: DATABASE_URL logs in as homayar_api, a member of homayar_app.
DATABASE_APP_ROLE=
API_IMAGE=$1
CONSOLE_WEB_IMAGE=$2
POSTGRES_IMAGE=$POSTGRES_DIGEST
REDIS_IMAGE=$REDIS_DIGEST
NGINX_IMAGE=$NGINX_DIGEST
MINIO_IMAGE=$MINIO_DIGEST
MINIO_MC_IMAGE=$MINIO_MC_DIGEST
LOG_LEVEL=INFO
ENABLE_FREE_DEMO=false
REGISTRATION_ENABLED=false
SETTINGS
  chmod 600 "$H/config/settings.env"
  return 0
}

secret_file() { printf '%s' "$H/secrets/$1"; }

write_secret() {  # write_secret <name> <bytes>: only when missing; 0 written, 1 kept
  if [[ -f "$(secret_file "$1")" ]]; then return 1; fi
  (umask 077 && printf '%s\n' "$(hex_rand "$2")" >"$(secret_file "$1")")
  SECRETS_NEW+=("$1")
  return 0
}

read_secret() {  # the file content without its trailing newline (never printed)
  local content
  content="$(<"$(secret_file "$1")")"
  printf '%s' "$content"
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

write_secrets_env() {  # secrets/secrets.env (600), from the secret files; the
  # printf builtin keeps the values out of every process list
  local f n
  for f in minio_root_user minio_root_password; do
    n="$(bad_char_count "$(secret_file "$f")" ':\r\n')"
    if [[ "$n" != 0 ]]; then
      say "REFUSED: secrets/$f has $n forbidden characters (':', a line break or a carriage return)"
      return 1
    fi
  done
  for f in storage_access_key storage_secret_key; do
    n="$(bad_char_count "$(secret_file "$f")" '\r\n')"
    if [[ "$n" != 0 ]]; then
      say "REFUSED: secrets/$f has $n forbidden characters (a line break or a carriage return)"
      return 1
    fi
  done
  (
    umask 077
    {
      printf 'POSTGRES_PASSWORD=%s\n' "$(read_secret postgres_password)"
      # +psycopg: the image has psycopg 3 only; a bare postgresql:// makes
      # SQLAlchemy load psycopg2, and migrate fails (rehearsal, HOM-383).
      printf 'MIGRATION_DATABASE_URL=postgresql+psycopg://homayar:%s@postgres:5432/homayar\n' "$(read_secret postgres_password)"
      printf 'DATABASE_URL=postgresql+psycopg://homayar_api:%s@postgres:5432/homayar\n' "$(read_secret homayar_api_db_password)"
      printf 'REDIS_PASSWORD=%s\n' "$(read_secret redis_password)"
      printf 'REDIS_URL=redis://:%s@redis:6379/0\n' "$(read_secret redis_password)"
      printf 'JWT_SECRET=%s\n' "$(read_secret jwt_secret)"
      printf 'TOKEN_PEPPER=%s\n' "$(read_secret token_pepper)"
      printf 'MINIO_ROOT_USER=%s\n' "$(read_secret minio_root_user)"
      printf 'MINIO_ROOT_PASSWORD=%s\n' "$(read_secret minio_root_password)"
      printf 'STORAGE_ACCESS_KEY=%s\n' "$(read_secret storage_access_key)"
      printf 'STORAGE_SECRET_KEY=%s\n' "$(read_secret storage_secret_key)"
    } >"$H/secrets/secrets.env"
  )
  chmod 600 "$H/secrets/secrets.env"
}

setup_home() {  # the demo home skeleton and config, only what is missing
  local api_tag="$1" cw_tag="$2"
  [[ -d "$H" ]] || { install -d -m 700 "$H"; say "Created $H (700)"; }
  [[ -d "$H/config" ]] || { install -d -m 700 "$H/config"; say "Created $H/config (700)"; }
  [[ -d "$H/config/.steps" ]] || { install -d -m 700 "$H/config/.steps"; }
  [[ -d "$H/secrets" ]] || { install -d -m 700 "$H/secrets"; say "Created $H/secrets (700)"; }
  [[ -d "$H/logs" ]] || { install -d -m 700 "$H/logs"; }
  if [[ ! -f "$H/dc" ]]; then
    write_dc
    say "Wrote $H/dc (the one Compose command line)"
  fi
  if write_settings "$api_tag" "$cw_tag"; then
    say "Wrote $H/config/settings.env (600)"
  else
    say "Kept the existing $H/config/settings.env"
  fi
  SECRETS_NEW=()
  write_secret postgres_password 32 || true
  write_secret homayar_api_db_password 32 || true
  write_secret redis_password 32 || true
  write_secret jwt_secret 32 || true
  write_secret token_pepper 32 || true
  write_secret minio_root_user 10 || true
  write_secret minio_root_password 20 || true
  write_secret storage_access_key 10 || true
  write_secret storage_secret_key 20 || true
  if [[ ${#SECRETS_NEW[@]} -gt 0 ]]; then
    say "Generated ${#SECRETS_NEW[@]} secret file(s) in $H/secrets (values never printed)"
  fi
  # secrets.env only holds values derived from the secret files, so it is
  # written on every run: a fixed line format reaches an existing demo home.
  write_secrets_env || return 1
  say "Wrote $H/secrets/secrets.env (600, from the secret files)"
  return 0
}

step_done() { [[ -f "$H/config/.steps/$1" ]]; }
step_mark() { printf '%s\n' "$TS" >"$H/config/.steps/$1"; }

# --- --apply steps ---------------------------------------------------------------

demo_tag_of() {  # demo_tag_of <repo:tag ref>: the matching homayar-demo/<name>:tag
  local repo="${1%:*}" tag="${1##*:}"
  printf 'homayar-demo/%s:%s' "${repo##*/}" "$tag"
}

apply_tags() {  # add the demo's own tags to the two image ids, so a later
  # production image cleanup by tag cannot remove the demo's images
  local src tag id
  for src in "$DEMO_API_IMAGE" "$DEMO_CONSOLE_WEB_IMAGE"; do
    if [[ "$src" == homayar-demo/* ]]; then continue; fi
    tag="$(demo_tag_of "$src")"
    id="$(image_id "$tag")"
    if [[ -n "$id" && "$id" == "$(image_id "$src")" ]]; then
      say "Demo tag $tag already points at the image (kept)"
      continue
    fi
    run docker tag "$src" "$tag" || return 1
    say "Tagged $src as $tag"
  done
  return 0
}

apply_stores() {
  run dc "$T_STORES" up -d --wait --no-build --pull never postgres redis minio
}

apply_bootstrap() {
  run dc "$T_BOOT" run --pull never --rm -T --no-deps minio-bootstrap
}

apply_migrate() {
  run dc "$T_MIGRATE" --profile migrate run --pull never --rm -T --no-deps migrate
}

apply_role() {  # the homayar_api role, once, like the runbook First Start step 7
  local sql status=0
  sql="$(mktemp "$H/config/.role.$TS.XXXXXX")"
  chmod 600 "$sql"
  {
    printf "SET log_min_error_statement = 'panic';\n"
    printf "CREATE ROLE homayar_api LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION IN ROLE homayar_app PASSWORD '%s';\n" "$(read_secret homayar_api_db_password)"
  } >"$sql"
  run_in "$sql" dc "$T_ROLE" exec -T postgres \
    sh -c 'psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"' || status=$?
  rm -f -- "$sql"
  return "$status"
}

apply_tombstone() {  # the tombstone volume permissions, once (First Start step 8)
  run dc "$T_TOMB" run --pull never --rm -T --no-deps --user 0 --cap-add CHOWN \
    --entrypoint sh api \
    -c 'chmod 700 /app/tombstones && chown 10001:10001 /app/tombstones && stat -c "%u:%g %a" /app/tombstones'
}

SEED_STDOUT_FILTER='^(Demo workspace|Meetings):'

apply_seed() {  # the demo seed, once, as a one-shot api container with
  # ENVIRONMENT=development set only for that container. Its stdout (with the
  # public seed password) goes to a 600 temp file, is never shown and never
  # logged; only the workspace and meeting count lines are kept.
  local out status=0
  out="$(mktemp "$H/logs/.seed.$TS.XXXXXX")"
  chmod 600 "$out"
  if ! timeout --kill-after=30 "$T_SEED" env API_MEM_LIMIT="$SEED_MEM_LIMIT" \
    bash "$H/dc" run --pull never --rm -T --no-deps \
    -e ENVIRONMENT=development -e ALLOW_MOCK_PROVIDERS=true \
    api python -m backend.database.demo_seed </dev/null >"$out" 2>&1; then
    status=1
  fi
  if [[ "$status" != 0 ]]; then
    say "The demo seed run failed. Filtered tail of its output:"
    show "$(grep -vE 'demo\.example\.com|Logins' "$out" | tail -n 20)"
    rm -f -- "$out"
    return 1
  fi
  say "Seed output (only the workspace and count lines are kept):"
  show "$(grep -E "$SEED_STDOUT_FILTER" "$out")"
  rm -f -- "$out"
  return 0
}

LOGIN_SETTER_PY='import sys
password = sys.stdin.readline().strip()
if len(password) < 20:
    raise SystemExit("REFUSED: the demo login password is too short")
from sqlalchemy import select
from backend.config import Settings
from backend.database import relational
from backend.database.demo_seed import DEMO_EMAIL_DOMAIN, DEMO_ROLES
from backend.database.relational import get_session_factory
from backend.database.saas_models import User
from backend.security.tokens import hash_password
relational.configure_database(Settings())
factory = get_session_factory()
for role in DEMO_ROLES:
    email = role + "@" + DEMO_EMAIL_DOMAIN
    with factory() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            raise SystemExit("REFUSED: the seed account is missing: " + role)
        user.password_hash = hash_password(password)
        db.commit()
print("demo-login-set: " + str(len(DEMO_ROLES)) + " accounts")'

apply_login() {  # one new random password on the four seed accounts, written to
  # secrets/demo-login; the password itself is never shown or logged
  local pw_file login_file status=0
  pw_file="$(secret_file .demo-login.pw.new)"
  login_file="$(secret_file demo-login)"
  (umask 077 && printf '%s\n' "$(hex_rand 16)" >"$pw_file")
  run_in "$pw_file" dc "$T_LOGIN" run --pull never --rm -T --no-deps \
    api python -c "$LOGIN_SETTER_PY" || status=$?
  if [[ "$status" != 0 ]]; then
    rm -f -- "$pw_file"
    return 1
  fi
  (
    umask 077
    {
      printf 'owner@demo.example.com\n'
      printf 'admin@demo.example.com\n'
      printf 'member@demo.example.com\n'
      printf 'viewer@demo.example.com\n'
      printf '%s\n' "$(cat "$pw_file")"
    } >"$login_file"
  )
  rm -f -- "$pw_file"
  say "Demo login written to $login_file (the four seed e-mails and the password, mode 600)"
  return 0
}

apply_nginx_t() {
  run dc "$T_CALL" run --pull never --rm -T --no-deps --entrypoint nginx proxy -t
}

apply_up() {
  run dc "$T_UP" up -d --no-build --pull never --wait
}

print_summary() {
  say ""
  say "==== demo.homayar.com summary ===="
  say "compose project:   $PROJECT (the only project this script touches)"
  say "published port:    127.0.0.1:$DEMO_PORT (host nginx proxies demo.homayar.com to it)"
  say "public URL:        https://demo.homayar.com:8080/app/ (after the owner nginx steps)"
  say "demo logins:       $H/secrets/demo-login (four e-mails, one password; never print it)"
  say "local health:      curl -fsS http://127.0.0.1:$DEMO_PORT/healthz"
  say "undo (destroys all demo data): bash $H/src/infra/scripts/demo-site.sh --undo --yes </dev/null"
  say "reset (new data, new password): --undo --yes, then --apply (runbook, Reset)"
  if [[ -n "$LOG" ]]; then say "log:               $LOG"; fi
}

# --- --undo -----------------------------------------------------------------------

demo_tag_refs() {  # the two demo tags (as they are, even when absent)
  local src
  for src in "$DEMO_API_IMAGE" "$DEMO_CONSOLE_WEB_IMAGE"; do
    printf '%s\n' "$(demo_tag_of "$src")"
  done
}

remove_demo_tags() {  # only the two demo tags, never an image id or a production tag
  local tag refs=()
  while IFS= read -r tag; do
    [[ -n "$tag" ]] || continue
    if [[ -n "$(image_id "$tag")" ]]; then
      refs+=("$tag")
    else
      say "Tag $tag is absent (nothing to remove)"
    fi
  done < <(demo_tag_refs)
  if [[ ${#refs[@]} -eq 0 ]]; then
    say "No demo image tags to remove."
    return 0
  fi
  run docker rmi "${refs[@]}"
}

leftover_demo_resources() {  # prints ids of containers, volumes and networks
  # still labelled with this project
  local ids found=""
  ids="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" 2>/dev/null || true)"
  [[ -z "$ids" ]] || found="$ids"
  ids="$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT" 2>/dev/null || true)"
  [[ -z "$ids" ]] || found="${found:+$found }$ids"
  ids="$(docker network ls -q --filter "label=com.docker.compose.project=$PROJECT" 2>/dev/null || true)"
  [[ -z "$ids" ]] || found="${found:+$found }$ids"
  printf '%s' "$found"
}

undo_apply() {  # --undo --yes
  local left final_log
  if demo_config_complete; then
    run dc "$T_DOWN" down --volumes --remove-orphans || return 1
  else
    say "No complete demo config found; skipping compose down (nothing to stop)."
  fi
  remove_demo_tags || return 1
  left="$(leftover_demo_resources)"
  if [[ -n "$left" ]]; then
    say "These resources with label com.docker.compose.project=$PROJECT still remain:"
    show "$left"
    say "Remove them by hand, then rerun --undo --yes."
    return 1
  fi
  say "No container, volume or network of $PROJECT remains."
  # Keep this run's log as the final log line of the demo: move it out of
  # logs/ before logs/ goes, so the lines below still have a file to go to.
  # The undo log holds no secret.
  final_log="$H/demo-site-undo-$TS.log"
  if [[ -n "$LOG" && -f "$LOG" ]]; then
    mv -f -- "$LOG" "$final_log"
    LOG="$final_log"
  fi
  rm -rf -- "$H/config" "$H/secrets" "$H/logs" "$H/dc" "$H/demo-site.lock"
  say "Removed the demo home except src/ and this log (config, secrets, logs, dc, lock)."
  say ""
  say "==== undo summary ===="
  say "Removed: the $PROJECT compose project (containers, volumes, networks), the two"
  say "homayar-demo/* image tags, and the demo home except src/."
  say "The source images and every production resource (homayar-onprem) are untouched."
  say "Finish by hand when you want the folder gone:"
  say "  rm -rf $H/src"
  say "  rm -f $final_log"
  say "  rmdir $H"
  return 0
}

undo_plan() {  # --undo without --yes: print what would go, change nothing
  local tag
  say "This is what --undo --yes would remove:"
  say "  1. the compose project $PROJECT: dc down --volumes --remove-orphans"
  say "     (containers, volumes homayar-demo_*, networks; all demo data is destroyed)"
  say "  2. the two demo image tags:"
  while IFS= read -r tag; do
    say "       $tag"
  done < <(demo_tag_refs)
  say "  3. the demo home except src/: $H/config $H/secrets $H/logs $H/dc $H/demo-site.lock"
  say "     (the undo log stays as $H/demo-site-undo-<UTC stamp>.log)"
  say "Nothing else: no image id, no production tag, no homayar-onprem resource;"
  say "src/ stays (remove it by hand with: rm -rf $H/src)."
  say "Nothing was changed."
  return 0
}

# --- finish -----------------------------------------------------------------------

finish() {
  local code="$1" line
  trap '' INT TERM
  say ""
  say "==== HOM-383 demo site ($MODE) ===="
  for line in "${RESULTS[@]}"; do say "  $line"; done
  say "exit code: $code"
  exit "$code"
}

on_signal() {
  trap '' INT TERM
  say "STOPPED by a signal. The steps are idempotent: fix the cause and rerun --apply."
  finish 130
}

usage() {
  echo "usage: bash $0 --check|--apply|--undo [--yes] </dev/null"
  echo "  --check  read-only preflight; changes nothing"
  echo "  --apply  build, seed and start the demo stack (idempotent)"
  echo "  --undo   print what --undo --yes would remove; with --yes, remove the demo"
}

# --- main -------------------------------------------------------------------------

main() {
  local api_tag cw_tag
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --check|--apply|--undo)
        [[ -z "$MODE" ]] || { usage; exit 2; }
        MODE="${1#--}"
        shift
        ;;
      --yes)
        YES=1
        shift
        ;;
      *)
        usage
        exit 2
        ;;
    esac
  done
  [[ -n "$MODE" ]] || { usage; exit 2; }
  if [[ "$YES" == 1 && "$MODE" != undo ]]; then usage; exit 2; fi

  check_identity || exit 2
  check_repo_under_home || exit 2
  if [[ -n "${COMPOSE_PROJECT_NAME:-}" && "$COMPOSE_PROJECT_NAME" != "$PROJECT" ]]; then
    say_err "REFUSED: COMPOSE_PROJECT_NAME is '$COMPOSE_PROJECT_NAME'; this script only runs project $PROJECT."
    exit 2
  fi
  api_tag="$(image_tag_part "$DEMO_API_IMAGE")" || exit 2
  cw_tag="$(image_tag_part "$DEMO_CONSOLE_WEB_IMAGE")" || exit 2

  if [[ "$MODE" == check ]]; then
    run_checks
    print_plan
    if [[ "$CHECK_READY" == 1 ]]; then finish 0; else finish 1; fi
  fi

  # --apply and --undo take the lock and write a log
  [[ -d "$H" ]] || install -d -m 700 "$H"
  [[ -d "$H/logs" ]] || install -d -m 700 "$H/logs"
  LOG="$H/logs/demo-site-$TS.log"
  (umask 077 && : >>"$LOG")
  exec 9>>"$H/demo-site.lock"
  if ! flock -n 9; then
    say "REFUSED: another run holds $H/demo-site.lock."
    say "Nothing was changed."
    exit 2
  fi
  say "HOM-383 demo site $MODE, home $H, log $LOG"

  if [[ "$MODE" == undo ]]; then
    if [[ "$YES" != 1 ]]; then
      undo_plan
      finish 2
    fi
    if undo_apply; then finish 0; else finish 1; fi
  fi

  # --apply: preflight first; a failure changes nothing
  say "Preflight (every --check item):"
  run_checks
  if [[ "$CHECK_READY" != 1 ]]; then
    refuse "the preflight failed; fix the FAIL items above first"
  fi

  if [[ -n "$ALREADY_APPLIED" ]] && step_done seed && step_done login; then
    say "The demo is already applied and healthy; reprinting the summary."
    apply_up || refuse "compose up --wait failed on the healthy demo"
    healthz_ok || refuse "http://127.0.0.1:$DEMO_PORT/healthz did not answer 200"
    print_summary
    finish 0
  fi

  say "Step 1: the demo home and its config (only what is missing)"
  setup_home "homayar-demo/api:$api_tag" "homayar-demo/console-web:$cw_tag" \
    || refuse "the demo home could not be written"

  say "Step 2: the demo image tags"
  apply_tags || refuse "tagging the demo images failed"

  say "Step 3: postgres, redis, minio (--wait)"
  apply_stores || finish 1

  if step_done bootstrap; then
    say "Step 4: minio-bootstrap already ran (marker)"
  else
    say "Step 4: minio-bootstrap"
    apply_bootstrap || finish 1
    step_mark bootstrap
  fi

  if step_done migrate; then
    say "Step 5: migrate already ran (marker)"
  else
    say "Step 5: migrate"
    apply_migrate || finish 1
    step_mark migrate
  fi

  if step_done role; then
    say "Step 6: the homayar_api role already exists (marker)"
  else
    say "Step 6: the homayar_api role (once)"
    apply_role || finish 1
    step_mark role
  fi

  if step_done tombstone; then
    say "Step 7: the tombstone volume is already prepared (marker)"
  else
    say "Step 7: the tombstone volume (once)"
    apply_tombstone || finish 1
    step_mark tombstone
  fi

  if step_done seed; then
    say "Step 8: the demo seed already ran (marker)"
  else
    say "Step 8: the demo seed (once, ENVIRONMENT=development only for that container)"
    apply_seed || finish 1
    step_mark seed
  fi

  if step_done login; then
    say "Step 9: the demo login password is already set (marker; the password is kept)"
  else
    say "Step 9: the demo login password (once)"
    apply_login || finish 1
    step_mark login
  fi

  say "Step 10: proxy nginx -t"
  apply_nginx_t || finish 1

  say "Step 11: up -d --wait (the whole demo stack)"
  apply_up || finish 1

  say "Step 12: health checks"
  if healthz_ok; then
    say "  http://127.0.0.1:$DEMO_PORT/healthz is 200"
  else
    say "  http://127.0.0.1:$DEMO_PORT/healthz did not answer 200"
    finish 1
  fi
  if app_ok; then
    say "  http://127.0.0.1:$DEMO_PORT/app/ is 200"
  else
    say "  http://127.0.0.1:$DEMO_PORT/app/ did not answer 200"
    finish 1
  fi

  print_summary
  finish 0
}

trap 'on_signal' INT TERM
main "$@"
