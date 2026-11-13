#!/usr/bin/env bash
# Selftest for infra/scripts/homayar-onprem.sh (HOM-110, HOM-107).
#
# Runs where bash >= 4.4, coreutils, openssl and python3 exist (the CI ubuntu
# runner, or
#   docker run --rm -v "$PWD":/work:ro -w /work python:3.12-slim \
#     bash infra/ci/tests/test-homayar-onprem.sh
# ). It needs no network, no Docker and no root: the docker, curl and git
# commands are stubs first on PATH, and ONPREM_TEST_HOME points the script
# at a fake /opt/homayar built in a temp folder. The fake operator env file
# holds the marker ZzOnpremKrit42; every case asserts the marker never
# reaches the output or the script's log files. Since HOM-107 every fixture
# package is signed with a throwaway Ed25519 key generated at run time, and
# the fake home's config/bundle-keys holds that key's public half (the
# real openssl of the test image signs and verifies for real).
set -Eeuo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
subject="${here}/../../scripts/homayar-onprem.sh"

tmp="$(mktemp -d "${TMPDIR:-/tmp}/onprem-test.XXXXXX")"
trap 'rm -rf -- "${tmp}"' EXIT

bin="$tmp/bin"
state="$tmp/state"
mkdir -p "$bin" "$state"
MARKER="ZzOnpremKrit42"

# The world: version 0.5.0 installed (base revision a7c3e9f5b2d1), the
# upgrade package 0.6.0 (head revision 6d4ec2d2e498, matching its sha12).
OLD_VER="0.5.0"
OLD_SHA="019a906aa9ef2edefc8223d478eeb317e4f55b0c"
NEW_VER="0.6.0"
NEW_SHA="a651110b03c30123456789abcdef0123456789ab"
BASE_REV="a7c3e9f5b2d1"
NEW_REV="6d4ec2d2e498"
OLD_API="homayar/api:0.5.0"
OLD_CW="homayar/console-web:0.5.0"
OLD_EG="homayar/egress-proxy:0.5.0"
NEW_API="homayar/api:0.6.0"
NEW_CW="homayar/console-web:0.6.0"
NEW_EG="homayar/egress-proxy:0.6.0"
OLD_API_ID="sha256:1111111111111111111111111111111111111111111111111111111111111111"
OLD_CW_ID="sha256:2222222222222222222222222222222222222222222222222222222222222222"
OLD_EG_ID="sha256:3333333333333333333333333333333333333333333333333333333333333333"
NEW_API_ID="sha256:4444444444444444444444444444444444444444444444444444444444444444"
NEW_CW_ID="sha256:5555555555555555555555555555555555555555555555555555555555555555"
NEW_EG_ID="sha256:6666666666666666666666666666666666666666666666666666666666666666"

HOME_F="$tmp/home"
CASE_OUT="$tmp/case-out"
export ONPREM_TEST_HOME="$HOME_F"
export HOMAYAR_STUB_STATE="$state"
export NODE_EXPORTER_TEXTFILE_DIR="$HOME_F/gauge"
export PATH="$bin:$PATH"

# HOM-107: every fixture package is signed at run time with throwaway Ed25519
# keys made in the test temp folder (never committed). The public key of the
# main key seeds the trust directory of the fake home; ALT_KEY is the
# rotation-era second key, RSA_KEY proves a wrong algorithm is refused.
KEY="$tmp/keys/bundle-signing-key.pem"
PUB="$tmp/keys/bundle-signing-key.pub.pem"
ALT_KEY="$tmp/keys/bundle-alt-key.pem"
ALT_PUB="$tmp/keys/bundle-alt-key.pub.pem"
RSA_KEY="$tmp/keys/bundle-rsa-key.pem"
mkdir -p "$tmp/keys"
openssl genpkey -algorithm ed25519 -out "$KEY" 2>/dev/null
openssl genpkey -algorithm ed25519 -out "$ALT_KEY" 2>/dev/null
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$RSA_KEY" 2>/dev/null
chmod 600 "$KEY" "$ALT_KEY" "$RSA_KEY"
openssl pkey -in "$KEY" -pubout -out "$PUB" 2>/dev/null
openssl pkey -in "$ALT_KEY" -pubout -out "$ALT_PUB" 2>/dev/null
TRUST="$HOME_F/config/bundle-keys"
key_fingerprint() { # first 16 hex of the sha256 of the public key DER
  openssl pkey -in "$1" -pubout -outform DER 2>/dev/null | sha256sum | cut -c1-16
}
FINGERPRINT="$(key_fingerprint "$KEY")"
ALT_FINGERPRINT="$(key_fingerprint "$ALT_KEY")"

OUT=""
RC=0

detail() { printf '    ! %s\n' "$*"; }

# --- fixtures -------------------------------------------------------------------

write_env() { # the operator env file; the marker values must never leak
  cat >"$HOME_F/config/onprem.env" <<ENV
APP_ENVIRONMENT=production
DEPLOYMENT_MODE=onprem
DEPLOYMENT_TOPOLOGY=single-host-p40
PUBLIC_ORIGIN=https://homayar.customer.example
CORS_ORIGINS=https://homayar.customer.example
TRUSTED_HOSTS=homayar.customer.example
HTTP_PORT=8080
POSTGRES_DB=homayar
POSTGRES_USER=homayar
POSTGRES_PASSWORD=${MARKER}aaaa
REDIS_PASSWORD=${MARKER}bbbb
MINIO_ROOT_USER=${MARKER}miniouser
MINIO_ROOT_PASSWORD=${MARKER}miniopass
DATABASE_URL=postgresql+psycopg://homayar:${MARKER}cccc@postgres:5432/homayar
REDIS_URL=redis://:${MARKER}dddd@redis:6379/0
JWT_SECRET=${MARKER}eeee111111111111111111111111111
TOKEN_PEPPER=${MARKER}ffff111111111111111111111111
STORAGE_ACCESS_KEY=${MARKER}appkey
STORAGE_SECRET_KEY=${MARKER}appsecret
ENV
  chmod 600 "$HOME_F/config/onprem.env"
}

write_gauge() { # write_gauge <age-seconds>: the backup freshness gauge in the
  # exact format backend/backup/metrics.py writes it (three decimals, real
  # target labels)
  local ts
  ts=$(($(date +%s) - "$1"))
  mkdir -p "$NODE_EXPORTER_TEXTFILE_DIR"
  {
    echo '# HELP homayar_backup_last_success_timestamp_seconds Unix time of the last successful Homayar backup per target.'
    echo '# TYPE homayar_backup_last_success_timestamp_seconds gauge'
    printf 'homayar_backup_last_success_timestamp_seconds{target="postgres"} %.3f\n' "$ts"
    printf 'homayar_backup_last_success_timestamp_seconds{target="objects"} %.3f\n' "$ts"
    printf 'homayar_backup_last_success_timestamp_seconds{target="offsite"} %.3f\n' "$ts"
  } >"$NODE_EXPORTER_TEXTFILE_DIR/homayar_backup.prom"
}

make_release_tree() { # make_release_tree <dir>: the infra part of a release
  mkdir -p "$1/infra/compose" "$1/infra/scripts/lib" "$1/infra/nginx"
  printf 'name: homayar-onprem\nservices:\n  api:\n    image: "${API_IMAGE:?}"\n' \
    >"$1/infra/compose/compose.onprem.yaml"
  printf '#!/usr/bin/env bash\necho backup-stub "$@"\n' >"$1/infra/scripts/backup.sh"
  printf '#!/usr/bin/env bash\necho restore-stub "$@"\n' >"$1/infra/scripts/restore-isolated.sh"
  chmod +x "$1/infra/scripts/backup.sh" "$1/infra/scripts/restore-isolated.sh"
  printf 'server_name homayar;\n' >"$1/infra/nginx/homayar.conf"
}

sign_pkg() { # sign_pkg <dir>: sign SHA256SUMS with the throwaway fixture key
  openssl pkeyutl -sign -inkey "$KEY" -rawin -in "$1/SHA256SUMS" \
    -out "$1/SHA256SUMS.sig" 2>/dev/null
}

write_bundle_manifest() { # write_bundle_manifest <dir> <version> <sha> <api> <cw> <eg>
  {
    printf 'schema homayar-offline-bundle/1\n'
    printf 'release %s %s\n' "$2" "$3"
    printf 'image images/api.tar %s %s LicenseRef-Homayar-Proprietary\n' \
      "$(sha256sum "$1/images/api.tar" | awk '{ print $1 }')" "$4"
    printf 'image images/console-web.tar %s %s LicenseRef-Homayar-Proprietary\n' \
      "$(sha256sum "$1/images/console-web.tar" | awk '{ print $1 }')" "$5"
    printf 'image images/egress-proxy.tar %s %s LicenseRef-Homayar-Proprietary\n' \
      "$(sha256sum "$1/images/egress-proxy.tar" | awk '{ print $1 }')" "$6"
    printf 'requires bash >=4.4\n'
    printf 'requires openssl >=3.0\n'
    printf 'requires docker-engine >=27.3.1\n'
    printf 'requires docker-compose-plugin >=2.29.7\n'
    printf 'requires nvidia-container-toolkit gpu-profile-only\n'
  } >"$1/BUNDLE.manifest"
}

