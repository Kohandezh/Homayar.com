#!/usr/bin/env bash
# Selftest for infra/scripts/demo-site.sh and infra/scripts/demo-site-nginx.sh
# (HOM-383).
#
# Runs where bash >= 4.4, flock and coreutils exist (the CI ubuntu runner, or
#   docker run --rm -v "$PWD":/work:ro -w /work ubuntu:24.04 \
#     bash infra/ci/tests/test-demo-site.sh
# ). It needs no network, no Docker and no root: id, docker, ss, ip, free, df,
# curl, nginx and systemctl are stubs first on PATH, DEMO_TEST_HOME points
# demo-site.sh at a fake demo home (with the script copied under its src/, as
# on the server), and DEMO_NGINX_TEST_ROOT points demo-site-nginx.sh at a
# fake /etc. The fake secrets hold the marker ZzSeeKrit77; every case asserts
# the marker never reaches the output or the script's log files.
set -Eeuo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
subject_src="$here/../../scripts/demo-site.sh"
subject_nginx="$here/../../scripts/demo-site-nginx.sh"

tmp="$(mktemp -d "${TMPDIR:-/tmp}/demo-site-test.XXXXXX")"
trap 'rm -rf -- "${tmp}"' EXIT

bin="$tmp/bin"
state="$tmp/state"
mkdir -p "$bin" "$state"
HOME_F="$tmp/home"
NGROOT="$tmp/nginx-root"
CASE_OUT="$tmp/case-out"
MARKER="ZzSeeKrit77"

export DEMO_TEST_HOME="$HOME_F"
export DEMO_NGINX_TEST_ROOT="$NGROOT"
export DEMO_STUB_STATE="$state"
export PATH="$bin:$PATH"

OUT=""
RC=0
API_SRC="homayar/api:d8e39a994f8b"
CW_SRC="homayar/console-web:d8e39a994f8b"
API_DEMO_TAG="homayar-demo/api:d8e39a994f8b"
CW_DEMO_TAG="homayar-demo/console-web:d8e39a994f8b"

detail() { printf '    ! %s\n' "$*"; }

# --- the fake world ---------------------------------------------------------------

cat >"$bin/id" <<'STUB_ID'
#!/usr/bin/env bash
# id stub: uid and name come from the stub state (id-uid, id-name).
S="${DEMO_STUB_STATE:?}"
uid="$(cat "$S/id-uid" 2>/dev/null || echo 1000)"
name="$(cat "$S/id-name" 2>/dev/null || echo gpu)"
case "${1:-}" in
  -un|-nu|-n) echo "$name" ;;
  -u) echo "$uid" ;;
  *) echo "uid=$uid($name) gid=1000(test)" ;;
esac
exit 0
STUB_ID
chmod +x "$bin/id"

write_id() { # write_id <name> <uid>
  printf '%s\n' "$2" >"$state/id-uid"
  printf '%s\n' "$1" >"$state/id-name"
}

prepare() { # a fresh fake demo home (src tree only) and stub state
  rm -rf -- "$HOME_F" "$state" "$NGROOT"
  mkdir -p "$bin" "$state" "$HOME_F/src/infra/scripts" "$HOME_F/src/infra/compose" \
    "$HOME_F/src/infra/manifests" \
    "$NGROOT/etc/nginx/sites-available" "$NGROOT/etc/nginx/sites-enabled" \
    "$NGROOT/etc/nginx/snippets" "$NGROOT/etc/nginx/conf.d" \
    "$NGROOT/etc/letsencrypt/live/demo.homayar.com" "$NGROOT/var/log/nginx"
  write_id gpu 1000
  cp -- "$subject_src" "$HOME_F/src/infra/scripts/demo-site.sh"
  printf '# test stub of compose.onprem.yaml\n' >"$HOME_F/src/infra/compose/compose.onprem.yaml"
  printf '# test stub of compose.onprem.demo.yaml\n' >"$HOME_F/src/infra/compose/compose.onprem.demo.yaml"
  printf '# snippet\nset_real_ip_from 127.0.0.1;\n' >"$NGROOT/etc/nginx/snippets/relay-tunnel-realip.conf"
  printf '# websocket map\nmap \$http_upgrade \$connection_upgrade { default upgrade; }\n' \
    >"$NGROOT/etc/nginx/conf.d/00-websocket-map.conf"
  : >"$NGROOT/etc/letsencrypt/live/demo.homayar.com/fullchain.pem"
  : >"$NGROOT/etc/letsencrypt/live/demo.homayar.com/privkey.pem"
  cat >"$state/routes" <<'ROUTES'
172.17.0.0/16 dev docker0 proto kernel scope link src 172.17.0.1
192.168.100.0/24 dev eth0 proto kernel scope link src 192.168.100.6
ROUTES
  printf '999999999999\n' >"$state/mem-available"
  printf '9999999999999\n' >"$state/disk-available"
  cat >"$state/images" <<'IMAGES'
homayar/api:d8e39a994f8b|sha256:1111111111111111111111111111111111111111111111111111111111111111
homayar/console-web:d8e39a994f8b|sha256:2222222222222222222222222222222222222222222222222222222222222222
postgres:17-alpine@sha256:266d3c257594e195b309ae9d1805e54c788f1d7b375f56bf6c367aac6c917e7b|sha256:3333333333333333333333333333333333333333333333333333333333333333
redis:7.4-alpine@sha256:8da19c7399dca900c6822a1cf30f9d2285585e2c4b457ff4095b0f4500054d3f|sha256:4444444444444444444444444444444444444444444444444444444444444444
nginx:1.30-alpine-slim@sha256:dede8895648b461178c3bc604f7eed4423efe5220b2c4d839f5eb3d34ac3f8f7|sha256:5555555555555555555555555555555555555555555555555555555555555555
bitnamilegacy/minio:2025.4.22@sha256:50cec18ac4184af4671a78aedd5554942c8ae105d51a465fa82037949046da01|sha256:6666666666666666666666666666666666666666666666666666666666666666
bitnamilegacy/minio-client:2025.4.16@sha256:8a86e441decf053093c5977d49290298295f850c7843652f93e970ab15871dd1|sha256:7777777777777777777777777777777777777777777777777777777777777777
IMAGES
  : >"$state/calls"
  : >"$state/nginx-calls"
  : >"$state/systemctl-calls"
}

