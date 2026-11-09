#!/usr/bin/env bash
# Selftest for infra/scripts/app-server-release.sh (HOM-358).
#
# Runs where bash >= 4.4, flock and coreutils exist (the CI ubuntu runner, or
#   docker run --rm -v "$PWD":/work:ro -w /work ubuntu:24.04 \
#     bash infra/ci/tests/test-app-server-release.sh
# ). It needs no network, no Docker and no root: the docker, curl and id
# commands are stubs first on PATH, and APPREL_TEST_HOME points the script at
# a fake /home/gpu/homayar built in a temp folder. The fake secrets hold the
# marker ZzSeeKrit42; every case asserts the marker never reaches the output
# or the script's log file.
set -Eeuo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
subject="${here}/../../scripts/app-server-release.sh"

tmp="$(mktemp -d "${TMPDIR:-/tmp}/apprel-test.XXXXXX")"
trap 'rm -rf -- "${tmp}"' EXIT

bin="$tmp/bin"
state="$tmp/state"
mkdir -p "$bin" "$state"
MARKER="ZzSeeKrit42"

# The start release 019a906aa9ef and the target release a651110b03c3 (SPEC
# section 2). The target head migration revision equals its sha12 by choice.
S12="019a906aa9ef"
SHA_S="019a906aa9ef2edefc8223d478eeb317e4f55b0c"
T12="a651110b03c3"
SHA_T="a651110b03c30123456789abcdef0123456789ab"
BASE_REV="a7c3e9f5b2d1"
NEW_REVS=(6d4ec2d2e498 f2c6a8e4b1d7 47676f445509 565d2ccd4fca e4b8d1f6a3c7 5067ea773c0b 484a6df74abf)
HEAD_REV="$T12"
START_API_ID="sha256:1111111111111111111111111111111111111111111111111111111111111111"
START_CW_ID="sha256:2222222222222222222222222222222222222222222222222222222222222222"

HOME_F="$tmp/home"    # the fake /home/gpu/homayar
CASE_OUT="$tmp/case-out" # every run's output of the current case (marker check)
export APPREL_TEST_HOME="$HOME_F"
export APPREL_STUB_STATE="$state"
export PATH="$bin:$PATH"

OUT=""
RC=0

detail() { printf '    ! %s\n' "$*"; }

# --- fake home and stub state -------------------------------------------------

make_release() { # make_release <dir> <sha40> <rev> [more revs, in chain order]
  local dir="$1" sha="$2" down="None" rev i=0
  shift 2
  mkdir -p "$dir/infra/compose" "$dir/infra/docker" "$dir/Homayar/backend/migrations/versions"
  printf '%s\n' "$sha" >"$dir/SOURCE_SHA"
  {
    printf 'services:\n  api:\n    environment:\n'
    printf '      STORAGE_PRESIGNED_DELIVERY: "false"\n'
  } >"$dir/infra/compose/compose.onprem.yaml"
  : >"$dir/infra/compose/compose.onprem.app-server.yaml"
  printf 'FROM test\n' >"$dir/infra/docker/api.Dockerfile"
  printf 'FROM test\n' >"$dir/infra/docker/console-web.Dockerfile"
  for rev in "$@"; do
    i=$((i + 1))
    if [[ "$down" == "None" ]]; then
      printf '"""test"""\nrevision: str = "%s"\ndown_revision: Union[str, None] = None\n' "$rev" \
        >"$dir/Homayar/backend/migrations/versions/$(printf '%04d' "$i")_${rev}.py"
    elif [[ "$i" == 2 ]]; then
      # single-quoted style: 10 of the 44 real migration files use it (review F2)
      printf "'''test'''\nrevision: str = '%s'\ndown_revision: str | Sequence[str] | None = '%s'\n" "$rev" "$down" \
        >"$dir/Homayar/backend/migrations/versions/$(printf '%04d' "$i")_${rev}.py"
    else
      printf '"""test"""\nrevision: str = "%s"\ndown_revision: Union[str, None] = "%s"\n' "$rev" "$down" \
        >"$dir/Homayar/backend/migrations/versions/$(printf '%04d' "$i")_${rev}.py"
    fi
    down="$rev"
  done
}

write_build_args() { # write_build_args <file> <sha> [extra line]
  {
    printf 'SOURCE_SHA=%s\n' "$2"
    printf 'SOURCE_DATE_EPOCH=1794264000\n'
    printf 'SOURCE_DATE=2026-11-09T00:00:00Z\n'
    printf 'RELEASE_VERSION=0.5.0\n'
    printf 'RELEASE_PROFILE=standard\n'
    printf 'INSTALL_AI_LOCAL=false\n'
    if [[ -n "${3:-}" ]]; then printf '%s\n' "$3"; fi
  } >"$1"
}

add_image() { # add_image <tag> <id> <label> <bi_sha> <bi_head> <has_index>
  printf '%s|%s|%s|%s|%s|%s\n' "$1" "$2" "$3" "$4" "$5" "$6" >>"$state/images"
}

write_id_normal() {
  cat >"$bin/id" <<'STUB_ID'
#!/usr/bin/env bash
# id stub: the selftest never runs as root; pretend to be gpu (uid 1000).
case "${1:-} ${2:-}" in
  "-u n") echo gpu ;;
  "-un ") echo gpu ;;
  "-nu ") echo gpu ;;
  "-n ") echo gpu ;;
  "-u ") echo 1000 ;;
  *) echo "uid=1000(gpu) gid=1000(gpu)" ;;
esac
exit 0
STUB_ID
  chmod +x "$bin/id"
}