make_package() { # make_package <dir> <version> <sha> <api> <cw> <eg> <api-id> <cw-id> <eg-id> <head>
  local dir="$1"
  rm -rf -- "$dir"
  make_release_tree "$dir"
  mkdir -p "$dir/images"
  printf '%s|%s|%s|%s|%s|0\n' "$4" "$7" "$3" "$3" "${10}" >"$dir/images/api.tar"
  printf '%s|%s|%s|%s|%s|0\n' "$5" "$8" "$3" "" "" >"$dir/images/console-web.tar"
  printf '%s|%s|%s|%s|%s|0\n' "$6" "$9" "$3" "" "" >"$dir/images/egress-proxy.tar"
  {
    printf 'HOMAYAR_RELEASE_VERSION=%s\n' "$2"
    printf 'HOMAYAR_RELEASE_SHA=%s\n' "$3"
    printf 'API_IMAGE=%s\n' "$4"
    printf 'CONSOLE_WEB_IMAGE=%s\n' "$5"
    printf 'EGRESS_PROXY_IMAGE=%s\n' "$6"
  } >"$dir/release.env"
  write_bundle_manifest "$dir" "$2" "$3" "$4" "$5" "$6"
  (cd "$dir" && find . -type f ! -path ./SHA256SUMS ! -path ./SHA256SUMS.sig -printf '%P\0' |
    LC_ALL=C sort -z | xargs -0 sha256sum >SHA256SUMS)
  sign_pkg "$dir"
}

write_sums() { # rewrite SHA256SUMS of a package (after tampering) and re-sign
  # it, so the refusal under test is the tampered rule, not the signature
  (cd "$1" && find . -type f ! -path ./SHA256SUMS ! -path ./SHA256SUMS.sig -printf '%P\0' |
    LC_ALL=C sort -z | xargs -0 sha256sum >SHA256SUMS)
  sign_pkg "$1"
}

rewrite_sums_unsigned() { # rewrite SHA256SUMS without re-signing (a broken
  # re-pack: the signature no longer covers the new hash list)
  (cd "$1" && find . -type f ! -path ./SHA256SUMS ! -path ./SHA256SUMS.sig -printf '%P\0' |
    LC_ALL=C sort -z | xargs -0 sha256sum >SHA256SUMS)
}

seed_trust() { # the trust directory of the fake home holds the fixture key
  mkdir -p "$TRUST"
  cp "$PUB" "$TRUST/bundle-key-test.pem"
}

make_checkout() { # make_checkout <dir>: a clean checkout with every part pack
  # reads, including infra/offline/bundle.conf from the repo
  mkdir -p "$1/infra/compose" "$1/infra/scripts" "$1/infra/nginx" "$1/infra/offline"
  cp "$here/../../../infra/offline/bundle.conf" "$1/infra/offline/bundle.conf"
}

add_image() { # add_image <tag> <id> <label> <bisha> <bihead>: seed the stub
  printf '%s|%s|%s|%s|%s|0\n' "$1" "$2" "$3" "$4" "$5" >>"$state/images"
}

set_svc_running() { # every service in its running state
  cat >"$state/svc" <<'SVC'
proxy|running|healthy|0
console-web|running|healthy|0
api|running|healthy|0
worker|running|healthy|0
scheduler|running||0
postgres|running|healthy|0
redis|running|healthy|0
minio|running|healthy|0
minio-bootstrap|exited||0
SVC
  cat >"$state/imgs" <<IMGS
api|homayar/api:0.5.0
worker|homayar/api:0.5.0
scheduler|homayar/api:0.5.0
console-web|homayar/console-web:0.5.0
IMGS
}

prepare() { # a fresh fake home and stub state; 0.5.0 already installed
  rm -rf -- "$HOME_F" "$state"
  mkdir -p "$state" "$bin" "$HOME_F"/{config,ops,state,releases,gauge}
  write_env
  seed_trust
  write_gauge 3600
  make_release_tree "$HOME_F/releases/$OLD_VER"
  {
    printf 'HOMAYAR_RELEASE_VERSION=%s\n' "$OLD_VER"
    printf 'HOMAYAR_RELEASE_SHA=%s\n' "$OLD_SHA"
    printf 'API_IMAGE=%s\n' "$OLD_API"
    printf 'CONSOLE_WEB_IMAGE=%s\n' "$OLD_CW"
    printf 'EGRESS_PROXY_IMAGE=%s\n' "$OLD_EG"
  } >"$HOME_F/releases/$OLD_VER/release.env"
  ln -sfn "releases/$OLD_VER" "$HOME_F/current"
  add_image "$OLD_API" "$OLD_API_ID" "$OLD_SHA" "$OLD_SHA" "$BASE_REV"
  add_image "$OLD_CW" "$OLD_CW_ID" "$OLD_SHA" "" ""
  add_image "$OLD_EG" "$OLD_EG_ID" "$OLD_SHA" "" ""
  printf '%s\n' "$BASE_REV" >"$state/db-version"
  printf '42\n' >"$state/db-tables"
  set_svc_running
  : >"$state/calls"
  make_package "$tmp/pkg-$NEW_VER" "$NEW_VER" "$NEW_SHA" \
    "$NEW_API" "$NEW_CW" "$NEW_EG" "$NEW_API_ID" "$NEW_CW_ID" "$NEW_EG_ID" "$NEW_REV"
  make_package "$tmp/pkg-$OLD_VER" "$OLD_VER" "$OLD_SHA" \
    "$OLD_API" "$OLD_CW" "$OLD_EG" "$OLD_API_ID" "$OLD_CW_ID" "$OLD_EG_ID" "$BASE_REV"
}

prepare_fresh() { # like prepare, but nothing installed yet
  prepare
  rm -f -- "$HOME_F/current"
  rm -rf -- "$HOME_F/releases/$OLD_VER"
  rm -f "$state/images" "$state/svc" "$state/imgs"
  : >"$state/images"
  : >"$state/calls"
}

latest_state_id() {
  local sid best=""
  for sid in "$HOME_F"/state/*; do
    [[ -f "$sid/record.env" ]] || continue
    sid="${sid##*/}"
    if [[ -z "$best" || "$sid" > "$best" ]]; then best="$sid"; fi
  done
  printf '%s' "$best"
}

MODEL_ROOT="$tmp/model-root"
MODEL_MANIFEST="$tmp/demo-model-manifest.json"
write_model_fixture() { # one pinned model file plus its operator manifest
  rm -rf -- "$MODEL_ROOT"
  mkdir -p "$MODEL_ROOT/asr/demo"
  printf 'demo model weights\n' >"$MODEL_ROOT/asr/demo/model.bin"
  local sha
  sha="$(sha256sum "$MODEL_ROOT/asr/demo/model.bin" | awk '{ print $1 }')"
  cat >"$MODEL_MANIFEST" <<M
{
  "schema": "homayar.model-manifests/1",
  "manifests": [
    {
      "manifest_id": "asr.demo-model",
      "license": "MIT",
      "artifacts": [
        {"path": "asr/demo/model.bin", "kind": "file", "sha256": "$sha"}
      ]
    }
  ]
}
M
  printf '%s' "$sha"
}

record_value() { # record_value <state-id> <key>
  sed -n "s/^$2=//p" "$HOME_F/state/$1/record.env" | head -n 1
}

# --- stubs (git, curl, docker) ----------------------------------------------------

cat >"$bin/git" <<'STUB_GIT'
#!/usr/bin/env bash
# git stub for the pack cases: HEAD comes from the stub state, the tree is
# clean unless the git-dirty marker exists.
set -u
S="${HOMAYAR_STUB_STATE:?}"
dir=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -C) dir="$2"; shift 2 ;;
    *) break ;;
  esac
done
case "${1:-}" in
  rev-parse)
    [[ -f "$S/git-sha" ]] && cat "$S/git-sha" || echo "0000000000000000000000000000000000000000"
    ;;
  status)
    if [[ -e "$S/git-dirty" ]]; then echo " M dirty-file"; fi
    ;;
  *)
    echo "git stub: unsupported: $*" >&2
    exit 1
    ;;
esac
exit 0
STUB_GIT

cat >"$bin/curl" <<'STUB_CURL'
#!/usr/bin/env bash
# curl stub: no network, it answers from the stub service rows. Every URL is
# recorded so the cases can check which host:port was really used.
set -u
S="${HOMAYAR_STUB_STATE:?}"
out=/dev/null
url=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -w) shift 2 ;;
    --max-time) shift 2 ;;
    -s|-sS|--silent|--show-error) shift ;;
    *) url="$1"; shift ;;
  esac
done
printf '%s\n' "$url" >>"$S/curls"
svc_ok() { [[ -f "$S/svc" ]] && grep -q "^$1|running" "$S/svc"; }
code=000
case "$url" in
  http://*/healthz)
    if svc_ok proxy; then code=200; printf '{"component":"proxy","status":"ok"}' >"$out"; fi ;;
  http://*/readyz)
    if svc_ok proxy && svc_ok api; then
      code=200
      printf '{"status":"ready","checks":{"database":{"status":"ready"}}}' >"$out"
    fi ;;
esac
printf '%s' "$code"
exit 0
STUB_CURL

cat >"$bin/docker" <<'STUB_DOCKER'
#!/usr/bin/env bash
# docker stub for the homayar-onprem selftest. It never runs a container:
# every answer comes from the stub state, and every call is recorded in
# $HOMAYAR_STUB_STATE/calls so the cases can check the call order.
set -u
S="${HOMAYAR_STUB_STATE:?}"
CALLS="$S/calls"
log() { printf '%s\n' "$*" >>"$CALLS"; }
state() {
  local f="$S/$1"
  if [[ -f "$f" ]]; then cat "$f"; else printf '%s' "${2:-}"; fi
}
set_state() { printf '%s' "$2" >"$S/$1"; }
img_row() { # by tag, else by image ID (docker inspect accepts both)
  [[ -f "$S/images" ]] || return 0
  local row
  row="$(grep -F "$1|" "$S/images" | head -n 1)"
  if [[ -z "$row" ]]; then
    row="$(awk -F'|' -v r="$1" '$2 == r' "$S/images" | head -n 1)"
  fi
  printf '%s' "$row"
}
imgs_tag() { [[ -f "$S/imgs" ]] && grep -F "$1|" "$S/imgs" | head -n 1 | cut -d'|' -f 2; }
rel_env() { sed -n "s/^$1=//p" ./release.env 2>/dev/null | head -n 1; }
set_svc() { # set_svc <service> <state|health>; a fresh install has no row
  # file yet, so a missing row is appended
  if [[ -s "$S/svc" ]] && grep -q "^$1|" "$S/svc"; then
    awk -F'|' -v s="$1" -v v="$2" '
      $1 == s { print s "|" v; next }
      { print }
    ' "$S/svc" >"$S/svc.new" 2>/dev/null && mv "$S/svc.new" "$S/svc"
  else
    printf '%s|%s\n' "$1" "$2" >>"$S/svc"
  fi
}