prepare_applied() { # a world as after a successful --apply, with marker secrets
  local f
  prepare
  install -d -m 700 "$HOME_F/config/.steps" "$HOME_F/secrets" "$HOME_F/logs"
  for f in bootstrap migrate role tombstone seed login; do
    printf '20260101T000000Z\n' >"$HOME_F/config/.steps/$f"
  done
  printf 'APP_ENVIRONMENT=production\nAPI_IMAGE=%s\nCONSOLE_WEB_IMAGE=%s\nREGISTRATION_ENABLED=false\n' \
    "$API_DEMO_TAG" "$CW_DEMO_TAG" >"$HOME_F/config/settings.env"
  chmod 600 "$HOME_F/config/settings.env"
  for f in postgres_password homayar_api_db_password redis_password jwt_secret token_pepper \
    minio_root_user minio_root_password storage_access_key storage_secret_key; do
    printf '%s\n' "${MARKER}$f" >"$HOME_F/secrets/$f"
    chmod 600 "$HOME_F/secrets/$f"
  done
  {
    printf 'POSTGRES_PASSWORD=%saaaa\n' "$MARKER"
    printf 'MIGRATION_DATABASE_URL=postgresql+psycopg://homayar:%saaaa@postgres:5432/homayar\n' "$MARKER"
    printf 'DATABASE_URL=postgresql+psycopg://homayar_api:%sbbbb@postgres:5432/homayar\n' "$MARKER"
    printf 'REDIS_PASSWORD=%scccc\n' "$MARKER"
    printf 'REDIS_URL=redis://:%scccc@redis:6379/0\n' "$MARKER"
    printf 'JWT_SECRET=%sdddd111111111111111111111111111\n' "$MARKER"
    printf 'TOKEN_PEPPER=%seeee111111111111111111111111\n' "$MARKER"
    printf 'MINIO_ROOT_USER=%sminiouser\n' "$MARKER"
    printf 'MINIO_ROOT_PASSWORD=%sminiopass\n' "$MARKER"
    printf 'STORAGE_ACCESS_KEY=%sappkey\n' "$MARKER"
    printf 'STORAGE_SECRET_KEY=%sappsecret\n' "$MARKER"
  } >"$HOME_F/secrets/secrets.env"
  chmod 600 "$HOME_F/secrets/secrets.env"
  printf 'owner@demo.example.com\nadmin@demo.example.com\nmember@demo.example.com\nviewer@demo.example.com\n%s-login-pass\n' \
    "$MARKER" >"$HOME_F/secrets/demo-login"
  chmod 600 "$HOME_F/secrets/demo-login"
  cat >"$HOME_F/dc" <<DC
#!/bin/bash
set -euo pipefail
H='$HOME_F'
cd "\$H/src"
exec docker compose --project-name homayar-demo \\
  --env-file "\$H/config/settings.env" --env-file "\$H/secrets/secrets.env" \\
  --file infra/compose/compose.onprem.yaml --file infra/compose/compose.onprem.demo.yaml \\
  --profile onprem "\$@"
DC
  chmod 700 "$HOME_F/dc"
  printf '%s|%s\n' "$API_DEMO_TAG" "sha256:1111111111111111111111111111111111111111111111111111111111111111" \
    >>"$state/images"
  printf '%s|%s\n' "$CW_DEMO_TAG" "sha256:2222222222222222222222222222222222222222222222222222222222222222" \
    >>"$state/images"
  full_stack_rows
}

full_stack_rows() {
  cat >"$state/svc" <<'SVC'
proxy|running|healthy|
console-web|running|healthy|
api|running|healthy|
worker|running|healthy|
scheduler|running||
postgres|running|healthy|
redis|running|healthy|
minio|running|healthy|
minio-bootstrap|exited||0
SVC
  : >"$state/full-up"
}

# --- stubs -------------------------------------------------------------------------

cat >"$bin/ss" <<'STUB_SS'
#!/usr/bin/env bash
# ss stub: prints saved listeners for -H -ltn, nothing otherwise.
S="${DEMO_STUB_STATE:?}"
[[ "${1:-} ${2:-} ${3:-}" == "-H -ltn" || "$1 $2" == "-H -ltn" ]] || exit 0
[[ -f "$S/ss-listeners" ]] && cat "$S/ss-listeners"
exit 0
STUB_SS

cat >"$bin/ip" <<'STUB_IP'
#!/usr/bin/env bash
# ip stub: only "ip -4 route show", from the saved routes.
S="${DEMO_STUB_STATE:?}"
[[ "${1:-} ${2:-} ${3:-}" == "-4 route show" ]] || { echo "ip stub: unsupported" >&2; exit 1; }
cat "$S/routes"
exit 0
STUB_IP

cat >"$bin/free" <<'STUB_FREE'
#!/usr/bin/env bash
# free stub: one Mem line whose 7th field comes from the state.
S="${DEMO_STUB_STATE:?}"
printf '%s\n' '               total        used        free      shared  buff/cache   available'
printf 'Mem:      %s %s %s %s %s %s\n' 999999999999 1 1 1 1 "$(cat "$S/mem-available")"
exit 0
STUB_FREE

cat >"$bin/df" <<'STUB_DF'
#!/usr/bin/env bash
# df stub: any invocation answers with the saved available bytes.
S="${DEMO_STUB_STATE:?}"
printf '%s\n' 'Filesystem 1B-blocks Used Available Use% Mounted on'
printf 'tmpfs %s 1 %s 1%% /\n' 9999999999999 "$(cat "$S/disk-available")"
exit 0
STUB_DF