prepare() { # prepare: a fresh fake home and stub state (the "old" world)
  write_id_normal
  rm -rf -- "$HOME_F" "$state"
  mkdir -p "$state" "$bin" "$HOME_F"/config "$HOME_F"/secrets "$HOME_F"/ops \
    "$HOME_F"/backups "$HOME_F"/releases
  make_release "$HOME_F/releases/$S12" "$SHA_S" "$BASE_REV"
  make_release "$HOME_F/releases/$T12" "$SHA_T" "$BASE_REV" "${NEW_REVS[@]}" "$HEAD_REV"
  write_build_args "$HOME_F/releases/$T12/build-args.txt" "$SHA_T"
  ln -sfn "releases/$S12" "$HOME_F/current"
  cat >"$HOME_F/config/settings.env" <<'SETTINGS'
APP_ENVIRONMENT=production
PUBLIC_ORIGIN=https://app.homayar.com
CORS_ORIGINS=https://app.homayar.com
TRUSTED_HOSTS=app.homayar.com,127.0.0.1
SECURE_COOKIES=true
BIND_ADDRESS=127.0.0.1
HTTP_PORT=8080
POSTGRES_DB=homayar
POSTGRES_USER=homayar
DATABASE_APP_ROLE=
LOG_LEVEL=INFO
ENABLE_FREE_DEMO=false
SETTINGS
  cat >"$HOME_F/secrets/secrets.env" <<SECRETS
POSTGRES_PASSWORD=${MARKER}aaaa
MIGRATION_DATABASE_URL=postgresql://homayar:${MARKER}aaaa@postgres:5432/homayar
DATABASE_URL=postgresql+psycopg://homayar_api:${MARKER}bbbb@postgres:5432/homayar
REDIS_PASSWORD=${MARKER}cccc
REDIS_URL=redis://:${MARKER}cccc@redis:6379/0
JWT_SECRET=${MARKER}dddd111111111111111111111111111
TOKEN_PEPPER=${MARKER}eeee111111111111111111111111
MINIO_ROOT_USER=${MARKER}miniouser
MINIO_ROOT_PASSWORD=${MARKER}miniopass
STORAGE_ACCESS_KEY=${MARKER}appkey
STORAGE_SECRET_KEY=${MARKER}appsecret
SECRETS
  printf '%s\n' "${MARKER}miniouser" >"$HOME_F/secrets/minio_root_user"
  printf '%s\n' "${MARKER}miniopass" >"$HOME_F/secrets/minio_root_password"
  printf '%s\n' "${MARKER}appkey" >"$HOME_F/secrets/storage_access_key"
  printf '%s\n' "${MARKER}appsecret" >"$HOME_F/secrets/storage_secret_key"
  printf '%s\n' "$BASE_REV" >"$state/db-version"
  printf '42\n' >"$state/db-tables"
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
  add_image "homayar/api:$S12" "$START_API_ID" "$SHA_S" "$SHA_S" "$BASE_REV" ""
  add_image "homayar/console-web:$S12" "$START_CW_ID" "$SHA_S" "" "" "1"
  cat >"$state/imgs" <<IMGS
api|homayar/api:$S12
worker|homayar/api:$S12
scheduler|homayar/api:$S12
console-web|homayar/console-web:$S12
IMGS
  : >"$state/calls"
}

# --- stubs (id, curl, docker) --------------------------------------------------

cat >"$bin/curl" <<'STUB_CURL'
#!/usr/bin/env bash
# curl stub for the app-server-release selftest. No network: it answers from
# the stub state and handles exactly the option forms the script uses.
set -u
S="${APPREL_STUB_STATE:?}"
H="${APPREL_TEST_HOME:?}"
out=/dev/null; fmt=""; url=""; data=""; ip=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -w) fmt="$2"; shift 2 ;;
    --max-time) shift 2 ;;
    -H)
      case "$2" in
        X-Real-IP:*) ip="${2#X-Real-IP: }" ;;
      esac
      shift 2 ;;
    --data-binary) data="${2#@}"; shift 2 ;;
    -s|-sS|--silent|--show-error) shift ;;
    *) url="$1"; shift ;;
  esac
done

state() {
  local f="$S/$1"
  if [[ -f "$f" ]]; then cat "$f"; else printf '%s' "${2:-}"; fi
}
svc_ok() { [[ -f "$S/svc" ]] && grep -q "^$1|running" "$S/svc"; }
setting() { sed -n "s/^$1=//p" "$H/config/settings.env" | head -n 1; }
reg() {
  local v
  v="$(setting REGISTRATION_ENABLED)"
  [[ -n "$v" ]] || v=true
  printf '%s' "$v"
}

code=000 body="" ctype="text/html" redir="" ssl=0
case "$url" in
  http://127.0.0.1:8080/healthz)
    if svc_ok proxy; then code=200; body='{"component":"proxy","status":"ok"}'; fi ;;
  http://127.0.0.1:8080/readyz)
    if svc_ok api && svc_ok proxy; then
      code=200; ctype=application/json
      body='{"status":"ready","checks":{"database":{"status":"ready"},"redis":{"status":"ready"}}}'
    fi ;;
  http://127.0.0.1:8080/app/)
    if svc_ok console-web && svc_ok proxy; then code=200; body='<!doctype html><html></html>'; fi ;;
  http://127.0.0.1:8080/app)
    if svc_ok console-web && svc_ok proxy; then code=308; redir="http://app.homayar.com:8080/app/"; fi ;;
  http://127.0.0.1:8080/api/v1/auth/methods)
    if svc_ok api; then
      code=200; ctype=application/json
      body='{"password":true,"phone":false,"google":false,"registration":'"$(reg)"'}'
    fi ;;
  http://127.0.0.1:8080/api/v1/auth/register)
    if svc_ok api; then
      if [[ "$(reg)" == false ]]; then
        code=403; ctype=application/json; body='{"code":"registration_closed","message":"closed"}'
      else
        code=201; ctype=application/json; body='{"ok":true}'
      fi
    fi ;;
  http://127.0.0.1:8080/api/v1/auth/login)
    if svc_ok api; then
      code=401; ctype=application/json; body='{"code":"invalid_credentials","message":"Invalid credentials"}'
    fi ;;
  http://127.0.0.1:8080/api/v1/hom358-cap-probe)
    if svc_ok proxy; then
      size=0
      if [[ -n "$data" && -f "$data" ]]; then size="$(wc -c <"$data" | tr -d '[:space:]')"; fi
      if [[ "$size" -gt $((16 * 1024 * 1024)) ]]; then code=413; else code=404; fi
    fi ;;
  http://127.0.0.1:8080/api/v1/*)
    if svc_ok proxy; then
      code=404
      if [[ -n "$ip" ]]; then
        printf '%s %s\n' "${url#http://127.0.0.1:8080}" "$ip" >"$S/last-tagged"
      fi
    fi ;;
  https://app.homayar.com:8080/healthz)
    n=$(( $(state pub-h 0) + 1 )); printf '%s' "$n" >"$S/pub-h"
    if [[ "$n" == 1 ]]; then
      v="$(state public-baseline '200 0')"
    elif [[ -e "$S/public-after-bad" ]]; then
      v="000 60"
    else
      v="$(state public-baseline '200 0')"
    fi
    code="${v%% *}"; ssl="${v#* }"; body='{"component":"proxy"}' ;;
  https://app.homayar.com:8080/app)
    n=$(( $(state pub-app 0) + 1 )); printf '%s' "$n" >"$S/pub-app"
    if [[ "$n" == 1 ]]; then
      v="$(state public-app-baseline '308 https://app.homayar.com:8080/app/')"
    elif [[ -e "$S/public-after-bad" ]]; then
      v="000 "
    else
      v="$(state public-app-baseline '308 https://app.homayar.com:8080/app/')"
    fi
    code="${v%% *}"; redir="${v#* }" ;;
  *) code=000 ;;