cmd="${1:-}"
shift || true
case "$cmd" in

  --version)
    echo "Docker version 27.3.1, build afdd78b (stub)"
    exit 0
    ;;

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
    IFS='|' read -r _tag id _label _b1 _b2 _b3 <<<"$row"
    if [[ "$fmt" == *'{{.Id}}'* ]]; then echo "$id"; else echo "$id"; fi
    exit 0
    ;;

  inspect) # inspect --format '{{.Image}}' cid-SVC
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

  load) # load --input FILE: the file holds a fake "tag|id|label|bisha|head|0" row
    f=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --input|-i) f="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    log "docker:load"
    if [[ -e "$S/fail-load" ]]; then
      echo "open $f: no such file or directory (injected load failure)" >&2
      exit 1
    fi
    [[ -n "$f" && -f "$f" ]] || { echo "docker load stub: no input" >&2; exit 1; }
    head -n 1 "$f" >>"$S/images"
    echo "Loaded image from $f"
    exit 0
    ;;

  save) # save --output FILE REF
    f=""; ref=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --output|-o) f="$2"; shift 2 ;;
        *) ref="$1"; shift ;;
      esac
    done
    log "docker:save:$ref"
    n=$(( $(state save-n 0) + 1 )); set_state save-n "$n"
    printf '%s|sha256:%s|%s|0|0|0\n' "$ref" "$(printf 'a%063d' "$n")" "$(printf 'b%040d' "$n")" >"$f"
    exit 0
    ;;

  run) # run --rm --network none [--volume V] [--user U] --entrypoint python TAG args...
    ep=""; tag=""; user=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --rm) shift ;;
        --network|--volume) shift 2 ;;
        --user) user="$2"; shift 2 ;;
        --entrypoint) ep="$2"; shift 2 ;;
        *) break ;;
      esac
    done
    tag="$1"; shift
    if [[ "$*" == *'backend.support_bundle scan'* ]]; then
      log "docker:run:scan:user=${user:-none}:$tag"
      if [[ -e "$S/scan-crash" ]]; then
        echo "scan stub: segmentation fault (injected)" >&2
        exit 137
      fi
      if [[ -e "$S/scan-error" ]]; then
        echo "scan stub: internal error (injected)" >&2
        exit 2
      fi
      if [[ -e "$S/scan-fail" ]]; then
        echo "finding kind=secret_value file=/bundle/host.json"
        echo "finding kind=email file=/bundle/container.json"
        exit 1
      fi
      exit 0
    fi
    # the migration-head call has exactly this shape: entrypoint python, -c
    # code that reads build-info.json for the migration head
    if [[ "$ep" != python || "${1:-}" != -c ||
      "$*" != *build-info.json* || "$*" != *migration_head* ]]; then
      log "docker:run:bad-shape:$ep"
      echo "docker run stub: unsupported python call shape: $ep $*" >&2
      exit 1
    fi
    log "docker:run:python:$tag"
    row="$(img_row "$tag")"
    if [[ -z "$row" ]]; then echo "cannot find image $tag" >&2; exit 1; fi
    IFS='|' read -r _tag _id _label _bisha bihead _ix <<<"$row"
    echo "$bihead"
    exit 0
    ;;

  compose)
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --env-file|--file|--profile) shift 2 ;;
        *) break ;;
      esac
    done
    sub="${1:-}"; shift || true
    case "$sub" in

      version)
        log "compose:version"
        echo "Docker Compose version v2.29.7 (stub)"
        exit 0
        ;;

      config)
        log "compose:config"
        exit 0
        ;;

      ps)
        q=0; svc=""; fmt=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --all) shift ;;
            --format) fmt="$2"; shift 2 ;;
            -q) q=1; shift ;;
            *) svc="$1"; shift ;;
          esac
        done
        log "compose:ps"
        if [[ "$q" == 1 ]]; then echo "cid-${svc:-all}"; exit 0; fi
        [[ -f "$S/svc" ]] || exit 0
        if [[ -z "$fmt" ]]; then cat "$S/svc"; exit 0; fi
        # render the template like real compose, and fail on any field
        # compose does not have (the same class of error the real one prints)
        allowed=" Service Name State Health ExitCode Image ID Status "
        for field in $(grep -oE '\{\{[^}]+\}\}' <<<"$fmt" | tr -d '{}.' | sort -u); do
          if [[ "$allowed" != *" $field "* ]]; then
            echo "template parsing error: template :1: executing \"\" at <.$field>: can't evaluate field $field in type *" >&2
            exit 1
          fi
        done
        while IFS='|' read -r p_svc p_state p_health p_exit; do
          line="$fmt"
          line="${line//'{{.Service}}'/$p_svc}"
          line="${line//'{{.Name}}'/$p_svc}"
          line="${line//'{{.State}}'/$p_state}"
          line="${line//'{{.Health}}'/$p_health}"
          line="${line//'{{.ExitCode}}'/$p_exit}"
          line="${line//'{{.Image}}'/$(imgs_tag "$p_svc")}"
          line="${line//'{{.ID}}'/cid-$p_svc}"
          line="${line//'{{.Status}}'/$p_state}"
          printf '%s\n' "$line"
        done <"$S/svc"
        exit 0
        ;;

      stop)
        log "compose:stop:cwd=$PWD"
        n=$(( $(state stop-count 0) + 1 )); set_state stop-count "$n"
        if [[ -e "$S/fail-stop-2" && "$n" == 2 ]]; then
          echo "stop failed (injected)" >&2
          exit 1
        fi
        for s in "$@"; do set_svc "$s" "stopped||0"; done
        exit 0
        ;;

      up)
        log "compose:up:cwd=$PWD"
        n=$(( $(state up-count 0) + 1 )); set_state up-count "$n"
        if [[ -e "$S/fail-up-$n" ]]; then
          echo "up failed (injected)" >&2
          exit 1
        fi
        api_tag="$(rel_env API_IMAGE)"
        cw_tag="$(rel_env CONSOLE_WEB_IMAGE)"
        eg_tag="$(rel_env EGRESS_PROXY_IMAGE)"
        for t in "$api_tag" "$cw_tag" "$eg_tag"; do
          [[ -n "$(img_row "$t")" ]] || { echo "an image for $t is missing" >&2; exit 1; }
        done
        for s in proxy console-web api worker postgres redis minio; do set_svc "$s" "running|healthy|0"; done
        set_svc scheduler "running||0"
        set_svc minio-bootstrap "exited||0"
        cat >"$S/imgs" <<IMGSUP
api|$api_tag
worker|$api_tag
scheduler|$api_tag
console-web|$cw_tag
IMGSUP
        exit 0
        ;;

      exec)
        while [[ "${1:-}" == -* ]]; do shift; done
        svc="$1"; shift
        if [[ "$svc" == api && "$*" == "python -m backend.licensing status-line" ]]; then
          log "compose:exec:$svc:license-status"
          if [[ -e "$S/fail-license" ]]; then
            echo "license status failed (injected)" >&2
            exit 1
          fi
          echo "license: state=grace reason=- id=L-2027-001 customer=acme expires=2027-01-01T00:00:00Z grace_until=2027-01-15T00:00:00Z days_left=3 modules=insights,quality limits=audio_seconds=360000,concurrent_jobs=unlimited key=aaaaaaaaaaaaaaaa"
          exit 0
        fi
        if [[ "${1:-}" == sh && "${2:-}" == -c ]]; then
          payload="$3"
          case "$payload" in
            *pg_dump*)
              log "compose:exec:$svc:pg_dump"
              if [[ -e "$S/fail-dump" ]]; then
                echo "pg_dump failed (injected)" >&2
                exit 1
              fi
              echo "$(state db-version)" >"$S/dump-version"
              echo "$(state db-tables)" >"$S/dump-tables"
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
              rm -f "$S/fail-db-version" # the restore made the version readable again
              exit 0
              ;;
            *alembic_version*)
              log "compose:exec:$svc:psql"
              if [[ "$payload" == *homayar_restorecheck* ]]; then
                echo "$(state scratch-version)"
              else
                if [[ -e "$S/fail-db-version" ]]; then
                  echo "psql: error: connection to the LIVE database failed (injected)" >&2
                  exit 1
                fi
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
            *)
              log "compose:exec:$svc:sh-unknown"
              echo "exec sh stub: unsupported payload: $payload" >&2
              exit 1
              ;;
          esac
        fi
        log "compose:exec:$svc:unknown"
        echo "exec stub: unsupported: $*" >&2
        exit 1
        ;;

      run)
        while [[ "${1:-}" == -* ]]; do shift; done
        rsvc="$1"; shift
        words="$*"
        if [[ "$rsvc" == migrate ]]; then
          log "compose:run:migrate:cwd=$PWD"
          if [[ -e "$S/fail-migrate" ]]; then
            # a migration that already moved the version and then failed: the
            # rollback must restore the dump (the version differs). With the
            # break-db-version marker the version cannot even be read
            # afterwards (an unknown database state): the rollback must
            # restore the dump all the same.
            tag="$(rel_env API_IMAGE)"
            row="$(img_row "$tag")"
            IFS='|' read -r _t _id _l _sha bihead _ix <<<"$row"
            printf '%s\n' "$bihead" >"$S/db-version"
            if [[ -e "$S/break-db-version" ]]; then
              touch "$S/fail-db-version"
            fi
            echo "migrate failed (injected, after moving the version)" >&2
            exit 1
          fi
          tag="$(rel_env API_IMAGE)"
          row="$(img_row "$tag")"
          IFS='|' read -r _t _id _l _sha bihead _ix <<<"$row"
          printf '%s\n' "$bihead" >"$S/db-version"
          exit 0
        fi
        case "$words" in
          *model_preflight*)
            log "compose:run:$rsvc:model_preflight:cwd=$PWD"
            echo '{"ready": true, "host": {"asr_runtime": "pass"}}'
            code=0
            [[ -f "$S/preflight-code" ]] && code="$(cat "$S/preflight-code")"
            exit "$code"
            ;;
          *support_bundle*)
            log "compose:run:$rsvc:collect:cwd=$PWD"
            echo '{"schema_version": 1, "release": {"identity": {"version": "0.5.0"}}}'
            exit 0
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
        if [[ -e "$S/logs-empty" ]]; then
          exit 0
        fi
        echo "one plain log line"
        echo "one line with ERROR inside"
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