cat >"$bin/curl" <<'STUB_CURL'
#!/usr/bin/env bash
# curl stub: answers the demo health URLs from the stub state, no network.
S="${DEMO_STUB_STATE:?}"
url=""
fmt=""
out=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -w) fmt="$2"; shift 2 ;;
    --max-time) shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
code=000 body=""
case "$url" in
  http://127.0.0.1:8090/healthz)
    [[ -f "$S/full-up" ]] && { code=200; body='{"status":"ok"}'; } ;;
  http://127.0.0.1:8090/app/)
    [[ -f "$S/full-up" ]] && { code=200; body='<!doctype html>'; } ;;
esac
if [[ -n "$fmt" ]]; then
  printf '%s' "$fmt" | sed -e "s|%{http_code}|$code|g"
else
  [[ -n "$body" ]] && printf '%s' "$body"
fi
[[ "$code" == 200 ]]
STUB_CURL

cat >"$bin/nginx" <<'STUB_NGINX'
#!/usr/bin/env bash
# nginx stub: records the call; -t fails when the state asks for it.
S="${DEMO_STUB_STATE:?}"
printf '%s\n' "$*" >>"$S/nginx-calls"
if [[ "$1" == -t && -e "$S/nginx-t-fail" ]]; then
  echo "nginx: [emerg] unknown directive (injected)" >&2
  exit 1
fi
echo "nginx: the configuration file /etc/nginx/nginx.conf syntax is ok"
exit 0
STUB_NGINX

cat >"$bin/systemctl" <<'STUB_SYSTEMCTL'
#!/usr/bin/env bash
# systemctl stub: records the call; is-active answers active.
S="${DEMO_STUB_STATE:?}"
printf '%s\n' "$*" >>"$S/systemctl-calls"
if [[ "$1 $2" == "is-active nginx" ]]; then
  echo active
  exit 0
fi
exit 0
STUB_SYSTEMCTL

cat >"$bin/docker" <<'STUB_DOCKER'
#!/usr/bin/env bash
# docker stub for the demo-site selftest. It never runs a container: every
# answer comes from the stub state, every call is logged twice in $DEMO_STUB_STATE/calls
# (the full argv as "argv: ...", and a short semantic tag).
set -u
S="${DEMO_STUB_STATE:?}"
CALLS="$S/calls"
log() { printf '%s\n' "$*" >>"$CALLS"; }
state() {
  local f="$S/$1"
  if [[ -f "$f" ]]; then cat "$f"; else printf '%s' "${2:-}"; fi
}
img_row() { [[ -f "$S/images" ]] && grep -F -- "$1|" "$S/images" | head -n 1; }
img_id_of() {
  local row
  row="$(img_row "$1")"
  [[ -n "$row" ]] && printf '%s' "${row#*|}"
}
set_svc() { # set_svc <service> <row>
  local f="$S/svc"
  if [[ -s "$f" ]] && grep -qF -- "$1|" "$f"; then
    awk -F'|' -v s="$1" -v v="$2" '$1 == s { print v; next } { print }' "$f" >"$f.new"
    mv "$f.new" "$f"
  else
    printf '%s\n' "$2" >>"$f"
  fi
}

cmd="${1:-}"
shift || true
log "argv: $cmd $*"