esac

if [[ "$out" != /dev/null && -n "$body" ]]; then printf '%s' "$body" >"$out"; fi
if [[ -n "$fmt" ]]; then
  printf '%s' "$fmt" | sed \
    -e 's|%{http_code}|'"$code"'|g' \
    -e 's|%{redirect_url}|'"$redir"'|g' \
    -e 's|%{ssl_verify_result}|'"$ssl"'|g' \
    -e 's|%{content_type}|'"$ctype"'|g'
else
  [[ -n "$body" ]] && printf '%s' "$body"
fi
exit 0
STUB_CURL

cat >"$bin/docker" <<'STUB_DOCKER'
#!/usr/bin/env bash
# docker stub for the app-server-release selftest. It never runs a container:
# every answer comes from the stub state, and every call is recorded in
# $APPREL_STUB_STATE/calls so the cases can check the call order.
set -u
S="${APPREL_STUB_STATE:?}"
CALLS="$S/calls"
log() { printf '%s\n' "$*" >>"$CALLS"; }
state() {
  local f="$S/$1"
  if [[ -f "$f" ]]; then cat "$f"; else printf '%s' "${2:-}"; fi
}
set_state() { printf '%s' "$2" >"$S/$1"; }
img_row() { [[ -f "$S/images" ]] && grep -F "$1|" "$S/images" | head -n 1; }
setting_env=""

head_of_cwd() { # the head revision of the migrations in the current folder
  local f rev down
  : >"$S/.h1"
  : >"$S/.h2"
  for f in Homayar/backend/migrations/versions/*.py; do
    [[ -e "$f" ]] || continue
    rev="$(sed -n 's/^[[:space:]]*revision[[:space:]]*\(:[^=]*\)\{0,1\}[[:space:]]*=[[:space:]]*\(["'"'"']\)\([^"'"'"']*\)\2.*/\3/p' "$f" | head -n 1)"
    down="$(sed -n 's/^[[:space:]]*down_revision[[:space:]]*\(:[^=]*\)\{0,1\}[[:space:]]*=[[:space:]]*\(["'"'"']\)\([^"'"'"']*\)\2.*/\3/p' "$f" | head -n 1)"
    [[ -n "$rev" ]] || continue
    printf '%s\n' "$rev" >>"$S/.h1"
    printf '%s\n' "${down:-ROOT}" >>"$S/.h2"
  done
  sort -o "$S/.h1" "$S/.h1"
  grep -vx ROOT "$S/.h2" | sort -u >"$S/.h3"
  comm -23 "$S/.h1" "$S/.h3"
  rm -f "$S/.h1" "$S/.h2" "$S/.h3"
}

set_svc() { # set_svc <service> <state|health|exit>
  awk -F'|' -v s="$1" -v v="$2" '
    $1 == s { print s "|" v; next }
    { print }
  ' "$S/svc" >"$S/svc.new" 2>/dev/null && mv "$S/svc.new" "$S/svc"
}

imgs_tag() { # the image tag a service runs
  [[ -f "$S/imgs" ]] && grep -F "$1|" "$S/imgs" | head -n 1 | cut -d'|' -f 2
}