chmod +x "$bin/curl" "$bin/docker" "$bin/git"

# --- case helpers ------------------------------------------------------------------

run_script() { # run_script <args...>: sets OUT and RC; collects output for
  # the secret-marker check
  RC=0
  OUT="$(bash "$subject" "$@" </dev/null 2>&1)" || RC=$?
  printf '%s\n' "$OUT" >>"$CASE_OUT"
}

run_env() { # run_env <VAR=VAL> <args...>: like run_script with one env pair
  RC=0
  OUT="$(env "$1" bash "$subject" "${@:2}" </dev/null 2>&1)" || RC=$?
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
calls_before() { # calls_before <a> <b>
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
current_is() {
  local got
  got="$(readlink -f "$HOME_F/current")"
  [[ "$got" == "$HOME_F/releases/$1" ]] || {
    detail "current points to $got, want $HOME_F/releases/$1"
    exit 1
  }
}
db_is() {
  [[ "$(cat "$state/db-version")" == "$1" ]] || {
    detail "db-version is $(cat "$state/db-version"), want $1"
    exit 1
  }
}
releases_untouched() { # no refusal may leave an extracted release behind
  local n
  n="$(find "$HOME_F/releases" -mindepth 1 -maxdepth 1 | wc -l | tr -d '[:space:]')"
  [[ "$n" == 0 ]] || { detail "releases/ holds $n entries after a refusal"; exit 1; }
}

passed=0
failed=0
run_case() { # run_case <case function>: one PASS or FAIL line per case; the
  # secret-marker check runs in the parent over the real outputs
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

# --- cases ---------------------------------------------------------------------------

c_usage_no_command() (
  prepare
  run_script
  want_rc 2
  want_out "usage"
  want_no_secret
)

c_usage_unknown_command() (
  prepare
  run_script frobnicate
  want_rc 2
  want_out "usage"
)

c_usage_unknown_flag() (
  prepare
  run_script upgrade --package "$tmp/pkg-$NEW_VER" --turbo
  want_rc 2
  want_out "usage"
)

c_pack_ok() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local src="$tmp/checkout" out="$tmp/outpkg" h
  make_checkout "$src"
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$KEY"
  want_rc 0
  file_has "$out/release.env" "^HOMAYAR_RELEASE_VERSION=$NEW_VER$"
  file_has "$out/release.env" "^HOMAYAR_RELEASE_SHA=$NEW_SHA$"
  file_has "$out/release.env" "^API_IMAGE=$NEW_API$"
  [[ -f "$out/SHA256SUMS" && -f "$out/images/api.tar" && -f "$out/images/console-web.tar" &&
    -f "$out/images/egress-proxy.tar" && -d "$out/infra/compose" ]] || {
    detail "the package layout is incomplete"
    exit 1
  }
  file_has "$out/SHA256SUMS" "release.env"
  # HOM-107: the package is signed and described
  [[ -f "$out/SHA256SUMS.sig" ]] || { detail "no SHA256SUMS.sig"; exit 1; }
  [[ "$(stat -c '%s' "$out/SHA256SUMS.sig")" == 64 ]] || {
    detail "SHA256SUMS.sig is not exactly 64 bytes"
    exit 1
  }
  openssl pkeyutl -verify -pubin -inkey "$PUB" -rawin -in "$out/SHA256SUMS" \
    -sigfile "$out/SHA256SUMS.sig" >/dev/null 2>&1 || {
    detail "SHA256SUMS.sig does not verify with the signing key's public key"
    exit 1
  }
  file_has "$out/BUNDLE.manifest" "^schema homayar-offline-bundle/1$"
  file_has "$out/BUNDLE.manifest" "^release $NEW_VER $NEW_SHA$"
  h="$(sha256sum "$out/images/api.tar" | awk '{ print $1 }')"
  file_has "$out/BUNDLE.manifest" "^image images/api.tar $h $NEW_API LicenseRef-Homayar-Proprietary$"
  h="$(sha256sum "$out/images/console-web.tar" | awk '{ print $1 }')"
  file_has "$out/BUNDLE.manifest" "^image images/console-web.tar $h $NEW_CW LicenseRef-Homayar-Proprietary$"
  h="$(sha256sum "$out/images/egress-proxy.tar" | awk '{ print $1 }')"
  file_has "$out/BUNDLE.manifest" "^image images/egress-proxy.tar $h $NEW_EG LicenseRef-Homayar-Proprietary$"
  file_has "$out/BUNDLE.manifest" "^requires bash >=4.4$"
  file_has "$out/BUNDLE.manifest" "^requires nvidia-container-toolkit gpu-profile-only$"
  file_has "$out/SHA256SUMS" "BUNDLE.manifest"
  want_out "fingerprint $FINGERPRINT"
  want_no_out "not signed"
  want_no_out "PRIVATE KEY"
  ! grep -rqF "PRIVATE KEY" "$out" || { detail "a package file holds private key material"; exit 1; }
  # the packed package verifies: prove it by installing nothing, just verify
  # through an install refusal that reaches the verify step
  run_script status
  want_no_secret
)

c_pack_dirty_tree() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  touch "$state/git-dirty"
  local src="$tmp/checkout-dirty" out="$tmp/outpkg-dirty"
  make_checkout "$src"
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$KEY"
  want_rc 2
  want_out "dirty"
)

c_pack_refuses_nonempty_output() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local src="$tmp/checkout2" out="$tmp/outpkg2"
  make_checkout "$src"
  mkdir -p "$out"
  touch "$out/already-here"
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$KEY"
  want_rc 2
  want_out "not empty"
)

c_install_happy() (
  prepare_fresh
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 0
  current_is "$OLD_VER"
  calls_has "docker:load"
  calls_before "docker:load" "compose:run:migrate"
  calls_has "compose:run:api:model_preflight"
  # migrate runs between the stores up and the full stack up: the FIRST up is
  # the stores, the LAST up is the stack
  local m u
  m="$(grep -nF 'compose:run:migrate' "$state/calls" | head -n 1 | cut -d: -f1)"
  u="$(grep -nF 'compose:up' "$state/calls" | tail -n 1 | cut -d: -f1)"
  { [[ -n "$m" && -n "$u" && "$m" -lt "$u" ]]; } || {
    detail "migrate (line $m) must come before the stack up (line $u)"
    exit 1
  }
  # every container-starting compose call runs from the final release folder
  # (compose resolves the relative bind mounts to absolute paths there)
  if grep -Eq 'compose:(up|run|stop).*cwd=.*\.tmp\.' "$state/calls"; then
    detail "a container-starting compose call ran from a temp folder:"
    grep -E 'compose:(up|run|stop).*cwd=.*\.tmp\.' "$state/calls" | head -n 3 >&2 || true
    exit 1
  fi
  calls_has "compose:up:cwd=$HOME_F/releases/$OLD_VER"
  calls_has "compose:run:migrate:cwd=$HOME_F/releases/$OLD_VER"
  # HOM-107: the run names the verifying key's fingerprint and the signed
  # requirements, and the old unsigned-warning line is gone
  want_out "fingerprint $FINGERPRINT"
  want_out "requires bash >=4.4"
  want_no_out "not signed"
  want_no_secret
)

c_install_migrate_fails_retry() (
  prepare_fresh
  touch "$state/fail-migrate"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  # the failed install leaves nothing behind: no release, no current
  releases_untouched
  [[ ! -e "$HOME_F/current" && ! -L "$HOME_F/current" ]] || {
    detail "current exists after a failed install"
    exit 1
  }
  # the retry, with the migration working again, succeeds
  rm -f -- "$state/fail-migrate"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 0
  current_is "$OLD_VER"
  want_no_secret
)

c_install_from_tar() (
  prepare_fresh
  # a .tar package holds the package contents at the top level
  tar -cf "$tmp/pkg-$OLD_VER.tar" -C "$tmp/pkg-$OLD_VER" .
  run_script install --package "$tmp/pkg-$OLD_VER.tar"
  want_rc 0
  current_is "$OLD_VER"
  want_no_secret
)

c_install_refused_when_current_exists() (
  prepare
  run_script install --package "$tmp/pkg-$NEW_VER"
  want_rc 2
  want_out "use upgrade"
  calls_lack "docker:load"
  want_no_secret
)

c_pkg_tampered_refused() (
  prepare_fresh
  printf 'tampered\n' >>"$tmp/pkg-$OLD_VER/infra/compose/compose.onprem.yaml"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "hash mismatch for infra/compose/compose.onprem.yaml"
  calls_lack "docker:load"
  releases_untouched
  want_no_secret
)

c_pkg_extra_file_refused() (
  prepare_fresh
  printf 'unlisted\n' >"$tmp/pkg-$OLD_VER/extra.txt"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "unlisted file: extra.txt"
  calls_lack "docker:load"
  releases_untouched
)