case "$cmd" in

  compose)
    args=("$@")
    i=0
    sub=""
    while (( i < ${#args[@]} )); do
      case "${args[$i]}" in
        --project-name|-p|--env-file|--file|--profile) i=$((i + 2)) ;;
        --) i=$((i + 1)) ;;
        -*) i=$((i + 1)) ;;
        *) sub="${args[$i]}"; break ;;
      esac
    done
    rest=("${args[@]:$((i + 1))}")
    log "compose:$sub"
    case "$sub" in

      version)
        echo "Docker Compose version v2.40.0 (stub)"
        exit 0
        ;;

      up)
        svcs=()
        j=0
        while (( j < ${#rest[@]} )); do
          case "${rest[$j]}" in
            --pull) j=$((j + 2)) ;;
            -*) j=$((j + 1)) ;;
            *) svcs+=("${rest[$j]}"); j=$((j + 1)) ;;
          esac
        done
        if (( ${#svcs[@]} == 0 )); then
          cat >"$S/svc" <<'SVCUP'
proxy|running|healthy|
console-web|running|healthy|
api|running|healthy|
worker|running|healthy|
scheduler|running||
postgres|running|healthy|
redis|running|healthy|
minio|running|healthy|
minio-bootstrap|exited||0
SVCUP
          : >"$S/full-up"
          printf 'cid-proxy\ncid-console\ncid-api\ncid-worker\ncid-scheduler\ncid-postgres\ncid-redis\ncid-minio\n' >"$S/container-ids"
          printf 'vol-postgres\nvol-redis\nvol-minio\nvol-app\nvol-tombstone\n' >"$S/volume-ids"
          printf 'net-edge\nnet-ingress\nnet-backend\nnet-egress\nnet-outbound\n' >"$S/network-ids"
        else
          local_svc=""
          for local_svc in "${svcs[@]}"; do
            set_svc "$local_svc" "$local_svc|running|healthy|"
          done
          printf 'cid-stores\n' >"$S/container-ids"
        fi
        exit 0
        ;;

      run)
        envs=() entrypoint="" user="" pull="" svc="" words=("${rest[@]}") j=0 cmdv=()
        while (( j < ${#words[@]} )); do
          case "${words[$j]}" in
            --pull) pull="${words[$((j + 1))]}"; j=$((j + 2)) ;;
            -e) envs+=("${words[$((j + 1))]}"); j=$((j + 2)) ;;
            --entrypoint) entrypoint="${words[$((j + 1))]}"; j=$((j + 2)) ;;
            --user) user="${words[$((j + 1))]}"; j=$((j + 2)) ;;
            --cap-add) j=$((j + 2)) ;;
            --rm|-T|--no-deps|-*) j=$((j + 1)) ;;
            *) svc="${words[$j]}"; cmdv=("${words[@]:$((j + 1))}"); break ;;
          esac
        done
        joined="${cmdv[*]}"
        if [[ "$svc" == minio-bootstrap ]]; then
          log "compose:run:minio-bootstrap"
          echo "Created bucket homayar-private."
          echo "access key: 0123456789abcdef012345"
          exit 0
        fi
        if [[ "$svc" == migrate ]]; then
          log "compose:run:migrate"
          echo "INFO  [alembic] upgrade head (stub)"
          exit 0
        fi
        if [[ "$svc" == proxy && "$entrypoint" == nginx ]]; then
          log "compose:run:proxy:nginx_t"
          echo "nginx: configuration file /etc/nginx/nginx.conf test successful (stub)"
          exit 0
        fi
        if [[ "$svc" == api && "$joined" == 'python -m backend.database.demo_seed'* ]]; then
          log "compose:run:api:demo_seed env=${envs[*]}"
          echo "Demo workspace: stub workspace (ws-1)"
          echo "Meetings: 270"
          echo "Logins (all roles share one password):"
          echo "  owner   owner@demo.example.com  Homayar-Demo-1405"
          if [[ -e "$S/fail-seed" ]]; then
            echo "seed failed (injected)" >&2
            exit 1
          fi
          exit 0
        fi
        if [[ "$svc" == api && "$user" == 0 ]]; then
          log "compose:run:api:tombstone"
          echo "10001:10001 700"
          exit 0
        fi
        if [[ "$svc" == api && "$joined" == 'python -c'* ]]; then
          log "compose:run:api:login_setter"
          IFS= read -r pw
          cat >/dev/null
          if (( ${#pw} < 20 )); then
            echo "REFUSED: the demo login password is too short" >&2
            exit 1
          fi
          printf '%s' "$pw" >"$S/login-pw"
          echo "demo-login-set: 4 accounts"
          exit 0
        fi
        log "compose:run:$svc:unknown"
        echo "run stub: unsupported: $svc $joined" >&2
        exit 1
        ;;

      down)
        log "compose:down"
        rm -f -- "$S/svc" "$S/full-up" "$S/container-ids"
        [[ -e "$S/down-leaves-volume" ]] || rm -f -- "$S/volume-ids"
        rm -f -- "$S/network-ids"
        exit 0
        ;;

      ps)
        log "compose:ps"
        [[ -f "$S/svc" ]] && cat "$S/svc"
        exit 0
        ;;

      exec)
        payload="${rest[*]}"
        if [[ "$payload" == *psql* && "$payload" == *postgres* ]]; then
          sql="$(cat)"
          if [[ "$sql" == *'CREATE ROLE homayar_api'* ]]; then
            log "compose:exec:postgres:role"
            echo "CREATE ROLE"
            exit 0
          fi
        fi
        log "compose:exec:unsupported:$payload"
        echo "exec stub: unsupported payload: $payload" >&2
        exit 1
        ;;

      config)
        log "compose:config"
        exit 0
        ;;

      *)
        log "compose:unknown:$sub"
        echo "compose stub: unsupported: $sub ${rest[*]}" >&2
        exit 1
        ;;
    esac
    ;;

  image)
    # image inspect --format FMT REF
    [[ "${1:-}" == inspect ]] || { log "docker:image-unknown:$*"; exit 1; }
    shift
    fmt="" ref=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --format) fmt="$2"; shift 2 ;;
        *) ref="$1"; shift ;;
      esac
    done
    id="$(img_id_of "$ref")"
    if [[ -z "$id" ]]; then
      echo "cannot find image $ref" >&2
      exit 1
    fi
    printf '%s\n' "$id"
    exit 0
    ;;

  tag)
    log "docker:tag"
    srcid="$(img_id_of "$1")"
    if [[ -z "$srcid" ]]; then echo "cannot find image $1" >&2; exit 1; fi
    if [[ -z "$(img_id_of "$2")" ]]; then
      printf '%s|%s\n' "$2" "$srcid" >>"$S/images"
    fi
    exit 0
    ;;

  rmi)
    log "docker:rmi"
    for ref in "$@"; do
      grep -vF -- "$ref|" "$S/images" >"$S/images.new" 2>/dev/null || true
      mv "$S/images.new" "$S/images"
    done
    exit 0
    ;;

  ps)
    if [[ "${1:-}" == -aq ]]; then
      log "docker:ps-leftover"
      [[ -f "$S/container-ids" ]] && cat "$S/container-ids"
      exit 0
    fi
    log "docker:ps-ports"
    if [[ -f "$S/full-up" || -e "$S/port-by-demo" ]]; then
      echo "homayar-demo-proxy-1 127.0.0.1:8090->8080/tcp"
    fi
    exit 0
    ;;

  network)
    if [[ "${1:-}" == ls ]]; then
      log "docker:network-ls"
      [[ -f "$S/network-ids" ]] && cat "$S/network-ids"
      exit 0
    fi
    if [[ "${1:-}" == inspect ]]; then
      log "docker:network-inspect"
      printf '172.29.31.0/24|'
      exit 0
    fi
    log "docker:network-unknown:$*"
    exit 1
    ;;

  volume)
    log "docker:volume-ls"
    [[ -f "$S/volume-ids" ]] && cat "$S/volume-ids"
    exit 0
    ;;

  *)
    log "docker:unknown:$cmd"
    echo "docker stub: unsupported: $cmd $*" >&2
    exit 1
    ;;
esac
STUB_DOCKER