cmd="${1:-}"
shift || true
case "$cmd" in

  info)
    log "docker:info"
    echo /var/lib/docker
    exit 0
    ;;

  image) # image inspect --format FMT REF
    [[ "${1:-}" == inspect ]] || { log "docker:image-unknown:$*"; exit 1; }
    shift
    fmt=""; ref=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --format) fmt="$2"; shift 2 ;;
        *) ref="$1"; shift ;;
      esac
    done
    log "docker:image-inspect:$ref"
    row="$(img_row "$ref")"
    if [[ -z "$row" ]]; then echo "cannot find image $ref" >&2; exit 1; fi
    IFS='|' read -r _tag id label _b1 _b2 _b3 <<<"$row"
    if [[ "$fmt" == *'{{.Id}}'* ]]; then echo "$id"
    else echo "$label"; fi
    exit 0
    ;;

  inspect) # inspect --format '{{.Image}}' CID
    fmt=""; ref=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --format) fmt="$2"; shift 2 ;;
        *) ref="$1"; shift ;;
      esac
    done
    log "docker:inspect:$ref"
    svc="${ref#cid-}"
    tag="$(imgs_tag "$svc")"
    row="$(img_row "$tag")"
    if [[ -z "$row" ]]; then echo "cannot find image of $ref" >&2; exit 1; fi
    IFS='|' read -r _tag id _rest <<<"$row"
    echo "$id"
    exit 0
    ;;

  run) # run --rm --network none --entrypoint python|sh TAG args...
    ep=""; tag=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --rm) shift ;;
        --network|--entrypoint)
          [[ "$1" == --entrypoint ]] && ep="$2"
          shift 2 ;;
        *) break ;;
      esac
    done
    tag="$1"; shift
    log "docker:run:$ep:$tag"
    row="$(img_row "$tag")"
    if [[ -z "$row" ]]; then echo "cannot find image $tag" >&2; exit 1; fi
    IFS='|' read -r _tag _id _label bisha bihead hasix <<<"$row"
    if [[ "$ep" == python ]]; then
      # V1: -c <code> <expected sha> <expected head>
      nn=$#
      exp_head="${!nn}"
      mm=$((nn - 1))
      exp_sha="${!mm}"
      if [[ "$bisha" == "$exp_sha" && "$bihead" == "$exp_head" ]]; then
        echo "V1IDENTITY sha=$bisha head=$bihead"
        exit 0
      fi
      echo "V1IDENTITY mismatch (have $bisha / $bihead, want $exp_sha / $exp_head)" >&2
      exit 1
    fi
    if [[ "$ep" == sh && "$*" == *'test -f'* ]]; then
      if [[ "$hasix" == 1 ]]; then exit 0; fi
      exit 1
    fi
    echo "docker run stub: unsupported: $ep $*" >&2
    exit 1
    ;;

  build)
    tag=""; df=""; label=""; basha=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -f) df="$2"; shift 2 ;;
        -t) tag="$2"; shift 2 ;;
        --build-arg)
          case "$2" in
            SOURCE_SHA=*) basha="${2#SOURCE_SHA=}" ;;
          esac
          shift 2 ;;
        --label)
          case "$2" in
            org.opencontainers.image.revision=*) label="${2#*=}" ;;
          esac
          shift 2 ;;
        --memory|--memory-swap|--cpu-period|--cpu-quota) shift 2 ;;
        *) shift ;;
      esac
    done
    log "docker:build:$tag"
    if [[ -e "$S/fail-build" && "$df" == *api.Dockerfile ]]; then
      echo "apt-get failed (injected)" >&2
      echo "build failed (injected)" >&2
      exit 1
    fi
    n=$(( $(state build-n 0) + 1 )); set_state build-n "$n"
    id="sha256:$(printf 'b%063d' "$n")"
    [[ -n "$label" ]] || label="$basha"
    head=""
    [[ "$df" == *api.Dockerfile ]] && head="$(head_of_cwd)"
    hasix=0
    [[ "$df" == *console-web.Dockerfile ]] && hasix=1
    printf '%s|%s|%s|%s|%s|%s\n' "$tag" "$id" "$label" "$basha" "$head" "$hasix" >>"$S/images"
    echo "Successfully built $id"
    echo "Successfully tagged $tag"
    exit 0
    ;;

  compose)
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --env-file)
          [[ -z "$setting_env" ]] && setting_env="$2"
          shift 2 ;;
        --file|--profile) shift 2 ;;
        *) break ;;
      esac
    done
    sub="${1:-}"; shift || true
    case "$sub" in

      ps)
        q=0
        svc=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --all) shift ;;
            --format) shift 2 ;;
            -q) q=1; shift ;;
            *) svc="$1"; shift ;;
          esac
        done
        log "compose:ps"
        if [[ "$q" == 1 ]]; then echo "cid-${svc:-all}"; exit 0; fi
        [[ -f "$S/svc" ]] && cat "$S/svc"
        exit 0
        ;;

      config)
        log "compose:config"
        exit 0
        ;;

      exec)
        while [[ "${1:-}" == -* ]]; do shift; done
        svc="$1"; shift
        if [[ "${1:-}" == sh && "${2:-}" == -c ]]; then
          payload="$3"
          # F1 guard: a psql call without -U (or without -d for the app
          # database) would connect as role root on the real server and fail.
          # The stub refuses it, so a regression cannot pass the selftest.
          if [[ "$payload" == *psql* ]] &&
            { [[ "$payload" != *'-U "$POSTGRES_USER"'* ]] || [[ "$payload" != *'-d '* ]]; }; then
            log "compose:exec:$svc:psql-rejected"
            echo "psql stub: -U and -d are required (got: $payload)" >&2
            exit 1
          fi
          case "$payload" in
            *observability.release*)
              # C4: sh -c payload <code> <expected sha> <expected head>
              log "compose:exec:$svc:release-show"
              nn=$#
              exp_head="${!nn}"
              mm=$((nn - 1))
              exp_sha="${!mm}"
              tag="$(imgs_tag "$svc")"
              row="$(img_row "$tag")"
              IFS='|' read -r _t _id _label bisha bihead _ix <<<"$row"
              if [[ "$bisha" == "$exp_sha" && "$bihead" == "$exp_head" ]]; then
                echo "C4IDENTITY sha=$bisha head=$bihead"
                exit 0
              fi
              echo "C4IDENTITY mismatch (have $bisha / $bihead, want $exp_sha / $exp_head)" >&2
              exit 1
              ;;
            *WEBHOOK_SECRET_KEYS*)
              log "compose:exec:$svc:webhook-test"
              exit 0
              ;;
            *alembic_version*)
              log "compose:exec:$svc:psql"
              if [[ "$payload" == *homayar_restorecheck* ]]; then
                echo "$(state scratch-version)"
              else
                echo "$(state db-version)"
              fi
              exit 0
              ;;
            *'count(*)'*)
              log "compose:exec:$svc:psql"
              if [[ "$payload" == *homayar_restorecheck* ]]; then
                echo "$(state scratch-tables 0)"
              else
                echo "$(state db-tables)"
              fi
              exit 0
              ;;
            *pg_dump*)
              log "compose:exec:$svc:pg_dump"
              echo "$(state db-version)" >"$S/dump-version"
              if [[ -e "$S/backup-mismatch" ]]; then
                echo $(( $(state db-tables) + 1 )) >"$S/dump-tables"
              else
                echo "$(state db-tables)" >"$S/dump-tables"
              fi
              echo "PGDMP-fake-dump-content"
              exit 0
              ;;
            *'pg_restore --list'*)
              log "compose:exec:$svc:pg_restore-list"
              cat >/dev/null
              echo "; fake archive listing"
              echo "215; 1259 16401 TABLE public alembic_version homayar"
              echo "321; 1259 16401 TABLE DATA public alembic_version homayar"
              exit 0
              ;;
            *pg_restore*homayar_restorecheck*)
              log "compose:exec:$svc:pg_restore-scratch"
              cat >/dev/null
              exit 0
              ;;
            *homayar_restorecheck*createdb*)
              log "compose:exec:$svc:scratch-create"
              echo "$(state dump-version)" >"$S/scratch-version"
              echo "$(state dump-tables)" >"$S/scratch-tables"
              exit 0
              ;;
            *dropdb*homayar_restorecheck*)
              log "compose:exec:$svc:scratch-drop"
              rm -f "$S/scratch-version" "$S/scratch-tables"
              exit 0
              ;;
            *dropdb*)
              log "compose:exec:$svc:db-reset"
              exit 0
              ;;
            *pg_restore*)
              log "compose:exec:$svc:pg_restore-live"
              if [[ -e "$S/fail-live-restore" ]]; then
                echo "pg_restore failed (injected)" >&2
                exit 1
              fi
              cat >/dev/null
              echo "$(state dump-version)" >"$S/db-version"
              exit 0
              ;;
            *)
              log "compose:exec:$svc:sh-unknown"
              echo "exec sh stub: unsupported payload: $payload" >&2
              exit 1
              ;;
          esac
        fi
        case "$*" in
          printenv*)
            name="${!#}"
            log "compose:exec:$svc:printenv:$name"
            val=""
            case "$name" in
              STORAGE_PRESIGNED_DELIVERY) val=false ;;
              REGISTRATION_ENABLED)
                if [[ -e "$S/fail-c5" ]]; then val=true; else
                  val="$(sed -n 's/^REGISTRATION_ENABLED=//p' "$setting_env" | head -n 1)"
                  [[ -n "$val" ]] || val=true
                fi
                ;;
              PUBLIC_URL) val="$(sed -n 's/^PUBLIC_ORIGIN=//p' "$setting_env" | head -n 1)" ;;
              ASR_PROFILE|WEBHOOK_SECRET_KEYS)
                val="$(sed -n "s/^$name=//p" "$setting_env" | head -n 1)"
                ;;
            esac
            if [[ -z "$val" ]]; then exit 1; fi
            echo "$val"
            exit 0
            ;;
          *)
            log "compose:exec:$svc:unknown"
            echo "exec stub: unsupported: $*" >&2
            exit 1
            ;;
        esac
        ;;

      stop)
        log "compose:stop"
        n=$(( $(state stop-count 0) + 1 )); set_state stop-count "$n"
        if [[ -e "$S/fail-stop-2" && "$n" == 2 ]]; then
          echo "stop failed (injected)" >&2
          exit 1
        fi
        for s in "$@"; do set_svc "$s" "stopped||"; done
        exit 0
        ;;

      up)
        log "compose:up"
        n=$(( $(state up-count 0) + 1 )); set_state up-count "$n"
        if [[ -e "$S/fail-up-2" && "$n" == 2 ]]; then
          echo "up failed (injected)" >&2
          exit 1
        fi
        s12="$(head -c 12 SOURCE_SHA)"
        api_tag="homayar/api:$s12"
        cw_tag="homayar/console-web:$s12"
        { [[ -n "$(img_row "$api_tag")" && -n "$(img_row "$cw_tag")" ]]; } || {
          echo "an image for $s12 is missing" >&2
          exit 1
        }
        for s in proxy console-web api worker postgres redis minio; do set_svc "$s" "running|healthy|"; done
        set_svc scheduler "running||"
        set_svc minio-bootstrap "exited||0"
        cat >"$S/imgs" <<IMGSUP