c_pkg_missing_file_refused() (
  prepare_fresh
  rm -f -- "$tmp/pkg-$OLD_VER/infra/nginx/homayar.conf"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "missing file: infra/nginx/homayar.conf"
  releases_untouched
)

c_pkg_dotdot_path_refused() (
  prepare_fresh
  printf '%s  ../evil.txt\n' "$(sha256sum "$tmp/pkg-$OLD_VER/release.env" | awk '{ print $1 }')" \
    >>"$tmp/pkg-$OLD_VER/SHA256SUMS"
  # the appended line re-signs so the forbidden path is the rule under test
  sign_pkg "$tmp/pkg-$OLD_VER"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "forbidden path"
  releases_untouched
)

c_pkg_bad_release_env_refused() (
  prepare_fresh
  printf 'EXTRA_KEY=1\n' >>"$tmp/pkg-$OLD_VER/release.env"
  write_sums "$tmp/pkg-$OLD_VER"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "five allowed keys"
  releases_untouched
)

c_pkg_latest_tag_refused() (
  prepare_fresh
  sed -i "s|^API_IMAGE=.*|API_IMAGE=homayar/api:latest|" "$tmp/pkg-$OLD_VER/release.env"
  write_sums "$tmp/pkg-$OLD_VER"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "versioned tag"
  releases_untouched
)

c_upgrade_happy() (
  prepare
  run_script upgrade --package "$tmp/pkg-$NEW_VER"
  want_rc 0
  current_is "$NEW_VER"
  db_is "$NEW_REV"
  # the record
  local sid
  sid="$(latest_state_id)"
  [[ -n "$sid" ]] || { detail "no state record"; exit 1; }
  file_has "$HOME_F/state/$sid/record.env" "^FROM_VERSION=$OLD_VER$"
  file_has "$HOME_F/state/$sid/record.env" "^TO_VERSION=$NEW_VER$"
  file_has "$HOME_F/state/$sid/record.env" "^ALEMBIC_BEFORE=$BASE_REV$"
  file_has "$HOME_F/state/$sid/record.env" "^STOPPED_AT="
  [[ -s "$HOME_F/state/$sid/db.dump" ]] || { detail "no dump file"; exit 1; }
  # the new migration head is read during preflight, before the writers stop
  calls_before "docker:run:python" "compose:stop"
  # the order: stop writers, then dump, prove, migrate
  calls_has "compose:stop"
  calls_before "compose:stop" "compose:exec:postgres:pg_dump"
  calls_before "compose:exec:postgres:pg_dump" "compose:exec:postgres:pg_restore-list"
  calls_before "compose:exec:postgres:pg_restore-list" "compose:run:migrate"
  calls_before "compose:run:migrate" "compose:up"
  # old images are never removed
  want_no_out "prune"
  want_no_secret
)

c_upgrade_already_on_version() (
  prepare
  run_script upgrade --package "$tmp/pkg-$OLD_VER"
  want_rc 0
  want_out "Already on $OLD_VER"
  calls_lack "compose:stop"
  want_no_secret
)

c_upgrade_same_version_other_sha() (
  prepare
  sed -i "s|^HOMAYAR_RELEASE_SHA=.*|HOMAYAR_RELEASE_SHA=ffffffffffffffffffffffffffffffffffffffff|" \
    "$tmp/pkg-$OLD_VER/release.env"
  # keep the signed bundle manifest in sync so the version rule is what refuses
  sed -i "s|^release $OLD_VER $OLD_SHA$|release $OLD_VER ffffffffffffffffffffffffffffffffffffffff|" \
    "$tmp/pkg-$OLD_VER/BUNDLE.manifest"
  write_sums "$tmp/pkg-$OLD_VER"
  run_script upgrade --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "SHA differs"
)

c_upgrade_stale_gauge_refused() (
  prepare
  write_gauge $((25 * 3600))
  run_script upgrade --package "$tmp/pkg-$NEW_VER"
  want_rc 2
  want_out "backup"
  calls_lack "compose:stop"
  calls_lack "compose:run:migrate"
  current_is "$OLD_VER"
  db_is "$BASE_REV"
  want_no_secret
)

c_upgrade_missing_gauge_refused() (
  prepare
  rm -f -- "$NODE_EXPORTER_TEXTFILE_DIR/homayar_backup.prom"
  run_script upgrade --package "$tmp/pkg-$NEW_VER"
  want_rc 2
  want_out "homayar_backup_last_success_timestamp_seconds"
  calls_lack "compose:stop"
)

c_upgrade_skip_backup_check() (
  prepare
  write_gauge $((25 * 3600))
  run_script upgrade --package "$tmp/pkg-$NEW_VER" --skip-backup-check "no backup store yet"
  want_rc 0
  current_is "$NEW_VER"
  local sid
  sid="$(latest_state_id)"
  file_has "$HOME_F/state/$sid/record.env" '^SKIP_BACKUP_REASON=no backup store yet$'
  want_no_secret
)

c_upgrade_gauge_23h_proceeds() (
  prepare
  write_gauge $((23 * 3600))
  run_script upgrade --package "$tmp/pkg-$NEW_VER"
  want_rc 0
  current_is "$NEW_VER"
)

c_upgrade_migrate_fails_rolls_back() (
  prepare
  touch "$state/fail-migrate"
  run_script upgrade --package "$tmp/pkg-$NEW_VER"
  want_rc 1
  calls_has "compose:exec:postgres:pg_restore-live"
  current_is "$OLD_VER"
  db_is "$BASE_REV"
  want_out "ROLLED BACK"
  want_no_secret
)

c_upgrade_rollback_fails_exit_3() (
  prepare
  touch "$state/fail-migrate" "$state/fail-stop-2"
  run_script upgrade --package "$tmp/pkg-$NEW_VER"
  want_rc 3
  want_out "by hand"
  want_out "ROLLBACK FAILED"
  want_no_secret
)

c_upgrade_dump_fails_writers_restarted() (
  prepare
  touch "$state/fail-dump"
  run_script upgrade --package "$tmp/pkg-$NEW_VER"
  want_rc 2
  current_is "$OLD_VER"
  db_is "$BASE_REV"
  calls_has "compose:exec:postgres:pg_dump"
  calls_before "compose:exec:postgres:pg_dump" "compose:up"
  want_out "restarted"
  want_no_secret
)

c_rollback_wrong_timestamp() (
  prepare
  run_script upgrade --package "$tmp/pkg-$NEW_VER" --skip-backup-check "t"
  want_rc 0
  local sid stopped
  sid="$(latest_state_id)"
  stopped="$(record_value "$sid" STOPPED_AT)"
  run_script rollback --to "$sid" --accept-data-loss-since "${stopped}Z"
  want_rc 2
  want_out "stopped at"
  want_no_secret
)

c_rollback_ok() (
  prepare
  run_script upgrade --package "$tmp/pkg-$NEW_VER" --skip-backup-check "t"
  want_rc 0
  local sid stopped
  sid="$(latest_state_id)"
  stopped="$(record_value "$sid" STOPPED_AT)"
  [[ -n "$stopped" ]] || { detail "no STOPPED_AT in the record"; exit 1; }
  run_script rollback --to "$sid" --accept-data-loss-since "$stopped"
  want_rc 0
  current_is "$OLD_VER"
  db_is "$BASE_REV"
  calls_has "compose:exec:postgres:pg_restore-live"
  want_no_secret
)

c_rollback_unknown_state() (
  prepare
  run_script rollback --to 19990101T000000Z --accept-data-loss-since 19990101T000000Z
  want_rc 2
  want_out "no state record"
)

c_lock_held() (
  prepare
  mkdir -p "$HOME_F/ops/homayar-onprem.lock"
  run_script preflight
  want_rc 2
  want_out "another run holds"
  want_no_secret
)

c_preflight_ok() (
  prepare
  run_script preflight
  want_rc 0
  calls_has "compose:config"
  calls_has "compose:run:api:model_preflight"
  want_no_secret
)

c_preflight_exit_code_passed_through() (
  prepare
  printf '1\n' >"$state/preflight-code"
  run_script preflight
  want_rc 1
  want_no_secret
)

c_support_bundle_ok() (
  prepare
  local out="$tmp/bundle.tar.gz"
  run_script support-bundle --output "$out"
  want_rc 0
  [[ -f "$out" ]] || { detail "no bundle file"; exit 1; }
  [[ "$(stat -c '%a' "$out")" == 600 ]] || { detail "bundle mode is $(stat -c '%a' "$out"), want 600"; exit 1; }
  calls_has "docker:run:scan:user=$(id -u):$(id -g)"
  calls_has "compose:run:api:collect"
  calls_has "compose:logs"
  # the temp bundle dir is gone, and no partial .tmp output survives
  local n
  n="$(find "$HOME_F/ops" -maxdepth 1 -name 'bundle.*' | wc -l | tr -d '[:space:]')"
  [[ "$n" == 0 ]] || { detail "a temp bundle dir survived"; exit 1; }
  [[ ! -e "$out.tmp" ]] || { detail "a partial $out.tmp survived"; exit 1; }
  want_no_secret
)

c_support_bundle_logs_empty_count_zero() (
  prepare
  local out="$tmp/bundle-quiet.tar.gz"
  touch "$state/logs-empty"
  run_script support-bundle --output "$out"
  want_rc 0
  tar -xzOf "$out" ./logs-summary.json >"$tmp/logs-summary.json" 2>/dev/null ||
    { detail "cannot read logs-summary.json from the bundle"; exit 1; }
  file_has "$tmp/logs-summary.json" '"proxy": {"lines": 0, "error_warning_critical": 0}'
  want_no_secret
)

c_support_bundle_finding_fails_closed() (
  prepare
  local out="$tmp/bundle-fail.tar.gz"
  touch "$state/scan-fail"
  run_script support-bundle --output "$out"
  want_rc 2
  want_out "secret_value"
  [[ ! -e "$out" ]] || { detail "a bundle was written despite findings"; exit 1; }
  want_no_secret
)