chmod +x "$bin/ss" "$bin/ip" "$bin/free" "$bin/df" "$bin/curl" "$bin/nginx" "$bin/systemctl" "$bin/docker"

# --- case helpers ------------------------------------------------------------------

run_script() { # run_script <args...>: runs the copied demo-site.sh
  RC=0
  OUT="$(bash "$HOME_F/src/infra/scripts/demo-site.sh" "$@" </dev/null 2>&1)" || RC=$?
  printf '%s\n' "$OUT" >>"$CASE_OUT"
}

run_nginx() { # run_nginx <args...>: runs demo-site-nginx.sh as root
  RC=0
  OUT="$(bash "$subject_nginx" "$@" </dev/null 2>&1)" || RC=$?
  printf '%s\n' "$OUT" >>"$CASE_OUT"
}

want_rc() {
  [[ "$RC" == "$1" ]] || { detail "exit $RC, want $1"; exit 1; }
}
want_out() {
  grep -qF -- "$1" <<<"$OUT" || { detail "output lacks: $1"; exit 1; }
}
want_out_re() {
  grep -qE -- "$1" <<<"$OUT" || { detail "output lacks re: $1"; exit 1; }
}
want_no_out() {
  ! grep -qF -- "$1" <<<"$OUT" || { detail "output must not contain: $1"; exit 1; }
}
file_has() {
  grep -qF -- "$2" "$1" || { detail "$1 lacks: $2"; exit 1; }
}
file_mode() {
  local got
  got="$(stat -c %a "$1")"
  [[ "$got" == "$2" ]] || { detail "$1 mode $got, want $2"; exit 1; }
}
calls_has() {
  grep -qF -- "$1" "$state/calls" || { detail "calls lack: $1"; exit 1; }
}
calls_lack() {
  ! grep -qF -- "$1" "$state/calls" || { detail "calls must not contain: $1"; exit 1; }
}
call_before() { # call_before <a> <b>: the first a comes before the first b
  local a b
  a="$(grep -nF -- "$1" "$state/calls" | head -n 1 | cut -d: -f1)"
  b="$(grep -nF -- "$2" "$state/calls" | head -n 1 | cut -d: -f1)"
  { [[ -n "$a" && -n "$b" && "$a" -lt "$b" ]]; } || {
    detail "call '$1' must come before '$2'"
    exit 1
  }
}
last_call_after() { # last_call_after <a> <b>: the LAST a comes after the first b
  local a b
  a="$(grep -nF -- "$1" "$state/calls" | tail -n 1 | cut -d: -f1)"
  b="$(grep -nF -- "$2" "$state/calls" | head -n 1 | cut -d: -f1)"
  { [[ -n "$a" && -n "$b" && "$a" -gt "$b" ]]; } || {
    detail "the last '$1' must come after '$2'"
    exit 1
  }
}
all_compose_have_project() { # every docker compose argv (except the version
  # probe, which addresses no project) carries the project name
  local bad
  bad="$(grep '^argv: compose' "$state/calls" | grep -v -- '^argv: compose version' | grep -v -- '--project-name homayar-demo' || true)"
  [[ -z "$bad" ]] || { detail "a compose call without --project-name homayar-demo: $bad"; exit 1; }
}
no_foreign_names() {
  ! grep -qF -- "homayar-onprem" "$state/calls" || { detail "a call names homayar-onprem"; exit 1; }
  ! grep -qF -- "/home/gpu/homayar/" "$state/calls" || { detail "a call names a path under /home/gpu/homayar/"; exit 1; }
  ! grep -qF -- "compose.onprem.app-server.yaml" "$state/calls" || { detail "a call uses the app-server overlay"; exit 1; }
}
want_no_marker_anywhere() { # the marker and the generated login password
  local f pw
  ! grep -qF -- "$MARKER" <<<"$OUT" || { detail "secret marker in the output"; exit 1; }
  for f in "$HOME_F"/logs/*.log "$HOME_F"/demo-site-undo-*.log; do
    [[ -e "$f" ]] || continue
    ! grep -qF -- "$MARKER" "$f" || { detail "secret marker in $f"; exit 1; }
  done
  if [[ -f "$HOME_F/secrets/demo-login" ]]; then
    pw="$(tail -n 1 "$HOME_F/secrets/demo-login")"
    ! grep -qF -- "$pw" <<<"$OUT" || { detail "the demo login password is in the output"; exit 1; }
    for f in "$HOME_F"/logs/*.log; do
      [[ -e "$f" ]] || continue
      ! grep -qF -- "$pw" "$f" || { detail "the demo login password is in $f"; exit 1; }
    done
  fi
}

passed=0
failed=0
run_case() { # run_case <case function>: one PASS or FAIL line per case
  local name="${1#c_}" why="" f
  : >"$CASE_OUT"
  if "$1"; then :; else why="case failed"; fi
  if grep -qF -- "$MARKER" "$CASE_OUT"; then
    why="secret marker in a run's output"
  fi
  for f in "$HOME_F"/logs/*.log; do
    [[ -e "$f" ]] || continue
    if grep -qF -- "$MARKER" "$f"; then why="secret marker in $f"; fi
  done
  if ! grep -qF -- "Homayar-Demo-1405" "$CASE_OUT"; then :; else
    why="the public seed password reached the output"
  fi
  if [[ -z "$why" ]]; then
    passed=$((passed + 1))
    printf 'PASS %s\n' "$name"
  else
    failed=$((failed + 1))
    printf 'FAIL %s (%s)\n' "$name" "$why"
  fi
}

# --- cases: demo-site.sh -----------------------------------------------------------

c_usage_no_mode() (
  prepare
  run_script
  want_rc 2
  want_out "usage"
)

c_usage_unknown_flag() (
  prepare
  run_script --turbo
  want_rc 2
  want_out "usage"
)

c_usage_yes_without_undo() (
  prepare
  run_script --apply --yes
  want_rc 2
  want_out "usage"
)

c_root_refused() (
  prepare
  write_id root 0
  run_script --check
  want_rc 2
  want_out "root"
)

c_wrong_user() (
  prepare
  write_id bob 1001
  run_script --check
  want_rc 2
  want_out "bob"
)

c_check_ready() (
  prepare
  run_script --check
  want_rc 0
  want_out "C1 PASS"
  want_out "C4 PASS"
  want_out "C10 INFO"
  want_out "Plan (what --apply would do"
  all_compose_have_project
  no_foreign_names
)

c_check_writes_nothing() (
  prepare
  local before after
  before="$(cd "$HOME_F" && find . | LC_ALL=C sort)"
  run_script --check
  want_rc 0
  after="$(cd "$HOME_F" && find . | LC_ALL=C sort)"
  [[ "$before" == "$after" ]] || { detail "--check changed the tree"; exit 1; }
  calls_lack "docker:tag"
  calls_lack "docker:rmi"
  calls_lack "compose:up"
  calls_lack "compose:run"
  calls_lack "compose:down"
  calls_lack "compose:exec"
)

c_port_busy_foreign() (
  prepare
  printf 'LISTEN 0 128 127.0.0.1:8090 0.0.0.0:* users:(("foreign",pid=1,fd=3))\n' >"$state/ss-listeners"
  run_script --check
  want_rc 1
  want_out "C6 FAIL"
  run_script --apply
  want_rc 2
  want_out "REFUSED"
  calls_lack "docker:tag"
  calls_lack "compose:up"
)

c_subnet_overlap() (
  prepare
  printf '172.29.31.0/24 dev eth9 proto static\n' >>"$state/routes"
  run_script --check
  want_rc 1
  want_out "C7 FAIL"
  run_script --apply
  want_rc 2
  calls_lack "docker:tag"
  calls_lack "compose:up"
)

c_subnet_overlap_own_bridge() (
  prepare
  printf '172.29.31.0/24 dev br-demo0\n' >>"$state/routes"
  printf 'netdemo1\n' >"$state/network-ids"
  run_script --check
  want_rc 0
  want_out "C7 PASS"
)

c_missing_image() (
  prepare
  grep -v -- "$API_SRC|" "$state/images" >"$state/images.new" && mv "$state/images.new" "$state/images"
  run_script --check
  want_rc 1
  want_out "C4 FAIL"
  run_script --apply
  want_rc 2
  calls_lack "docker:tag"
  calls_lack "compose:up"
)

c_missing_third_party_image() (
  prepare
  grep -v -- "postgres:17-alpine" "$state/images" >"$state/images.new" && mv "$state/images.new" "$state/images"
  run_script --check
  want_rc 1
  want_out "C5 FAIL"
)

c_low_memory() (
  prepare
  printf '1000000000\n' >"$state/mem-available"
  run_script --check
  want_rc 1
  want_out "C8 FAIL"
)

c_low_disk() (
  prepare
  printf '1000000000\n' >"$state/disk-available"
  run_script --check
  want_rc 1
  want_out "C9 FAIL"
)

c_apply_happy() (
  prepare
  run_script --apply
  want_rc 0
  # the apply order of the SPEC
  call_before "docker:tag" "compose:up"
  call_before "compose:up" "compose:run:minio-bootstrap"
  call_before "compose:run:minio-bootstrap" "compose:run:migrate"
  call_before "compose:run:migrate" "compose:exec:postgres:role"
  call_before "compose:exec:postgres:role" "compose:run:api:tombstone"
  call_before "compose:run:api:tombstone" "compose:run:api:demo_seed"
  call_before "compose:run:api:demo_seed" "compose:run:api:login_setter"
  call_before "compose:run:api:login_setter" "compose:run:proxy:nginx_t"
  last_call_after "compose:up" "compose:run:proxy:nginx_t"
  # the seed run: development only for that container, pull never, raised limit env
  file_has "$state/calls" "compose:run:api:demo_seed env=ENVIRONMENT=development ALLOW_MOCK_PROVIDERS=true"
  # the exact demo tag names (a doubled tag once slipped through substring checks)
  file_has "$state/calls" "argv: tag homayar/api:d8e39a994f8b $API_DEMO_TAG"
  file_has "$state/calls" "argv: tag homayar/console-web:d8e39a994f8b $CW_DEMO_TAG"
  grep -q "^$API_DEMO_TAG|" "$state/images" || { detail "images lack the exact tag line $API_DEMO_TAG"; exit 1; }
  grep -qx "API_IMAGE=$API_DEMO_TAG" "$HOME_F/config/settings.env" || { detail "settings.env API_IMAGE is not the exact demo tag"; exit 1; }
  grep -qx "CONSOLE_WEB_IMAGE=$CW_DEMO_TAG" "$HOME_F/config/settings.env" || { detail "settings.env CONSOLE_WEB_IMAGE is not the exact demo tag"; exit 1; }
  file_has "$HOME_F/config/settings.env" "PUBLIC_ORIGIN=https://demo.homayar.com:8080"
  file_has "$HOME_F/config/settings.env" "REGISTRATION_ENABLED=false"
  file_has "$HOME_F/config/settings.env" "ENABLE_FREE_DEMO=false"
  file_mode "$HOME_F/config/settings.env" 600
  file_has "$HOME_F/dc" "--project-name homayar-demo"
  file_has "$HOME_F/dc" "compose.onprem.demo.yaml"
  file_mode "$HOME_F/dc" 700
  file_has "$HOME_F/secrets/secrets.env" "DATABASE_URL=postgresql+psycopg://homayar_api:"
  # The image has psycopg 3 only: a bare postgresql:// migration DSN fails.
  file_has "$HOME_F/secrets/secrets.env" "MIGRATION_DATABASE_URL=postgresql+psycopg://homayar:"
  file_mode "$HOME_F/secrets/secrets.env" 600
  file_mode "$HOME_F/secrets/demo-login" 600
  [[ "$(wc -l <"$HOME_F/secrets/demo-login" | tr -d '[:space:]')" == 5 ]] || { detail "demo-login must hold 5 lines"; exit 1; }
  file_has "$HOME_F/secrets/demo-login" "owner@demo.example.com"
  file_has "$HOME_F/secrets/demo-login" "viewer@demo.example.com"
  # the summary, the health lines, the markers
  want_out "summary"
  want_out "healthz is 200"
  want_out "/app/ is 200"
  local m
  for m in bootstrap migrate role tombstone seed login; do
    [[ -f "$HOME_F/config/.steps/$m" ]] || { detail "marker $m missing"; exit 1; }
  done
  # only this project, never production
  all_compose_have_project
  no_foreign_names
  calls_lack "docker:rmi"
  want_no_marker_anywhere
)

c_apply_idempotent() (
  prepare
  run_script --apply
  want_rc 0
  local login_before login_after seed_runs
  login_before="$(sha256sum "$HOME_F/secrets/demo-login" | awk '{ print $1 }')"
  run_script --check
  want_rc 0
  want_out "C10 PASS"
  run_script --apply
  want_rc 0
  want_out "already applied and healthy"
  seed_runs="$(grep -cF -- "compose:run:api:demo_seed" "$state/calls" || true)"
  [[ "$seed_runs" == 1 ]] || { detail "the seed ran $seed_runs times, want 1"; exit 1; }
  login_after="$(sha256sum "$HOME_F/secrets/demo-login" | awk '{ print $1 }')"
  [[ "$login_before" == "$login_after" ]] || { detail "the second apply changed the password"; exit 1; }
  want_no_marker_anywhere
)

c_apply_marker_secrets_kept() (
  prepare_applied
  run_script --check
  want_rc 0
  run_script --apply
  want_rc 0
  want_out "already applied and healthy"
  file_has "$HOME_F/secrets/secrets.env" "$MARKER"
  calls_lack "compose:run:api:demo_seed"
)

c_seed_failure() (
  prepare
  : >"$state/fail-seed"
  run_script --apply
  want_rc 1
  want_out "seed run failed"
  [[ ! -f "$HOME_F/config/.steps/seed" ]] || { detail "the seed marker was written despite the failure"; exit 1; }
  want_no_marker_anywhere
)

c_undo_without_yes() (
  prepare
  run_script --apply
  want_rc 0
  run_script --undo
  want_rc 2
  want_out "Nothing was changed."
  calls_lack "compose:down"
  calls_lack "docker:rmi"
  [[ -f "$HOME_F/config/settings.env" ]] || { detail "undo without --yes removed config"; exit 1; }
)

c_undo_yes() (
  prepare
  run_script --apply
  want_rc 0
  run_script --undo --yes
  want_rc 0
  calls_has "compose:down"
  grep -qxF "argv: rmi $API_DEMO_TAG $CW_DEMO_TAG" "$state/calls" || {
    detail "undo must rmi exactly the two demo tags in one call"; exit 1; }
  file_has "$state/images" "$API_SRC|"
  file_has "$state/images" "$CW_SRC|"
  ! grep -qF -- "$API_DEMO_TAG|" "$state/images" || { detail "the demo api tag survived undo"; exit 1; }
  [[ -d "$HOME_F/src" ]] || { detail "src/ must survive undo"; exit 1; }
  [[ ! -e "$HOME_F/config" && ! -e "$HOME_F/secrets" && ! -e "$HOME_F/dc" ]] || { detail "undo left config, secrets or dc"; exit 1; }
  want_out "rm -rf $HOME_F/src"
  # The final undo log survives at the home root, and every line after the
  # removal of logs/ still reaches it (no write to a removed file).
  want_no_out "No such file or directory"
  undo_logs=("$HOME_F"/demo-site-undo-*.log)
  [[ -f "${undo_logs[0]}" ]] || { detail "the final undo log is missing"; exit 1; }
  file_has "${undo_logs[0]}" "==== undo summary ===="
  file_has "${undo_logs[0]}" "exit code: 0"
  want_no_marker_anywhere
)

c_undo_leftover_volume() (
  prepare
  run_script --apply
  want_rc 0
  : >"$state/down-leaves-volume"
  run_script --undo --yes
  want_rc 1
  want_out "still remain"
)

# --- cases: demo-site-nginx.sh -----------------------------------------------------

c_nginx_not_root() (
  prepare
  write_id gpu 1000
  run_nginx --check
  want_rc 2
  want_out "root"
)

c_nginx_check_ok() (
  prepare
  printf 'LISTEN 0 128 127.0.0.1:8090 0.0.0.0:*\n' >"$state/ss-listeners"
  write_id root 0
  run_nginx --check
  want_rc 0
  want_out "P6 PASS"
  want_out "No live site file yet"
  [[ ! -e "$NGROOT/etc/nginx/sites-enabled/demo.homayar.com.conf" ]] || { detail "--check wrote the site"; exit 1; }
)

c_nginx_check_no_cert() (
  prepare
  printf 'LISTEN 0 128 127.0.0.1:8090 0.0.0.0:*\n' >"$state/ss-listeners"
  rm -f -- "$NGROOT/etc/letsencrypt/live/demo.homayar.com/fullchain.pem"
  write_id root 0
  run_nginx --check
  want_rc 1
  want_out "P2 FAIL"
)

c_nginx_check_other_site() (
  prepare
  printf 'LISTEN 0 128 127.0.0.1:8090 0.0.0.0:*\n' >"$state/ss-listeners"
  printf 'server { server_name demo.homayar.com; }\n' >"$NGROOT/etc/nginx/sites-enabled/other.conf"
  write_id root 0
  run_nginx --check
  want_rc 1
  want_out "P7 FAIL"
)

c_nginx_apply_ok() (
  prepare
  printf 'LISTEN 0 128 127.0.0.1:8090 0.0.0.0:*\n' >"$state/ss-listeners"
  write_id root 0
  run_nginx --apply
  want_rc 0
  file_has "$NGROOT/etc/nginx/sites-available/demo.homayar.com.conf" "proxy_pass http://127.0.0.1:8090;"
  file_has "$NGROOT/etc/nginx/sites-available/demo.homayar.com.conf" "proxy_redirect http://demo.homayar.com:8080/ https://\$http_host/;"
  file_has "$NGROOT/etc/nginx/sites-available/demo.homayar.com.conf" "server_name demo.homayar.com;"
  file_has "$NGROOT/etc/nginx/sites-available/demo.homayar.com.conf" "listen 127.0.0.1:10443 ssl http2 proxy_protocol;"
  file_mode "$NGROOT/etc/nginx/sites-available/demo.homayar.com.conf" 644
  [[ "$(readlink "$NGROOT/etc/nginx/sites-enabled/demo.homayar.com.conf")" == "../sites-available/demo.homayar.com.conf" ]] || {
    detail "wrong symlink target"; exit 1; }
  file_has "$state/nginx-calls" "-t"
  file_has "$state/systemctl-calls" "reload nginx"
  ! grep -qF -- "restart" "$state/systemctl-calls" || { detail "systemctl restart must never run"; exit 1; }
  file_has "$state/systemctl-calls" "is-active nginx"
  # only the demo site file and its symlink were added
  local links
  links="$(ls -A "$NGROOT/etc/nginx/sites-enabled")"
  [[ "$links" == "demo.homayar.com.conf" ]] || { detail "sites-enabled holds: $links"; exit 1; }
)

c_nginx_apply_t_fail_restores() (
  prepare
  printf 'LISTEN 0 128 127.0.0.1:8090 0.0.0.0:*\n' >"$state/ss-listeners"
  printf '# the old live file\nserver { old; }\n' >"$NGROOT/etc/nginx/sites-available/demo.homayar.com.conf"
  ln -s ../sites-available/demo.homayar.com.conf "$NGROOT/etc/nginx/sites-enabled/demo.homayar.com.conf"
  : >"$state/nginx-t-fail"
  write_id root 0
  run_nginx --apply
  want_rc 1
  want_out "restoring the previous state"
  file_has "$NGROOT/etc/nginx/sites-available/demo.homayar.com.conf" "# the old live file"
  [[ "$(readlink "$NGROOT/etc/nginx/sites-enabled/demo.homayar.com.conf")" == "../sites-available/demo.homayar.com.conf" ]] || {
    detail "the symlink state was not restored"; exit 1; }
  [[ ! -s "$state/systemctl-calls" ]] || { detail "a systemctl call ran although nginx -t failed"; exit 1; }
)

c_nginx_apply_t_fail_no_previous() (
  prepare
  printf 'LISTEN 0 128 127.0.0.1:8090 0.0.0.0:*\n' >"$state/ss-listeners"
  : >"$state/nginx-t-fail"
  write_id root 0
  run_nginx --apply
  want_rc 1
  [[ ! -e "$NGROOT/etc/nginx/sites-available/demo.homayar.com.conf" ]] || { detail "the site file must be gone"; exit 1; }
  [[ ! -e "$NGROOT/etc/nginx/sites-enabled/demo.homayar.com.conf" ]] || { detail "the symlink must be gone"; exit 1; }
  [[ ! -s "$state/systemctl-calls" ]] || { detail "a systemctl call ran although nginx -t failed"; exit 1; }
)

c_nginx_undo() (
  prepare
  printf 'LISTEN 0 128 127.0.0.1:8090 0.0.0.0:*\n' >"$state/ss-listeners"
  write_id root 0
  run_nginx --apply
  want_rc 0
  : >"$state/systemctl-calls"
  run_nginx --undo
  want_rc 0
  [[ ! -e "$NGROOT/etc/nginx/sites-enabled/demo.homayar.com.conf" ]] || { detail "the symlink must be removed"; exit 1; }
  [[ ! -e "$NGROOT/etc/nginx/sites-available/demo.homayar.com.conf" ]] || { detail "the site file must be removed"; exit 1; }
  local backups=0 f
  for f in "$NGROOT"/etc/nginx/sites-available/demo.homayar.com.conf.bak-*; do
    [[ -e "$f" ]] && backups=$((backups + 1))
  done
  [[ "$backups" == 1 ]] || { detail "expected one kept backup, got $backups"; exit 1; }
  file_has "$state/nginx-calls" "-t"
  file_has "$state/systemctl-calls" "reload nginx"
)

# --- run all cases ---------------------------------------------------------------

run_case c_usage_no_mode
run_case c_usage_unknown_flag
run_case c_usage_yes_without_undo
run_case c_root_refused
run_case c_wrong_user
run_case c_check_ready
run_case c_check_writes_nothing
run_case c_port_busy_foreign
run_case c_subnet_overlap
run_case c_subnet_overlap_own_bridge
run_case c_missing_image
run_case c_missing_third_party_image
run_case c_low_memory
run_case c_low_disk
run_case c_apply_happy
run_case c_apply_idempotent
run_case c_apply_marker_secrets_kept
run_case c_seed_failure
run_case c_undo_without_yes
run_case c_undo_yes
run_case c_undo_leftover_volume
run_case c_nginx_not_root
run_case c_nginx_check_ok
run_case c_nginx_check_no_cert
run_case c_nginx_check_other_site
run_case c_nginx_apply_ok
run_case c_nginx_apply_t_fail_restores
run_case c_nginx_apply_t_fail_no_previous
run_case c_nginx_undo

printf '%d passed, %d failed\n' "$passed" "$failed"
[[ "$failed" -eq 0 ]]