api|$api_tag
worker|$api_tag
scheduler|$api_tag
console-web|$cw_tag
IMGSUP
        exit 0
        ;;

      run)
        while [[ "${1:-}" == -* ]]; do shift; done
        rsvc="$1"; shift
        words="$*"
        if [[ "$rsvc" == migrate ]]; then
          log "compose:run:migrate"
          if [[ -e "$S/fail-migrate" ]]; then
            echo "migrate failed (injected)" >&2
            exit 1
          fi
          set_state db-version "$(head_of_cwd)"
          exit 0
        fi
        case "$words" in
          *trend_judgment_store*)
            log "compose:run:api:trend_judgment_store"
            echo '{"workspaces": 1, "judgments_written": 3, "judgments_skipped": 0}'
            exit 0
            ;;
          *model_preflight*)
            log "compose:run:api:model_preflight"
            echo "host: gpu_driver=skip asr_runtime=fail disk=ok postgres=ok redis=ok"
            echo "intelligence_provider_unconfigured" >&2
            exit 2
            ;;
          *)
            log "compose:run:$rsvc:unknown"
            echo "run stub: unsupported: $words" >&2
            exit 1
            ;;
        esac
        ;;

      logs)
        log "compose:logs"
        if [[ -f "$S/last-tagged" ]]; then
          read -r lpath lip <"$S/last-tagged"
          printf '{"uri":"%s","remote_addr":"%s"}\n' "$lpath" "$lip"
        fi
        exit 0
        ;;

      *)
        log "compose:unknown:$sub"
        echo "compose stub: unsupported: $sub $*" >&2
        exit 1
        ;;
    esac
    ;;

  *)
    log "docker:unknown:$cmd"
    echo "docker stub: unsupported: $cmd $*" >&2
    exit 1
    ;;
esac
STUB_DOCKER

chmod +x "$bin/curl" "$bin/docker"

# --- case helpers --------------------------------------------------------------

run_script() { # run_script <script args...>: sets OUT and RC, and appends the
  # run's output to the per-case file so run_case can check the secret marker
  # even though the case functions run in their own subshells (review N1)
  RC=0
  OUT="$(bash "$subject" "$@" </dev/null 2>&1)" || RC=$?
  printf '%s\n' "$OUT" >>"$CASE_OUT"
}