c_support_bundle_scan_error_fails_closed() (
  prepare
  local out="$tmp/bundle-error.tar.gz"
  touch "$state/scan-error"
  run_script support-bundle --output "$out"
  want_rc 2
  want_out "usage or read error"
  [[ ! -e "$out" ]] || { detail "a bundle was written despite a scan error"; exit 1; }
  want_no_secret
)

c_support_bundle_scan_crash_fails_closed() (
  prepare
  local out="$tmp/bundle-crash.tar.gz"
  touch "$state/scan-crash"
  run_script support-bundle --output "$out"
  want_rc 2
  want_out "scan failed (exit 137)"
  want_no_out "the bundle scan found"
  [[ ! -e "$out" ]] || { detail "a bundle was written despite a scan crash"; exit 1; }
  want_no_secret
)

c_status() (
  prepare
  run_script status
  want_rc 0
  want_out "current release: $OLD_VER"
  want_out "$OLD_SHA"
  want_out "alembic version: $BASE_REV"
  want_out "license: state=grace reason=- id=L-2027-001 customer=acme"
  want_out "key=aaaaaaaaaaaaaaaa"
  want_no_out "license: unreadable"
  calls_has "compose:exec:api:license-status"
  want_no_secret
)

c_status_license_unreadable() ( # HOM-109: a failed licence call is named, status still exits 0
  prepare
  touch "$state/fail-license"
  run_script status
  want_rc 0
  want_out "license: unreadable"
  want_no_out "license: state="
  want_out "alembic version: $BASE_REV"
  want_no_secret
)

c_backup_execs_release_script() (
  prepare
  local out=""
  out="$(bash "$subject" backup --no-prune </dev/null 2>&1)" || {
    printf '%s\n' "$out" >>"$CASE_OUT"
    detail "backup exec failed"
    exit 1
  }
  printf '%s\n' "$out" >>"$CASE_OUT"
  grep -q "backup-stub --no-prune" <<<"$out" || { detail "backup.sh was not exec'd"; exit 1; }
  want_no_secret
)

c_homyar_home_env_respected() (
  prepare
  RC=0
  OUT="$(env -u ONPREM_TEST_HOME HOMAYAR_HOME="$HOME_F" bash "$subject" status </dev/null 2>&1)" || RC=$?
  printf '%s\n' "$OUT" >>"$CASE_OUT"
  want_rc 0
  want_out "current release: $OLD_VER"
  want_no_secret
)

c_http_port_from_operator_env() (
  prepare
  sed -i 's/^HTTP_PORT=8080$/HTTP_PORT=9080/' "$HOME_F/config/onprem.env"
  run_script upgrade --package "$tmp/pkg-$NEW_VER"
  want_rc 0
  grep -q ':9080/healthz' "$state/curls" || { detail "health checks never used port 9080"; exit 1; }
  want_no_secret
)

c_http_port_quoted_in_operator_env() (
  prepare
  sed -i 's/^HTTP_PORT=8080$/HTTP_PORT="9080"/' "$HOME_F/config/onprem.env"
  run_script upgrade --package "$tmp/pkg-$NEW_VER"
  want_rc 0
  grep -q ':9080/healthz' "$state/curls" || { detail "a quoted HTTP_PORT was not unwrapped"; exit 1; }
  want_no_secret
)

c_upgrade_load_fails_then_retry() (
  prepare
  touch "$state/fail-load"
  run_script upgrade --package "$tmp/pkg-$NEW_VER"
  want_rc 2
  want_out "docker load"
  # neither a releases/0.6.0 nor a half-extracted temp folder survives
  local n
  n="$(find "$HOME_F/releases" -mindepth 1 -maxdepth 1 | wc -l | tr -d '[:space:]')"
  [[ "$n" == 1 ]] || { detail "releases/ holds $n entries, want only the old release"; exit 1; }
  current_is "$OLD_VER"
  # the retry, with the load working again, succeeds
  rm -f -- "$state/fail-load"
  run_script upgrade --package "$tmp/pkg-$NEW_VER"
  want_rc 0
  current_is "$NEW_VER"
  want_no_secret
)

c_upgrade_db_unreadable_restores_dump() (
  prepare
  touch "$state/fail-migrate" "$state/break-db-version"
  run_script upgrade --package "$tmp/pkg-$NEW_VER"
  want_rc 1
  # an unreadable version is an unknown state: the dump is restored
  calls_has "compose:exec:postgres:pg_restore-live"
  current_is "$OLD_VER"
  db_is "$BASE_REV"
  want_out "ROLLED BACK"
  want_no_secret
)

c_upgrade_db_unreadable_restore_fails_exit_3() (
  prepare
  touch "$state/fail-migrate" "$state/break-db-version" "$state/fail-live-restore"
  run_script upgrade --package "$tmp/pkg-$NEW_VER"
  want_rc 3
  want_out "by hand"
  want_out "R4 SKIPPED"
  # the stack is never started on a database in unknown state
  local ups
  ups="$(cat "$state/up-count" 2>/dev/null || printf 0)"
  [[ "$ups" == 0 ]] || { detail "compose up ran $ups times, want 0"; exit 1; }
  want_no_secret
)

c_rollback_image_tag_moved() (
  prepare
  run_script upgrade --package "$tmp/pkg-$NEW_VER" --skip-backup-check "t"
  want_rc 0
  # the old api tag now resolves to a different image id (a retag)
  sed -i "1i homayar/api:$OLD_VER|sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee|$OLD_SHA|$OLD_SHA|$BASE_REV|0" \
    "$state/images"
  local sid stopped
  sid="$(latest_state_id)"
  stopped="$(record_value "$sid" STOPPED_AT)"
  run_script rollback --to "$sid" --accept-data-loss-since "$stopped"
  want_rc 2
  want_out "tag moved"
  want_no_secret
)

c_pkg_symlink_refused() (
  prepare_fresh
  ln -s release.env "$tmp/pkg-$OLD_VER/link.env"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "not a regular file or folder: link.env"
  releases_untouched
)

c_pkg_multislash_image_ref_ok() (
  prepare
  make_package "$tmp/pkg-registry" "$NEW_VER" "$NEW_SHA" \
    "ghcr.io/kohandezh/homayar/api:1.2.3" "ghcr.io/kohandezh/homayar/console-web:1.2.3" \
    "ghcr.io/kohandezh/homayar/egress-proxy:1.2.3" \
    "$NEW_API_ID" "$NEW_CW_ID" "$NEW_EG_ID" "$NEW_REV"
  run_script upgrade --package "$tmp/pkg-registry"
  want_rc 0
  current_is "$NEW_VER"
  want_no_secret
)

c_pkg_no_slash_image_ref_ok() (
  prepare
  make_package "$tmp/pkg-noslash" "$NEW_VER" "$NEW_SHA" \
    "homayar-api:1.2.3" "homayar-console-web:1.2.3" "homayar-egress-proxy:1.2.3" \
    "$NEW_API_ID" "$NEW_CW_ID" "$NEW_EG_ID" "$NEW_REV"
  run_script upgrade --package "$tmp/pkg-noslash"
  want_rc 0
  current_is "$NEW_VER"
  want_no_secret
)

c_skip_reason_control_char_refused() (
  prepare
  run_script upgrade --package "$tmp/pkg-$NEW_VER" --skip-backup-check $'line one\nline two'
  want_rc 2
  want_out "control character"
  calls_lack "compose:stop"
  want_no_secret
)

# --- HOM-107: signing cases ----------------------------------------------------------

pack_denied() { # a pack refusal that must leave no signature behind
  want_rc 2
  [[ ! -e "$2/SHA256SUMS.sig" ]] || { detail "$2 holds a SHA256SUMS.sig"; exit 1; }
}

c_pack_key_from_env() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local src="$tmp/checkout-env" out="$tmp/outpkg-env" h
  make_checkout "$src"
  run_env "HOMAYAR_BUNDLE_SIGNING_KEY=$KEY" pack --source "$src" --output "$out" \
    --version "$NEW_VER" --api-image "$NEW_API" --console-web-image "$NEW_CW" \
    --egress-proxy-image "$NEW_EG"
  want_rc 0
  [[ -f "$out/SHA256SUMS.sig" ]] || { detail "no SHA256SUMS.sig"; exit 1; }
  openssl pkeyutl -verify -pubin -inkey "$PUB" -rawin -in "$out/SHA256SUMS" \
    -sigfile "$out/SHA256SUMS.sig" >/dev/null 2>&1 || {
    detail "SHA256SUMS.sig does not verify"
    exit 1
  }
  file_has "$out/BUNDLE.manifest" "^schema homayar-offline-bundle/1$"
  want_out "fingerprint $FINGERPRINT"
  want_no_secret
)

c_pack_no_key_refused() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local src="$tmp/checkout-nokey" out="$tmp/outpkg-nokey"
  make_checkout "$src"
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG"
  pack_denied "$OUT" "$out"
  want_out "signing key"
)

c_pack_key_mode_refused() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local src="$tmp/checkout-mode" out="$tmp/outpkg-mode" laxkey="$tmp/keys/lax-key.pem"
  make_checkout "$src"
  cp "$KEY" "$laxkey"
  chmod 644 "$laxkey"
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$laxkey"
  pack_denied "$OUT" "$out"
  want_out "permission"
)

c_pack_key_not_ed25519_refused() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local src="$tmp/checkout-rsa" out="$tmp/outpkg-rsa"
  make_checkout "$src"
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$RSA_KEY"
  pack_denied "$OUT" "$out"
  want_out "Ed25519"
)

c_pack_key_missing_file_refused() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local src="$tmp/checkout-gone" out="$tmp/outpkg-gone"
  make_checkout "$src"
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$tmp/keys/no-such-key.pem"
  pack_denied "$OUT" "$out"
)

c_pack_models_included() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local sha src="$tmp/checkout-models" out="$tmp/outpkg-models"
  make_checkout "$src"
  sha="$(write_model_fixture)"
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$KEY" --model-root "$MODEL_ROOT" --model-manifest "$MODEL_MANIFEST"
  want_rc 0
  [[ -f "$out/models/asr/demo/model.bin" ]] || { detail "the model file was not copied"; exit 1; }
  cmp -s "$MODEL_ROOT/asr/demo/model.bin" "$out/models/asr/demo/model.bin" || {
    detail "the copied model file differs"
    exit 1
  }
  [[ -f "$out/manifests/demo-model-manifest.json" ]] || { detail "the manifest was not copied"; exit 1; }
  file_has "$out/BUNDLE.manifest" "^model models/asr/demo/model.bin $sha MIT asr.demo-model$"
  file_has "$out/SHA256SUMS" "models/asr/demo/model.bin"
  file_has "$out/SHA256SUMS" "manifests/demo-model-manifest.json"
  # the packed bundle with models installs end to end
  prepare_fresh
  run_script install --package "$out"
  want_rc 0
  current_is "$NEW_VER"
  want_no_secret
)

c_pack_model_sha_mismatch_refused() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local src="$tmp/checkout-ms" out="$tmp/outpkg-ms"
  make_checkout "$src"
  write_model_fixture >/dev/null
  printf 'extra bytes\n' >>"$MODEL_ROOT/asr/demo/model.bin"
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$KEY" --model-root "$MODEL_ROOT" --model-manifest "$MODEL_MANIFEST"
  pack_denied "$OUT" "$out"
  want_out "asr/demo/model.bin"
)

c_pack_model_dir_artifact_refused() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local sha src="$tmp/checkout-dir" out="$tmp/outpkg-dir"
  make_checkout "$src"
  sha="$(write_model_fixture)"
  sed -i 's/"kind": "file"/"kind": "dir"/' "$MODEL_MANIFEST"
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$KEY" --model-root "$MODEL_ROOT" --model-manifest "$MODEL_MANIFEST"
  pack_denied "$OUT" "$out"
  want_out "dir artifacts"
)

c_pack_model_no_license_refused() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local src="$tmp/checkout-nolic" out="$tmp/outpkg-nolic"
  make_checkout "$src"
  write_model_fixture >/dev/null
  sed -i 's/"license": "MIT",//' "$MODEL_MANIFEST"
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$KEY" --model-root "$MODEL_ROOT" --model-manifest "$MODEL_MANIFEST"
  pack_denied "$OUT" "$out"
  want_out "license"
)

c_pack_model_dotdot_path_refused() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local sha src="$tmp/checkout-dd" out="$tmp/outpkg-dd"
  make_checkout "$src"
  sha="$(sha256sum "$HOME_F/config/onprem.env" | awk '{ print $1 }')"
  write_model_fixture >/dev/null
  sed -i "s|asr/demo/model.bin|../../onprem.env|" "$MODEL_MANIFEST"
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$KEY" --model-root "$MODEL_ROOT" --model-manifest "$MODEL_MANIFEST"
  pack_denied "$OUT" "$out"
)

c_pack_model_flags_half_refused() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local src="$tmp/checkout-half" out="$tmp/outpkg-half"
  make_checkout "$src"
  write_model_fixture >/dev/null
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$KEY" --model-manifest "$MODEL_MANIFEST"
  want_rc 2
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$KEY" --model-root "$MODEL_ROOT"
  want_rc 2
)

c_pack_bundle_conf_missing_refused() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local src="$tmp/checkout-noconf" out="$tmp/outpkg-noconf"
  mkdir -p "$src/infra/compose" "$src/infra/scripts" "$src/infra/nginx"
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$KEY"
  pack_denied "$OUT" "$out"
  want_out "bundle.conf"
)

c_pack_bundle_conf_license_missing_refused() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local src="$tmp/checkout-nolic-conf" out="$tmp/outpkg-nolic-conf"
  make_checkout "$src"
  sed -i '/^license console-web /d' "$src/infra/offline/bundle.conf"
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$KEY"
  pack_denied "$OUT" "$out"
  want_out "console-web"
)

c_pack_bundle_conf_no_requires_refused() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local src="$tmp/checkout-noreq" out="$tmp/outpkg-noreq"
  make_checkout "$src"
  sed -i '/^requires /d' "$src/infra/offline/bundle.conf"
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$KEY"
  pack_denied "$OUT" "$out"
  want_out "requires"
)

c_pack_nested_sums_name_listed_ok() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local src="$tmp/checkout-nested" out="$tmp/outpkg-nested"
  make_checkout "$src"
  # a nested file that shares the hash list's name must still be listed
  printf 'not the package hash list\n' >"$src/infra/scripts/SHA256SUMS"
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$KEY"
  want_rc 0
  file_has "$out/SHA256SUMS" "infra/scripts/SHA256SUMS"
  [[ -f "$out/SHA256SUMS.sig" ]] || { detail "no SHA256SUMS.sig"; exit 1; }
  # and the packed bundle installs with the nested file listed
  prepare_fresh
  run_script install --package "$out"
  want_rc 0
  current_is "$NEW_VER"
  want_no_secret
)

c_pack_model_duplicate_path_refused() (
  prepare
  printf '%s\n' "$NEW_SHA" >"$state/git-sha"
  local sha src="$tmp/checkout-dup" out="$tmp/outpkg-dup"
  make_checkout "$src"
  sha="$(write_model_fixture)"
  sed "s/asr.demo-model/other.model/" "$MODEL_MANIFEST" >"$tmp/other-model-manifest.json"
  run_script pack --source "$src" --output "$out" --version "$NEW_VER" \
    --api-image "$NEW_API" --console-web-image "$NEW_CW" --egress-proxy-image "$NEW_EG" \
    --signing-key "$KEY" --model-root "$MODEL_ROOT" \
    --model-manifest "$MODEL_MANIFEST" --model-manifest "$tmp/other-model-manifest.json"
  want_rc 2
  want_out "duplicate model artifact path"
  [[ ! -e "$out/SHA256SUMS.sig" ]] || { detail "a refused pack left a signature"; exit 1; }
  want_no_secret
)

c_install_unsigned_refused() (
  prepare_fresh
  rm -f "$tmp/pkg-$OLD_VER/SHA256SUMS.sig"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "signature"
  want_out "Nothing was changed."
  calls_lack "docker:load"
  releases_untouched
  [[ ! -e "$HOME_F/current" && ! -L "$HOME_F/current" ]] || {
    detail "current exists after the refusal"
    exit 1
  }
)

c_install_untrusted_key_refused() (
  prepare_fresh
  openssl pkeyutl -sign -inkey "$ALT_KEY" -rawin -in "$tmp/pkg-$OLD_VER/SHA256SUMS" \
    -out "$tmp/pkg-$OLD_VER/SHA256SUMS.sig" 2>/dev/null
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "signature"
  want_out "Nothing was changed."
  calls_lack "docker:load"
  releases_untouched
  # the same untrusted package with its own public PEM shipped inside: the
  # package never provides trust
  cp "$ALT_PUB" "$tmp/pkg-$OLD_VER/untrusted-key.pem"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "signature"
  calls_lack "docker:load"
  releases_untouched
)

c_install_sig_bad_refused() (
  prepare_fresh
  head -c 63 "$tmp/pkg-$OLD_VER/SHA256SUMS.sig" >"$tmp/pkg-$OLD_VER/short.sig"
  mv "$tmp/pkg-$OLD_VER/short.sig" "$tmp/pkg-$OLD_VER/SHA256SUMS.sig"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "signature"
  releases_untouched
  prepare_fresh
  head -c 64 /dev/urandom >"$tmp/pkg-$OLD_VER/SHA256SUMS.sig"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "signature"
  releases_untouched
)

c_install_tamper_rewritten_refused() (
  prepare_fresh
  printf 'tampered\n' >>"$tmp/pkg-$OLD_VER/infra/nginx/homayar.conf"
  rewrite_sums_unsigned "$tmp/pkg-$OLD_VER"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "signature"
  want_out "Nothing was changed."
  calls_lack "docker:load"
  releases_untouched
)