want_rc() {
  [[ "$RC" == "$1" ]] || { detail "exit $RC, want $1"; exit 1; }
}
want_out() {
  grep -q -- "$1" <<<"$OUT" || { detail "output lacks: $1"; exit 1; }
}
want_no_out() {
  ! grep -q -- "$1" <<<"$OUT" || { detail "output must not contain: $1"; exit 1; }
}
want_no_secret() {
  if grep -qF "$MARKER" <<<"$OUT"; then detail "secret marker in the output"; exit 1; fi
  local f
  for f in "$HOME_F"/ops/*.log; do
    [[ -e "$f" ]] || continue
    if grep -qF "$MARKER" "$f"; then detail "secret marker in $f"; exit 1; fi
  done
}
calls_has() {
  grep -qF -- "$1" "$state/calls" || { detail "calls lack: $1"; exit 1; }
}
calls_lack() {
  ! grep -qF -- "$1" "$state/calls" || { detail "calls must not contain: $1"; exit 1; }
}
calls_before() { # calls_before <line-part a> <line-part b>
  local a b
  a="$(grep -nF -- "$1" "$state/calls" | head -n 1 | cut -d: -f1)"
  b="$(grep -nF -- "$2" "$state/calls" | head -n 1 | cut -d: -f1)"
  { [[ -n "$a" && -n "$b" && "$a" -lt "$b" ]]; } || {
    detail "call '$1' must come before '$2'"
    exit 1
  }
}
file_has() {
  grep -q -- "$2" "$1" || { detail "$1 lacks: $2"; exit 1; }
}
db_is() {
  [[ "$(cat "$state/db-version")" == "$1" ]] || {
    detail "db-version is $(cat "$state/db-version"), want $1"
    exit 1
  }
}
want_up_count() { # how many compose up calls ran (B8 is 1, a rollback R5 adds 1)
  local got
  got="$(cat "$state/up-count" 2>/dev/null || printf 0)"
  [[ "$got" == "$1" ]] || {
    detail "up-count is $got, want $1"
    exit 1
  }
}
current_is() {
  local got
  got="$(readlink -f "$HOME_F/current")"
  [[ "$got" == "$HOME_F/releases/$1" ]] || {
    detail "current points to $got, want $HOME_F/releases/$1"
    exit 1
  }
}

passed=0
failed=0
run_case() { # run_case <case function>: one PASS or FAIL line per case.
  # The secret-marker check runs in the parent, over the real outputs that
  # run_script collected for every run of the case (SPEC 6, review N1), and
  # over the case's script log files.
  local name="${1#c_}" why="" f
  : >"$CASE_OUT"
  if "$1"; then :; else why="case failed"; fi
  if grep -qF "$MARKER" "$CASE_OUT"; then
    why="secret marker in a run's output"
  fi
  for f in "$HOME_F"/ops/*.log; do
    [[ -e "$f" ]] || continue
    if grep -qF "$MARKER" "$f"; then why="secret marker in $f"; fi
  done
  if [[ -z "$why" ]]; then
    passed=$((passed + 1))
    printf 'PASS %s\n' "$name"
  else
    failed=$((failed + 1))
    printf 'FAIL %s (%s)\n' "$name" "$why"
  fi
}

# --- cases ---------------------------------------------------------------------

c_usage_no_mode() (
  prepare
  run_script
  want_rc 2
  want_out "usage"
  want_no_secret
)

c_usage_no_mode_2() (
  prepare
  run_script --release "$SHA_T"
  want_rc 2
  want_out "usage"
)

c_usage_unknown_flag() (
  prepare
  run_script --check --release "$SHA_T" --turbo
  want_rc 2
  want_out "usage"
)

c_usage_release_missing_value() (
  prepare
  run_script --check --release
  want_rc 2
  want_out "usage"
)

c_usage_bad_sha() (
  prepare
  run_script --check --release "a651110b03c3"
  want_rc 2
  want_out "usage"
)

c_root_refused() (
  prepare
  cat >"$bin/id" <<'ROOT_ID'
#!/usr/bin/env bash
if [[ "${1:-}" == -u && "${2:-}" != n ]]; then echo 0; exit 0; fi
if [[ "${1:-}" == -u && "${2:-}" == n ]]; then echo root; exit 0; fi
echo "uid=0(root) gid=0(root)"
exit 0
ROOT_ID
  chmod +x "$bin/id"
  run_script --check --release "$SHA_T"
  want_rc 2
  want_out "root"
)

c_lock_held() (
  prepare
  flock "$HOME_F/ops/app-server-release.lock" sleep 10 &
  local holder=$!
  sleep 0.5
  run_script --check --release "$SHA_T"
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
  want_rc 2
  want_out "another"
  want_no_secret
)

c_old_lock_held() (
  prepare
  flock "$HOME_F/ops/hom349.lock" sleep 10 &
  local holder=$!
  sleep 0.5
  run_script --check --release "$SHA_T"
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
  want_rc 2
  want_out "hom349.lock"
)

c_target_folder_missing() (
  prepare
  rm -rf -- "$HOME_F/releases/$T12"
  run_script --check --release "$SHA_T"
  want_rc 2
  want_out "Nothing was changed"
  calls_lack "compose:stop"
)

c_target_sha_mismatch() (
  prepare
  printf '%s\n' "ffffffffffffffffffffffffffffffffffffffff" >"$HOME_F/releases/$T12/SOURCE_SHA"
  run_script --check --release "$SHA_T"
  want_rc 2
  want_out "SOURCE_SHA"
)

c_build_args_extra_line() (
  prepare
  printf 'EXTRA=1\n' >>"$HOME_F/releases/$T12/build-args.txt"
  run_script --check --release "$SHA_T"
  want_rc 2
  want_out "build-args.txt"
)

c_build_args_ai_local() (
  prepare
  sed -i 's/^INSTALL_AI_LOCAL=false$/INSTALL_AI_LOCAL=true/' "$HOME_F/releases/$T12/build-args.txt"
  run_script --check --release "$SHA_T"
  want_rc 2
  want_out "INSTALL_AI_LOCAL"
)

c_db_version_unknown() (
  prepare
  printf 'deadbeef1234\n' >"$state/db-version"
  run_script --check --release "$SHA_T"
  want_rc 2
  want_out "not in the target"
)

c_minio_key_colon() (
  prepare
  printf '%s\n' "${MARKER}bad:key" >"$HOME_F/secrets/minio_root_password"
  run_script --check --release "$SHA_T"
  want_rc 2
  want_out "minio_root_password"
  want_no_out "$MARKER"
  want_no_secret
)

c_s2_old_old() (
  prepare
  run_script --check --release "$SHA_T"
  want_rc 0
  want_out "PUBLIC_ORIGIN: https://app.homayar.com -> https://app.homayar.com:8080"
  want_out "CORS_ORIGINS: https://app.homayar.com -> https://app.homayar.com:8080"
  want_out "REGISTRATION_ENABLED: absent -> false"
  want_no_secret
)

c_s2_new_new() (
  prepare
  sed -i -e 's|^PUBLIC_ORIGIN=.*|PUBLIC_ORIGIN=https://app.homayar.com:8080|' \
    -e 's|^CORS_ORIGINS=.*|CORS_ORIGINS=https://app.homayar.com:8080|' \
    -e '$a REGISTRATION_ENABLED=false' "$HOME_F/config/settings.env"
  run_script --check --release "$SHA_T"
  want_rc 0
  want_out "PUBLIC_ORIGIN: unchanged"
  want_out "REGISTRATION_ENABLED: unchanged"
)

c_s2_mixed() (
  prepare
  sed -i 's|^PUBLIC_ORIGIN=.*|PUBLIC_ORIGIN=https://app.homayar.com:8080|' "$HOME_F/config/settings.env"
  run_script --check --release "$SHA_T"
  want_rc 2
  want_out "PUBLIC_ORIGIN"
)

c_check_changes_nothing() (
  prepare
  local before_tree before_sums after_tree after_sums diff extra gone changed
  before_tree="$(cd "$HOME_F" && find . | LC_ALL=C sort)"
  before_sums="$(cd "$HOME_F" && find . -type f -exec sha256sum {} + | LC_ALL=C sort)"
  run_script --check --release "$SHA_T"
  want_rc 0
  after_tree="$(cd "$HOME_F" && find . | LC_ALL=C sort)"
  after_sums="$(cd "$HOME_F" && find . -type f -exec sha256sum {} + | LC_ALL=C sort)"
  diff="$(diff <(printf '%s\n' "$before_tree") <(printf '%s\n' "$after_tree") || true)"
  extra="$(printf '%s\n' "$diff" | sed -n 's/^> //p' | grep -vE '^\./ops/(release-[^/]*\.log|app-server-release\.lock)$' || true)"
  gone="$(printf '%s\n' "$diff" | sed -n 's/^< //p' || true)"
  changed="$(diff <(printf '%s\n' "$before_sums") <(printf '%s\n' "$after_sums") | grep -E '^[<>]' | grep -vE 'ops/(release-[^/]*\.log|app-server-release\.lock)' || true)"
  [[ -z "$extra" ]] || { detail "unexpected new paths: $extra"; exit 1; }
  [[ -z "$gone" ]] || { detail "paths removed: $gone"; exit 1; }
  [[ -z "$changed" ]] || { detail "files changed: $changed"; exit 1; }
  calls_lack "docker:build"
  calls_lack "compose:stop"
  calls_lack "compose:up"
  calls_lack "pg_dump"
  calls_lack "compose:run:migrate"
  want_no_secret
)

c_apply_happy() (
  prepare
  run_script --apply --release "$SHA_T"
  want_rc 0
  want_out "backfill counts"
  want_out "HOM-340"
  # the order the SPEC asks for
  calls_has "docker:build:homayar/api:$T12"
  calls_has "docker:build:homayar/console-web:$T12"
  calls_before "docker:build:homayar/console-web:$T12" "compose:stop"
  calls_before "compose:stop" "pg_dump"
  calls_before "pg_dump" "pg_restore-list"
  calls_before "pg_restore-list" "compose:run:migrate"
  calls_before "compose:run:migrate" "compose:run:api:trend_judgment_store"
  calls_before "compose:run:api:trend_judgment_store" "compose:up"
  # the settings file and current after the run
  file_has "$HOME_F/config/settings.env" '^PUBLIC_ORIGIN=https://app.homayar.com:8080$'
  file_has "$HOME_F/config/settings.env" '^CORS_ORIGINS=https://app.homayar.com:8080$'
  file_has "$HOME_F/config/settings.env" '^REGISTRATION_ENABLED=false$'
  file_has "$HOME_F/config/settings.env" '^ENABLE_FREE_DEMO=false$'
  file_has "$HOME_F/config/settings.env" '^TRUSTED_HOSTS=app.homayar.com,127.0.0.1$'
  current_is "$T12"
  db_is "$HEAD_REV"
  # the status file and the backup folder
  file_has "$HOME_F/ops/release-$T12-status" '^EXIT 0 '
  local bfound=""
  local bdir
  for bdir in "$HOME_F"/backups/release-"$T12"-*; do
    [[ -d "$bdir" ]] && bfound=1
  done
  [[ -n "$bfound" ]] || { detail "no backup folder for $T12"; exit 1; }
  want_no_secret
)

c_apply_backup_check_failure() (
  prepare
  touch "$state/backup-mismatch"
  run_script --apply --release "$SHA_T"
  want_rc 1
  calls_has "pg_restore-list"
  calls_lack "compose:run:migrate"
  calls_lack "compose:run:api:trend_judgment_store"
  # nothing moved: the settings file and current are unchanged
  file_has "$HOME_F/config/settings.env" '^PUBLIC_ORIGIN=https://app.homayar.com$'
  grep -q '^REGISTRATION_ENABLED=' "$HOME_F/config/settings.env" && {
    detail "REGISTRATION_ENABLED was written"
    exit 1
  }
  current_is "$S12"
  db_is "$BASE_REV"
  # the writers were restarted on the start release
  calls_has "compose:up"
  want_no_secret
)

c_apply_migrate_failure() (
  prepare
  touch "$state/fail-migrate"
  run_script --apply --release "$SHA_T"
  want_rc 1
  calls_has "pg_restore-live"
  file_has "$HOME_F/config/settings.env" '^PUBLIC_ORIGIN=https://app.homayar.com$'
  current_is "$S12"
  db_is "$BASE_REV"
  # the R4 relink writes a relative link, like B5 (review N5)
  [[ "$(readlink "$HOME_F/current")" == "releases/$S12" ]] || {
    detail "current link is $(readlink "$HOME_F/current"), want releases/$S12"
    exit 1
  }
  want_no_secret
)

c_apply_after_check_failure() (
  prepare
  touch "$state/fail-c5"
  run_script --apply --release "$SHA_T"
  want_rc 1
  calls_has "pg_restore-live"
  current_is "$S12"
  db_is "$BASE_REV"
  # B8 plus the rollback R5 (R2 succeeded, so R5 ran)
  want_up_count 2
  want_no_secret
)

c_rollback_failure() (
  prepare
  touch "$state/fail-c5" "$state/fail-up-2"
  run_script --apply --release "$SHA_T"
  want_rc 3
  want_out "by hand"
  want_no_secret
)

c_public_check_worse() (
  prepare
  touch "$state/public-after-bad"
  run_script --apply --release "$SHA_T"
  want_rc 4
  current_is "$T12"
  db_is "$HEAD_REV"
  want_no_secret
)

c_already_done() (
  prepare
  ln -sfn "releases/$T12" "$HOME_F/current"
  printf '%s\n' "$HEAD_REV" >"$state/db-version"
  sed -i -e 's|^PUBLIC_ORIGIN=.*|PUBLIC_ORIGIN=https://app.homayar.com:8080|' \
    -e 's|^CORS_ORIGINS=.*|CORS_ORIGINS=https://app.homayar.com:8080|' \
    -e '$a REGISTRATION_ENABLED=false' "$HOME_F/config/settings.env"
  add_image "homayar/api:$T12" "sha256:3333333333333333333333333333333333333333333333333333333333333333" \
    "$SHA_T" "$SHA_T" "$HEAD_REV" ""
  add_image "homayar/console-web:$T12" "sha256:4444444444444444444444444444444444444444444444444444444444444444" \
    "$SHA_T" "" "" "1"
  cat >"$state/imgs" <<IMGSAD
api|homayar/api:$T12
worker|homayar/api:$T12
scheduler|homayar/api:$T12
console-web|homayar/console-web:$T12
IMGSAD
  run_script --apply --release "$SHA_T"
  want_rc 0
  want_out "Already done"
  calls_lack "compose:stop"
  calls_lack "compose:run:migrate"
  calls_lack "docker:build"
  want_no_secret
)

c_side_image_missing() (
  prepare
  run_script --check --use-side-image --release "$SHA_T"
  want_rc 2
  want_out "side image"
)

c_build_failed() (
  prepare
  touch "$state/fail-build"
  run_script --apply --release "$SHA_T"
  want_rc 2
  want_out "Nothing live changed"
  want_out "release-images.yml"
  want_out "docker load"
  calls_lack "compose:stop"
  calls_lack "pg_dump"
  calls_lack "compose:run:migrate"
  file_has "$HOME_F/config/settings.env" '^PUBLIC_ORIGIN=https://app.homayar.com$'
  current_is "$S12"
  db_is "$BASE_REV"
  want_no_secret
)

c_p12_bad_present_images() (
  prepare
  # both target images present, but the api one fails V1 (wrong build-info sha)
  add_image "homayar/api:$T12" "sha256:5555555555555555555555555555555555555555555555555555555555555555" \
    "$SHA_T" "ffffffffffffffffffffffffffffffffffffffff" "$HEAD_REV" ""
  add_image "homayar/console-web:$T12" "sha256:6666666666666666666666666666666666666666666666666666666666666666" \
    "$SHA_T" "" "" "1"
  run_script --check --release "$SHA_T"
  want_rc 0
  want_out "rebuild is planned"
  # with the side-image flag the same state must refuse
  run_script --check --use-side-image --release "$SHA_T"
  want_rc 2
  want_out "V1/V2"
)

c_p12_same_release_bad_images() (
  prepare
  # current already points to the target and its images fail V1/V2:
  # a rebuild would re-tag the live image, so the script must refuse
  ln -sfn "releases/$T12" "$HOME_F/current"
  printf '%s\n' "$HEAD_REV" >"$state/db-version"
  add_image "homayar/api:$T12" "sha256:7777777777777777777777777777777777777777777777777777777777777777" \
    "$SHA_T" "ffffffffffffffffffffffffffffffffffffffff" "$HEAD_REV" ""
  add_image "homayar/console-web:$T12" "sha256:8888888888888888888888888888888888888888888888888888888888888888" \
    "$SHA_T" "" "" "1"
  run_script --check --release "$SHA_T"
  want_rc 2
  want_out "equals the target"
  calls_lack "docker:build"
  calls_lack "compose:stop"
)

c_rollback_step_failure() (
  prepare
  # the after-checks fail (fail-c5) and the database restore in R2 fails:
  # R3 and R4 still run, R5 is skipped (Addendum A3), the exit is 3
  touch "$state/fail-c5" "$state/fail-live-restore"
  run_script --apply --release "$SHA_T"
  want_rc 3
  want_out "by hand"
  want_out "R5 SKIPPED"
  calls_has "compose:stop"
  calls_has "pg_restore-live"
  want_up_count 1 # only the B8 up: R5 was skipped because R2 failed
  file_has "$HOME_F/config/settings.env" '^PUBLIC_ORIGIN=https://app.homayar.com$'
  current_is "$S12"
)

c_rollback_r1_fails() (
  prepare
  # the after-checks fail and the rollback stop (the 2nd stop) fails:
  # R2 and R5 are skipped, R3 and R4 still run, the exit is 3
  touch "$state/fail-c5" "$state/fail-stop-2"
  run_script --apply --release "$SHA_T"
  want_rc 3
  want_out "R2 SKIPPED"
  want_out "R5 SKIPPED"
  want_out "by hand"
  file_has "$HOME_F/config/settings.env" '^PUBLIC_ORIGIN=https://app.homayar.com$'
  current_is "$S12"
  want_up_count 1 # only the B8 up: R5 was skipped because R1 failed
  # the restore never ran, so the database is still at the target head
  db_is "$HEAD_REV"
)

# --- run all cases -------------------------------------------------------------

run_case c_usage_no_mode
run_case c_usage_no_mode_2
run_case c_usage_unknown_flag
run_case c_usage_release_missing_value
run_case c_usage_bad_sha
run_case c_root_refused
run_case c_lock_held
run_case c_old_lock_held
run_case c_target_folder_missing
run_case c_target_sha_mismatch
run_case c_build_args_extra_line
run_case c_build_args_ai_local
run_case c_db_version_unknown
run_case c_minio_key_colon
run_case c_s2_old_old
run_case c_s2_new_new
run_case c_s2_mixed
run_case c_check_changes_nothing
run_case c_apply_happy
run_case c_apply_backup_check_failure
run_case c_apply_migrate_failure
run_case c_apply_after_check_failure
run_case c_rollback_failure
run_case c_public_check_worse
run_case c_already_done
run_case c_side_image_missing
run_case c_build_failed
run_case c_p12_bad_present_images
run_case c_p12_same_release_bad_images
run_case c_rollback_step_failure
run_case c_rollback_r1_fails

printf '%d passed, %d failed\n' "$passed" "$failed"
[[ "$failed" -eq 0 ]]