c_install_trust_missing_refused() (
  prepare_fresh
  rm -rf "$TRUST"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "signature"
  releases_untouched
  prepare_fresh
  mkdir -p "$TRUST"
  rm -f "$TRUST"/*
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "signature"
  releases_untouched
)

c_install_trust_private_key_pem_refused() (
  prepare_fresh
  # an Ed25519 private key next to the valid public key: openssl 3 pkey
  # -pubin would read it, but a trust file must be a PUBLIC key (D3)
  cp "$KEY" "$TRUST/private-key.pem"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "signature"
  want_out "private-key.pem"
  want_no_out "PRIVATE KEY"
  releases_untouched
)

c_install_trust_not_regular_pem_refused() (
  prepare_fresh
  mkdir "$TRUST/folder.pem"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "signature"
  want_out "folder.pem"
  releases_untouched
  prepare_fresh
  ln -s "$TRUST/nowhere.pem" "$TRUST/dangling.pem"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "signature"
  want_out "dangling.pem"
  releases_untouched
)

c_install_trust_rsa_pem_refused() (
  prepare_fresh
  openssl pkey -in "$RSA_KEY" -pubout -out "$TRUST/wrong-algorithm.pem" 2>/dev/null
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "signature"
  want_out "wrong-algorithm.pem"
  releases_untouched
)

c_install_manifest_missing_refused() (
  prepare_fresh
  rm -f "$tmp/pkg-$OLD_VER/BUNDLE.manifest"
  write_sums "$tmp/pkg-$OLD_VER"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "BUNDLE.manifest"
  want_out "Nothing was changed."
  calls_lack "docker:load"
  releases_untouched
)

c_install_manifest_image_hash_refused() (
  prepare_fresh
  sed -i "s|^image images/api.tar [0-9a-f]\\{64\\} |image images/api.tar $(printf '0%.0s' {1..64}) |" \
    "$tmp/pkg-$OLD_VER/BUNDLE.manifest"
  write_sums "$tmp/pkg-$OLD_VER"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "BUNDLE.manifest"
  releases_untouched
)

c_install_manifest_ref_mismatch_refused() (
  prepare_fresh
  sed -i "s|^image images/api.tar \([0-9a-f]*\) $OLD_API |image images/api.tar \1 homayar/api:9.9.9 |" \
    "$tmp/pkg-$OLD_VER/BUNDLE.manifest"
  write_sums "$tmp/pkg-$OLD_VER"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "BUNDLE.manifest"
  releases_untouched
)

c_install_manifest_license_refused() (
  prepare_fresh
  sed -i "s|^image images/api.tar \(.*\) LicenseRef-Homayar-Proprietary$|image images/api.tar \1 Bad Licence|" \
    "$tmp/pkg-$OLD_VER/BUNDLE.manifest"
  write_sums "$tmp/pkg-$OLD_VER"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "BUNDLE.manifest"
  releases_untouched
)

c_install_manifest_model_missing_line_refused() (
  prepare_fresh
  mkdir -p "$tmp/pkg-$OLD_VER/models/asr"
  printf 'an unlisted model\n' >"$tmp/pkg-$OLD_VER/models/asr/extra.bin"
  write_sums "$tmp/pkg-$OLD_VER"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "BUNDLE.manifest"
  releases_untouched
)

c_install_manifest_line_missing_file_refused() (
  prepare_fresh
  printf 'model models/gone.bin %s MIT gone.model\n' "$(printf 'a%.0s' {1..64})" \
    >>"$tmp/pkg-$OLD_VER/BUNDLE.manifest"
  write_sums "$tmp/pkg-$OLD_VER"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "BUNDLE.manifest"
  releases_untouched
)

c_install_manifest_unknown_record_refused() (
  prepare_fresh
  printf 'frobnicate one two\n' >>"$tmp/pkg-$OLD_VER/BUNDLE.manifest"
  write_sums "$tmp/pkg-$OLD_VER"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "BUNDLE.manifest"
  releases_untouched
)

c_install_manifest_no_requires_refused() (
  prepare_fresh
  sed -i '/^requires /d' "$tmp/pkg-$OLD_VER/BUNDLE.manifest"
  write_sums "$tmp/pkg-$OLD_VER"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "BUNDLE.manifest"
  releases_untouched
)

c_install_unlisted_sig2_refused() (
  prepare_fresh
  cp "$tmp/pkg-$OLD_VER/SHA256SUMS.sig" "$tmp/pkg-$OLD_VER/SHA256SUMS.sig2"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "unlisted file: SHA256SUMS.sig2"
  releases_untouched
  prepare_fresh
  cp "$PUB" "$tmp/pkg-$OLD_VER/extra.pem"
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 2
  want_out "unlisted file: extra.pem"
  releases_untouched
)

c_install_rotation_second_key() (
  prepare_fresh
  cp "$ALT_PUB" "$TRUST/rotation-new-key.pem"
  openssl pkeyutl -sign -inkey "$ALT_KEY" -rawin -in "$tmp/pkg-$OLD_VER/SHA256SUMS" \
    -out "$tmp/pkg-$OLD_VER/SHA256SUMS.sig" 2>/dev/null
  run_script install --package "$tmp/pkg-$OLD_VER"
  want_rc 0
  current_is "$OLD_VER"
  want_out "fingerprint $ALT_FINGERPRINT"
  want_no_secret
)

c_upgrade_unsigned_refused() (
  prepare
  rm -f "$tmp/pkg-$NEW_VER/SHA256SUMS.sig"
  run_script upgrade --package "$tmp/pkg-$NEW_VER"
  want_rc 2
  want_out "signature"
  want_out "Nothing was changed."
  current_is "$OLD_VER"
  calls_lack "compose:stop"
  calls_lack "compose:exec:postgres:pg_dump"
  want_no_secret
)

c_upgrade_untrusted_key_refused() (
  prepare
  openssl pkeyutl -sign -inkey "$ALT_KEY" -rawin -in "$tmp/pkg-$NEW_VER/SHA256SUMS" \
    -out "$tmp/pkg-$NEW_VER/SHA256SUMS.sig" 2>/dev/null
  run_script upgrade --package "$tmp/pkg-$NEW_VER"
  want_rc 2
  want_out "signature"
  current_is "$OLD_VER"
  calls_lack "compose:stop"
  calls_lack "compose:exec:postgres:pg_dump"
  want_no_secret
)

c_upgrade_tamper_rewritten_refused() (
  prepare
  printf 'tampered\n' >>"$tmp/pkg-$NEW_VER/infra/nginx/homayar.conf"
  rewrite_sums_unsigned "$tmp/pkg-$NEW_VER"
  run_script upgrade --package "$tmp/pkg-$NEW_VER"
  want_rc 2
  want_out "signature"
  current_is "$OLD_VER"
  calls_lack "compose:stop"
  calls_lack "compose:exec:postgres:pg_dump"
  want_no_secret
)

c_upgrade_manifest_broken_refused() (
  prepare
  sed -i '/^requires /d' "$tmp/pkg-$NEW_VER/BUNDLE.manifest"
  write_sums "$tmp/pkg-$NEW_VER"
  run_script upgrade --package "$tmp/pkg-$NEW_VER"
  want_rc 2
  want_out "BUNDLE.manifest"
  current_is "$OLD_VER"
  calls_lack "compose:stop"
  calls_lack "compose:exec:postgres:pg_dump"
  want_no_secret
)

# --- run all cases -------------------------------------------------------------------

run_case c_usage_no_command
run_case c_usage_unknown_command
run_case c_usage_unknown_flag
run_case c_pack_ok
run_case c_pack_dirty_tree
run_case c_pack_refuses_nonempty_output
run_case c_install_happy
run_case c_install_migrate_fails_retry
run_case c_install_from_tar
run_case c_install_refused_when_current_exists
run_case c_pkg_tampered_refused
run_case c_pkg_extra_file_refused
run_case c_pkg_missing_file_refused
run_case c_pkg_dotdot_path_refused
run_case c_pkg_bad_release_env_refused
run_case c_pkg_latest_tag_refused
run_case c_upgrade_happy
run_case c_upgrade_already_on_version
run_case c_upgrade_same_version_other_sha
run_case c_upgrade_stale_gauge_refused
run_case c_upgrade_missing_gauge_refused
run_case c_upgrade_skip_backup_check
run_case c_upgrade_gauge_23h_proceeds
run_case c_upgrade_migrate_fails_rolls_back
run_case c_upgrade_rollback_fails_exit_3
run_case c_upgrade_dump_fails_writers_restarted
run_case c_rollback_wrong_timestamp
run_case c_rollback_ok
run_case c_rollback_unknown_state
run_case c_lock_held
run_case c_preflight_ok
run_case c_preflight_exit_code_passed_through
run_case c_support_bundle_ok
run_case c_support_bundle_finding_fails_closed
run_case c_support_bundle_scan_error_fails_closed
run_case c_status
run_case c_status_license_unreadable
run_case c_backup_execs_release_script
run_case c_homyar_home_env_respected
run_case c_http_port_from_operator_env
run_case c_http_port_quoted_in_operator_env
run_case c_upgrade_load_fails_then_retry
run_case c_upgrade_db_unreadable_restores_dump
run_case c_upgrade_db_unreadable_restore_fails_exit_3
run_case c_rollback_image_tag_moved
run_case c_pkg_symlink_refused
run_case c_pkg_multislash_image_ref_ok
run_case c_pkg_no_slash_image_ref_ok
run_case c_skip_reason_control_char_refused
run_case c_support_bundle_logs_empty_count_zero
run_case c_support_bundle_scan_crash_fails_closed
# HOM-107
run_case c_pack_key_from_env
run_case c_pack_no_key_refused
run_case c_pack_key_mode_refused
run_case c_pack_key_not_ed25519_refused
run_case c_pack_key_missing_file_refused
run_case c_pack_models_included
run_case c_pack_model_sha_mismatch_refused
run_case c_pack_model_dir_artifact_refused
run_case c_pack_model_no_license_refused
run_case c_pack_model_dotdot_path_refused
run_case c_pack_model_flags_half_refused
run_case c_pack_bundle_conf_missing_refused
run_case c_pack_bundle_conf_license_missing_refused
run_case c_pack_bundle_conf_no_requires_refused
run_case c_pack_nested_sums_name_listed_ok
run_case c_pack_model_duplicate_path_refused
run_case c_install_unsigned_refused
run_case c_install_untrusted_key_refused
run_case c_install_sig_bad_refused
run_case c_install_tamper_rewritten_refused
run_case c_install_trust_missing_refused
run_case c_install_trust_rsa_pem_refused
run_case c_install_trust_private_key_pem_refused
run_case c_install_trust_not_regular_pem_refused
run_case c_install_manifest_missing_refused
run_case c_install_manifest_image_hash_refused
run_case c_install_manifest_ref_mismatch_refused
run_case c_install_manifest_license_refused
run_case c_install_manifest_model_missing_line_refused
run_case c_install_manifest_line_missing_file_refused
run_case c_install_manifest_unknown_record_refused
run_case c_install_manifest_no_requires_refused
run_case c_install_unlisted_sig2_refused
run_case c_install_rotation_second_key
run_case c_upgrade_unsigned_refused
run_case c_upgrade_untrusted_key_refused
run_case c_upgrade_tamper_rewritten_refused
run_case c_upgrade_manifest_broken_refused

printf '%d passed, %d failed\n' "$passed" "$failed"
[[ "$failed" -eq 0 ]]
