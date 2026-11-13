#!/usr/bin/env bash
# HOM-107 oracle: signed offline bundles in infra/scripts/homayar-onprem.sh.
# Run from the repo root (CI, or python:3.12-slim with the worktree mounted
# read-only). docker, curl and git are stubs first on PATH; openssl is real
# and every key is a throwaway made in the temp folder at run time.
set -Eu
export LC_ALL=C

WORK="$(pwd -P)"
SUBJECT="${ONPREM_SUBJECT:-infra/scripts/homayar-onprem.sh}"
case "$SUBJECT" in
  /*) ;;
  *) SUBJECT="$WORK/$SUBJECT" ;;
esac

T="$(mktemp -d "${TMPDIR:-/tmp}/onprem-signing.XXXXXX")"
trap 'rm -rf -- "${T:?}"' EXIT

BIN="$T/bin"
PY="$T/py"
STUB="$T/stub"
HOMEF="$T/home"
TRUST="$HOMEF/config/bundle-keys"
PKGS="$T/pkgs"
RUNTMP="$T/runtmp"
OUTS="$T/outs"
RUNCWD="$T/cwd"
KEYS="$T/keys"
SRC="$T/src"
GAUGE="$T/gauge"
MROOT="$T/model-root"
MANS="$T/model-manifests"
PACKS="$T/packs"
MARKER="ZzOnpremKrit42"
BASE_PATH="$PATH"
mkdir -p "$BIN" "$PY" "$PKGS" "$OUTS" "$RUNCWD" "$STUB" "$KEYS" "$MROOT" "$MANS" "$PACKS"

rep() {
  local s="" i
  for ((i = 0; i < $2; i++)); do s+="$1"; done
  printf '%s' "$s"
}

V5=0.5.0
V6=0.6.0
V7=0.7.0
SHA5="$(rep 5a 20)"
SHA5X="$(rep 5b 20)"
SHA6="$(rep 6b 20)"
SHA7="$(rep 7c 20)"
H5=a1b2c3d4e5f6
H6=0f1e2d3c4b5a
H7=7e7e7e7e7e70
IDA5="sha256:$(rep 50 32)"
IDC5="sha256:$(rep 51 32)"
IDE5="sha256:$(rep 52 32)"
IDA6="sha256:$(rep 60 32)"
IDC6="sha256:$(rep 61 32)"
IDE6="sha256:$(rep 62 32)"
IDA7="sha256:$(rep 70 32)"
IDC7="sha256:$(rep 71 32)"
IDE7="sha256:$(rep 72 32)"
LIC=LicenseRef-Homayar-Proprietary
STUB_WARN='hash-checked, not signed'
USAGE_LINE='usage: bash homayar-onprem.sh'

if ! openssl genpkey -algorithm ed25519 -out "$T/probe.pem" >/dev/null 2>&1; then
  printf 'FAIL setup: a real openssl with Ed25519 support is required\n0 passed, 1 failed\n'
  exit 1
fi
rm -f -- "$T/probe.pem"

umask 077
for k in k1 k2 ku; do
  openssl genpkey -algorithm ed25519 -out "$KEYS/$k.pem" 2>/dev/null
  openssl pkey -in "$KEYS/$k.pem" -pubout -out "$KEYS/$k.pub.pem" 2>/dev/null
done
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$KEYS/rsa.pem" 2>/dev/null
openssl pkey -in "$KEYS/rsa.pem" -pubout -out "$KEYS/rsa.pub.pem" 2>/dev/null
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$KEYS/ec.pem" 2>/dev/null
openssl pkey -in "$KEYS/ec.pem" -pubout -out "$KEYS/ec.pub.pem" 2>/dev/null
umask 022
for k in k1 k2 ku rsa ec; do
  if [[ ! -s "$KEYS/$k.pem" || ! -s "$KEYS/$k.pub.pem" ]]; then
    printf 'FAIL setup: openssl could not make the throwaway %s key\n0 passed, 1 failed\n' "$k"
    exit 1
  fi
done
chmod 600 "$KEYS"/*.pem
K1="$KEYS/k1.pem"
K2="$KEYS/k2.pem"
KU="$KEYS/ku.pem"
PUB1="$KEYS/k1.pub.pem"
PUB2="$KEYS/k2.pub.pem"
PUBU="$KEYS/ku.pub.pem"
for m in 0644 0640 0604 0400; do
  cp -p "$K1" "$KEYS/k1-$m.pem"
  chmod "$m" "$KEYS/k1-$m.pem"
done
mkdir -p "$KEYS/keydir"
chmod 700 "$KEYS/keydir"

fp_of() { openssl pkey -pubin -in "$1" -outform DER 2>/dev/null | sha256sum | cut -c1-16; }
FP1="$(fp_of "$PUB1")"
FP2="$(fp_of "$PUB2")"
FPU="$(fp_of "$PUBU")"

cat >"$PY/leakscan.py" <<'PYEOF'
import base64
import os
import sys

marker, keydir = sys.argv[1], sys.argv[2]
pats = [("the text PRIVATE KEY", b"PRIVATE KEY"), ("the operator env marker", marker.encode())]
ed_prefix = bytes.fromhex("302e020100300506032b657004220420")
for name in sorted(os.listdir(keydir)):
    path = os.path.join(keydir, name)
    if not name.endswith(".pem") or not os.path.isfile(path):
        continue
    with open(path, encoding="ascii") as fh:
        text = fh.read()
    if "PRIVATE KEY" not in text:
        continue
    body = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("-----")]
    for ln in body:
        if len(ln) >= 40:
            pats.append((name + " PEM body", ln.encode()))
    der = base64.b64decode("".join(body))
    pats.append((name + " DER bytes", der))
    if len(der) == 48 and der.startswith(ed_prefix):
        seed = der[16:]
        pats.append((name + " raw seed", seed))
        pats.append((name + " seed hex", seed.hex().encode()))
        pats.append((name + " seed HEX", seed.hex().upper().encode()))
        pats.append((name + " seed colon hex", ":".join("%02x" % b for b in seed[:15]).encode()))
for root in sys.argv[3:]:
    files = []
    if os.path.isdir(root) and not os.path.islink(root):
        for folder, _, names in os.walk(root):
            files.extend(os.path.join(folder, n) for n in names)
    elif os.path.isfile(root):
        files.append(root)
    for f in files:
        if os.path.islink(f) or not os.path.isfile(f):
            continue
        with open(f, "rb") as fh:
            data = fh.read()
        for label, pat in pats:
            if pat in data:
                print("%s in %s" % (label, f))
                sys.exit(1)
PYEOF

cat >"$PY/mkmodelman.py" <<'PYEOF'
import json
import sys

out, mid, lic = sys.argv[1], sys.argv[2], sys.argv[3]
arts = []
for spec in sys.argv[4:]:
    kind, sha, path = spec.split(":", 2)
    arts.append({"path": path, "kind": kind, "sha256": sha})
entry = {"manifest_id": mid, "version": 1, "stage": "asr", "status": "qualified"}
if lic != "@absent":
    entry["license"] = lic
entry["license_policy"] = "approved"
entry["offline_capable"] = True
entry["artifacts"] = arts
with open(out, "w", encoding="utf-8") as fh:
    json.dump({"schema": "homayar.model-manifests/1", "manifests": [entry]}, fh, indent=2)
    fh.write("\n")
PYEOF

{
  printf '#!/usr/bin/env bash\n'
  printf 'S=%q\n' "$STUB"
  cat <<'DOCKEREOF'
set -u
ev() { printf '%s\n' "$*" >>"$S/calls"; }
img_row() { [[ -f "$S/images" ]] && awk -F'|' -v r="$1" '$1 == r || $2 == r { print; exit }' "$S/images"; }
imgs_tag() { [[ -f "$S/imgs" ]] && awk -F'|' -v s="$1" '$1 == s { print $2; exit }' "$S/imgs"; }
rel_env() { sed -n "s/^$1=//p" ./release.env 2>/dev/null | head -n 1; }
set_svc() {
  if [[ -s "$S/svc" ]] && grep -q "^$1|" "$S/svc"; then
    awk -F'|' -v s="$1" -v v="$2" '$1 == s { print s "|" v; next } { print }' "$S/svc" >"$S/svc.new" && mv "$S/svc.new" "$S/svc"
  else
    printf '%s|%s\n' "$1" "$2" >>"$S/svc"
  fi
}
cmd="${1:-}"
shift || true
case "$cmd" in
  --version | version)
    echo "Docker version 27.3.1, build oracle"
    exit 0
    ;;
  info)
    ev "info"
    echo /var/lib/docker
    exit 0
    ;;
  image)
    [[ "${1:-}" == inspect ]] || { ev "image-unknown $*"; exit 1; }
    shift
    ref=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --format | -f) shift 2 ;;
        *) ref="$1"; shift ;;
      esac
    done
    row="$(img_row "$ref")"
    [[ -n "$row" ]] || { echo "Error: No such image: $ref" >&2; exit 1; }
    cut -d'|' -f2 <<<"$row"
    exit 0
    ;;
  inspect)
    ref=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --format | -f) shift 2 ;;
        *) ref="$1"; shift ;;
      esac
    done
    row="$(img_row "$(imgs_tag "${ref#cid-}")")"
    [[ -n "$row" ]] || { echo "Error: No such object: $ref" >&2; exit 1; }
    cut -d'|' -f2 <<<"$row"
    exit 0
    ;;
  load)
    f=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --input | -i) f="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    [[ -n "$f" && -f "$f" ]] || { echo "requested load from stdin, but stdin is empty" >&2; exit 1; }
    row="$(head -n 1 "$f")"
    ev "load ${row%%|*}"
    printf '%s\n' "$row" >>"$S/images"
    echo "Loaded image: ${row%%|*}"
    exit 0
    ;;
  save)
    f=""
    ref=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --output | -o) f="$2"; shift 2 ;;
        *) ref="$1"; shift ;;
      esac
    done
    row="$(img_row "$ref")"
    [[ -n "$row" && -n "$f" ]] || { echo "Error: No such image: $ref" >&2; exit 1; }
    ev "save $ref"
    printf '%s\n' "$row" >"$f"
    exit 0
    ;;
  run)
    ep=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --rm) shift ;;
        --network | --volume | --user | -v | -u) shift 2 ;;
        --entrypoint) ep="$2"; shift 2 ;;
        *) break ;;
      esac
    done
    tag="${1:-}"
    shift || true
    if [[ "$ep" == python && "$*" == *migration_head* ]]; then
      row="$(img_row "$tag")"
      [[ -n "$row" ]] || { echo "Unable to find image '$tag' locally" >&2; exit 125; }
      ev "head $tag"
      cut -d'|' -f5 <<<"$row"
      exit 0
    fi
    ev "run-unknown $tag $*"
    exit 1
    ;;
  compose)
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --env-file | --file | -f | --profile | --project-directory | -p) shift 2 ;;
        *) break ;;
      esac
    done
    sub="${1:-}"
    shift || true
    case "$sub" in
      version)
        echo "Docker Compose version v2.29.7"
        exit 0
        ;;
      config)
        ev "config $PWD"
        exit 0
        ;;
      ps)
        q=0
        svc=""
        fmt=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --all | -a) shift ;;
            --format) fmt="$2"; shift 2 ;;
            -q | --quiet) q=1; shift ;;
            *) svc="$1"; shift ;;
          esac
        done
        if [[ "$q" == 1 ]]; then echo "cid-${svc:-all}"; exit 0; fi
        [[ -f "$S/svc" ]] || exit 0
        while IFS='|' read -r a b c d; do
          line="$fmt"
          line="${line//'{{.Service}}'/$a}"
          line="${line//'{{.Name}}'/$a}"
          line="${line//'{{.State}}'/$b}"
          line="${line//'{{.Health}}'/$c}"
          line="${line//'{{.ExitCode}}'/$d}"
          printf '%s\n' "$line"
        done <"$S/svc"
        exit 0
        ;;
      stop)
        ev "stop $*"
        for s in "$@"; do set_svc "$s" "exited||0"; done
        exit 0
        ;;
      up)
        ev "up $PWD"
        printf '%s\n' "$PWD" >>"$S/up-from"
        api="$(rel_env API_IMAGE)"
        cw="$(rel_env CONSOLE_WEB_IMAGE)"
        eg="$(rel_env EGRESS_PROXY_IMAGE)"
        for t in "$api" "$cw" "$eg"; do
          [[ -n "$(img_row "$t")" ]] || { echo "pull access denied for $t" >&2; exit 1; }
        done
        for s in proxy console-web api worker postgres redis minio; do set_svc "$s" "running|healthy|0"; done
        set_svc scheduler "running||0"
        set_svc minio-bootstrap "exited||0"
        printf 'api|%s\nworker|%s\nscheduler|%s\nconsole-web|%s\n' "$api" "$api" "$api" "$cw" >"$S/imgs"
        exit 0
        ;;
      exec)
        while [[ "${1:-}" == -* ]]; do shift; done
        svc="${1:-}"
        shift || true
        [[ "${1:-}" == sh && "${2:-}" == -c ]] || { ev "exec-unknown $svc $*"; exit 1; }
        p="${3:-}"
        case "$p" in
          *pg_dump*)
            ev "pg_dump"
            cp "$S/db-version" "$S/dump-version"
            echo "PGDMP-oracle-dump"
            exit 0
            ;;
          *'pg_restore --list'*)
            cat >/dev/null
            echo "321; 1259 16401 TABLE DATA public alembic_version homayar"
            exit 0
            ;;
          *pg_restore*homayar_restorecheck*)
            cat >/dev/null
            cp "$S/dump-version" "$S/scratch-version"
            exit 0
            ;;
          *homayar_restorecheck*createdb*) exit 0 ;;
          *dropdb*homayar_restorecheck*) exit 0 ;;
          *pg_restore*)
            ev "pg_restore-live"
            cat >/dev/null
            cp "$S/dump-version" "$S/db-version"
            exit 0
            ;;
          *dropdb*)
            ev "db-reset"
            exit 0
            ;;
          *alembic_version*)
            if [[ "$p" == *homayar_restorecheck* ]]; then cat "$S/scratch-version"; else cat "$S/db-version"; fi
            exit 0
            ;;
          *'count(*)'*)
            echo 42
            exit 0
            ;;
          *)
            ev "exec-unknown $p"
            exit 1
            ;;
        esac
        ;;
      run)
        while [[ "${1:-}" == -* ]]; do shift; done
        rsvc="${1:-}"
        shift || true
        if [[ "$rsvc" == migrate ]]; then
          ev "migrate $PWD"
          row="$(img_row "$(rel_env API_IMAGE)")"
          cut -d'|' -f5 <<<"$row" >"$S/db-version"
          exit 0
        fi
        case "$*" in
          *model_preflight*)
            ev "preflight $PWD"
            echo '{"ready": true}'
            exit 0
            ;;
        esac
        ev "run-unknown $rsvc $*"
        exit 1
        ;;
      logs) exit 0 ;;
      *)
        ev "compose-unknown $sub $*"
        exit 1
        ;;
    esac
    ;;
  *)
    ev "unknown $cmd $*"
    exit 1
    ;;
esac
DOCKEREOF
} >"$BIN/docker"

{
  printf '#!/usr/bin/env bash\n'
  printf 'S=%q\n' "$STUB"
  cat <<'CURLEOF'
set -u
out=/dev/null
url=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -w | --max-time | -m) shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
svc_ok() { [[ -f "$S/svc" ]] && grep -q "^$1|running" "$S/svc"; }
code=000
case "$url" in
  http://*/healthz)
    if svc_ok proxy; then code=200; printf '{"status":"ok"}' >"$out"; fi
    ;;
  http://*/readyz)
    if svc_ok proxy && svc_ok api; then code=200; printf '{"status":"ready"}' >"$out"; fi
    ;;
esac
printf '%s' "$code"
exit 0
CURLEOF
} >"$BIN/curl"

{
  printf '#!/usr/bin/env bash\n'
  printf 'S=%q\n' "$STUB"
  cat <<'GITEOF'
set -u
printf 'git %s\n' "$*" >>"$S/calls"
dir="$PWD"
while [[ $# -gt 0 ]]; do
  case "$1" in
    -C) dir="$2"; shift 2 ;;
    -c) shift 2 ;;
    --*) shift ;;
    *) break ;;
  esac
done
sc="${1:-}"
[[ $# -gt 0 ]] && shift
[[ -f "$dir/.oracle-git-head" ]] || { echo "fatal: not a git repository" >&2; exit 128; }
sha="$(cat "$dir/.oracle-git-head")"
case "$sc" in
  rev-parse)
    case " $* " in
      *--show-toplevel*) printf '%s\n' "$dir" ;;
      *--short*) printf '%s\n' "${sha:0:12}" ;;
      *) printf '%s\n' "$sha" ;;
    esac
    ;;
  show | cat-file)
    for a in "$@"; do
      case "$a" in
        *:*)
          p="${a#*:}"
          [[ -f "$dir/$p" ]] || { echo "fatal: path '$p' does not exist in 'HEAD'" >&2; exit 128; }
          cat "$dir/$p"
          exit 0
          ;;
      esac
    done
    printf '%s\n' "$sha"
    ;;
esac
exit 0
GITEOF
} >"$BIN/git"
chmod 755 "$BIN"/*

fsha() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }
relv() { sed -n "s/^$2=//p" "$1/release.env" 2>/dev/null | head -n 1; }
img_row() { printf '%s|%s|%s|%s|%s\n' "$1" "$2" "$3" "$4" "$5"; }

REQS="$T/requires.txt"
printf 'requires bash >=4.4\nrequires openssl >=3.0\nrequires oracle-dep-canary >=9.9.9\n' >"$REQS"

write_tree() {
  local d="$1" tag="$2"
  mkdir -p "$d/infra/compose" "$d/infra/scripts" "$d/infra/nginx"
  printf 'name: homayar-onprem\n# release %s\nservices:\n  api:\n    image: "${API_IMAGE:?}"\n' "$tag" \
    >"$d/infra/compose/compose.onprem.yaml"
  printf '#!/usr/bin/env bash\necho backup-stub "$@"\n' >"$d/infra/scripts/backup.sh"
  printf '#!/usr/bin/env bash\necho restore-stub "$@"\n' >"$d/infra/scripts/restore-isolated.sh"
  chmod 755 "$d/infra/scripts/backup.sh" "$d/infra/scripts/restore-isolated.sh"
  printf 'server_name homayar; # %s\n' "$tag" >"$d/infra/nginx/homayar.conf"
}

make_base() {
  local d="$1" v="$2" sha="$3" head="$4"
  rm -rf -- "${d:?}"
  mkdir -p "$d/images"
  write_tree "$d" "$v"
  img_row "homayar/api:$v" "$5" "$sha" "$v" "$head" >"$d/images/api.tar"
  img_row "homayar/console-web:$v" "$6" "$sha" "$v" "$head" >"$d/images/console-web.tar"
  img_row "homayar/egress-proxy:$v" "$7" "$sha" "$v" "$head" >"$d/images/egress-proxy.tar"
  printf 'HOMAYAR_RELEASE_VERSION=%s\nHOMAYAR_RELEASE_SHA=%s\nAPI_IMAGE=homayar/api:%s\nCONSOLE_WEB_IMAGE=homayar/console-web:%s\nEGRESS_PROXY_IMAGE=homayar/egress-proxy:%s\n' \
    "$v" "$sha" "$v" "$v" "$v" >"$d/release.env"
}

write_manifest() {
  local d="$1" la="${2:-$LIC}" lc="${3:-$LIC}" le="${4:-$LIC}" p
  {
    printf 'schema homayar-offline-bundle/1\n'
    printf 'release %s %s\n' "$(relv "$d" HOMAYAR_RELEASE_VERSION)" "$(relv "$d" HOMAYAR_RELEASE_SHA)"
    printf 'image images/api.tar %s %s %s\n' "$(fsha "$d/images/api.tar")" "$(relv "$d" API_IMAGE)" "$la"
    printf 'image images/console-web.tar %s %s %s\n' "$(fsha "$d/images/console-web.tar")" "$(relv "$d" CONSOLE_WEB_IMAGE)" "$lc"
    printf 'image images/egress-proxy.tar %s %s %s\n' "$(fsha "$d/images/egress-proxy.tar")" "$(relv "$d" EGRESS_PROXY_IMAGE)" "$le"
    if [[ -d "$d/models" ]]; then
      while IFS= read -r p; do
        printf 'model %s %s Apache-2.0 asr.oracle-demo\n' "$p" "$(fsha "$d/$p")"
      done < <(cd "$d" && find models -type f | LC_ALL=C sort)
    fi
    cat "$REQS"
  } >"$d/BUNDLE.manifest"
}

write_sums() {
  (cd "$1" && find . -type f ! -path ./SHA256SUMS ! -path ./SHA256SUMS.sig -printf '%P\0' | LC_ALL=C sort -z |
    xargs -0 sha256sum --) >"$T/sums.tmp"
  mv -f "$T/sums.tmp" "$1/SHA256SUMS"
}

sign() {
  rm -f -- "$1/SHA256SUMS.sig"
  openssl pkeyutl -sign -inkey "$2" -rawin -in "$1/SHA256SUMS" -out "$1/SHA256SUMS.sig" 2>/dev/null
}

seal() {
  write_sums "$1"
  sign "$1" "$2"
}

copy_of() {
  local d="$PKGS/$1"
  rm -rf -- "${d:?}"
  cp -R -p "$2" "$d"
  printf '%s' "$d"
}

medit() {
  local d
  d="$(copy_of "$1" "$2")"
  sed -i -e "$3" "$d/BUNDLE.manifest"
  seal "$d" "$K1"
  printf '%s' "$d"
}

mktar() {
  tar -cf "$PKGS/$1.tar" -C "$2" .
  printf '%s' "$PKGS/$1.tar"
}

PKG5="$PKGS/p050"
make_base "$PKG5" "$V5" "$SHA5" "$H5" "$IDA5" "$IDC5" "$IDE5"
write_manifest "$PKG5"
seal "$PKG5" "$K1"
PKG6="$PKGS/p060"
make_base "$PKG6" "$V6" "$SHA6" "$H6" "$IDA6" "$IDC6" "$IDE6"
write_manifest "$PKG6"
seal "$PKG6" "$K1"

PKG5_TAR="$(mktar p050 "$PKG5")"
PKG5_K2="$(copy_of p050-k2 "$PKG5")"
sign "$PKG5_K2" "$K2"
PKG5_KU="$(copy_of p050-ku "$PKG5")"
sign "$PKG5_KU" "$KU"
PKG5_KU_TAR="$(mktar p050-ku "$PKG5_KU")"
PKG5_UNSIGNED="$(copy_of p050-unsigned "$PKG5")"
rm -f -- "$PKG5_UNSIGNED/SHA256SUMS.sig"
PKG5_UNSIGNED_TAR="$(mktar p050-unsigned "$PKG5_UNSIGNED")"
PKG5_HOM110="$(copy_of p050-hom110 "$PKG5")"
rm -f -- "$PKG5_HOM110/SHA256SUMS.sig" "$PKG5_HOM110/BUNDLE.manifest"
write_sums "$PKG5_HOM110"
PKG5_KU_PEM="$(copy_of p050-ku-pem "$PKG5")"
mkdir -p "$PKG5_KU_PEM/bundle-keys" "$PKG5_KU_PEM/config/bundle-keys"
cp "$PUBU" "$PKG5_KU_PEM/bundle-keys/oracle-shipped.pem"
cp "$PUBU" "$PKG5_KU_PEM/config/bundle-keys/oracle-shipped.pem"
seal "$PKG5_KU_PEM" "$KU"

PKG5_SIG63="$(copy_of p050-sig63 "$PKG5")"
head -c 63 "$PKG5/SHA256SUMS.sig" >"$PKG5_SIG63/SHA256SUMS.sig"
PKG5_SIG65="$(copy_of p050-sig65 "$PKG5")"
printf '\n' >>"$PKG5_SIG65/SHA256SUMS.sig"
PKG5_SIG0="$(copy_of p050-sig0 "$PKG5")"
: >"$PKG5_SIG0/SHA256SUMS.sig"
PKG5_SIGRND="$(copy_of p050-sigrnd "$PKG5")"
head -c 64 /dev/urandom >"$PKG5_SIGRND/SHA256SUMS.sig"

PKG5_REWRITE="$(copy_of p050-rewrite "$PKG5")"
printf '# changed after signing\n' >>"$PKG5_REWRITE/infra/compose/compose.onprem.yaml"
write_sums "$PKG5_REWRITE"
PKG5_REWRITE_TAR="$(mktar p050-rewrite "$PKG5_REWRITE")"
PKG5_SWAPREF="$(copy_of p050-swapref "$PKG5")"
sed -i "s|^API_IMAGE=.*|API_IMAGE=evil.example/api:$V5|" "$PKG5_SWAPREF/release.env"
write_manifest "$PKG5_SWAPREF"
write_sums "$PKG5_SWAPREF"

PKG5_SIG2="$(copy_of p050-sig2 "$PKG5")"
cp "$PKG5/SHA256SUMS.sig" "$PKG5_SIG2/SHA256SUMS.sig2"
PKG5_EXTRAPEM="$(copy_of p050-extrapem "$PKG5")"
cp "$PUB1" "$PKG5_EXTRAPEM/extra.pem"
PKG5_SUBSIG="$(copy_of p050-subsig "$PKG5")"
cp "$PKG5/SHA256SUMS.sig" "$PKG5_SUBSIG/infra/SHA256SUMS.sig"
PKG5_LOWERSIG="$(copy_of p050-lowersig "$PKG5")"
cp "$PKG5/SHA256SUMS.sig" "$PKG5_LOWERSIG/sha256sums.sig"

PKG5_IMGTAMPER="$(copy_of p050-imgtamper "$PKG5")"
printf 'x' >>"$PKG5_IMGTAMPER/images/api.tar"
PKG5_DOTDOT="$(copy_of p050-dotdot "$PKG5")"
printf 'outside the package\n' >"$PKGS/outside.txt"
printf '%s  ../outside.txt\n' "$(fsha "$PKGS/outside.txt")" >>"$PKG5_DOTDOT/SHA256SUMS"
sign "$PKG5_DOTDOT" "$K1"
PKG5_SYMLINK="$(copy_of p050-symlink "$PKG5")"
ln -s ../release.env "$PKG5_SYMLINK/infra/link"
PKG5_LATEST="$(copy_of p050-latest "$PKG5")"
sed -i "s|^API_IMAGE=.*|API_IMAGE=homayar/api:latest|" "$PKG5_LATEST/release.env"
write_manifest "$PKG5_LATEST"
seal "$PKG5_LATEST" "$K1"

PKG5_LICS="$(copy_of p050-licences "$PKG5")"
write_manifest "$PKG5_LICS" "GPL-2.0+" "MIT" "LicenseRef-Oracle.Test-1"
seal "$PKG5_LICS" "$K1"

PKG5M="$(copy_of p050-models "$PKG5")"
mkdir -p "$PKG5M/models/asr" "$PKG5M/manifests"
printf 'oracle asr weights in the package\n' >"$PKG5M/models/asr/model.bin"
printf '{"oracle": "tokenizer in the package"}\n' >"$PKG5M/models/asr/tokenizer.json"
python3 -I "$PY/mkmodelman.py" "$PKG5M/manifests/oracle-asr.json" asr.oracle-demo Apache-2.0 \
  "file:$(fsha "$PKG5M/models/asr/model.bin"):asr/model.bin" \
  "file:$(fsha "$PKG5M/models/asr/tokenizer.json"):asr/tokenizer.json"
write_manifest "$PKG5M"
seal "$PKG5M" "$K1"

CWH="$(fsha "$PKG5/images/console-web.tar")"
TKH="$(fsha "$PKG5M/models/asr/tokenizer.json")"
M_NONE="$(copy_of m-none "$PKG5")"
rm -f -- "$M_NONE/BUNDLE.manifest"
seal "$M_NONE" "$K1"
M_SCHEMA2="$(medit m-schema2 "$PKG5" '1s|.*|schema homayar-offline-bundle/2|')"
M_SCHEMA_SECOND="$(medit m-schema-second "$PKG5" '1{h;d};2G')"
M_REL_SHA="$(medit m-rel-sha "$PKG5" "s|^release .*|release $V5 $SHA5X|")"
M_REL_DUP="$(medit m-rel-dup "$PKG5" '2p')"
M_REL_NONE="$(medit m-rel-none "$PKG5" '/^release /d')"
M_IMG_HASH="$(medit m-img-hash "$PKG5" "s|^\\(image images/api\\.tar\\) [0-9a-f]* |\\1 $CWH |")"
M_IMG_REF="$(medit m-img-ref "$PKG5" "s|^\\(image images/api\\.tar [0-9a-f]*\\) homayar/api:$V5 |\\1 homayar/api:0.4.9 |")"
M_IMG_SWAP="$(medit m-img-swap "$PKG5" "s| homayar/api:$V5 | ORACLESWAP |; s| homayar/console-web:$V5 | homayar/api:$V5 |; s| ORACLESWAP | homayar/console-web:$V5 |")"
M_LIC_NONE="$(medit m-lic-none "$PKG5" 's|^\(image images/api\.tar [^ ]* [^ ]*\) [^ ]*$|\1|')"
M_LIC_BAD="$(medit m-lic-bad "$PKG5" 's|^\(image images/api\.tar [^ ]* [^ ]*\) [^ ]*$|\1 Apache_2.0|')"
M_FIELD_EXTRA="$(medit m-field-extra "$PKG5" '/^image images\/api\.tar /s|$| oracle-extra|')"
M_IMG_DUP="$(medit m-img-dup "$PKG5" '/^image images\/api\.tar /p')"
M_IMG_GHOST="$(medit m-img-ghost "$PKG5" "/^image images\\/egress-proxy\\.tar /a image images/oracle-ghost.tar $(rep 0e 32) homayar/oracle-ghost:$V5 $LIC")"
M_IMG_UNLINED="$(copy_of m-img-unlined "$PKG5")"
img_row "homayar/oracle-extra:$V5" "sha256:$(rep 53 32)" "$SHA5" "$V5" "$H5" >"$M_IMG_UNLINED/images/oracle-extra.tar"
seal "$M_IMG_UNLINED" "$K1"
M_UNKNOWN="$(medit m-unknown "$PKG5" '$a note oracle-canary')"
M_COMMENT="$(medit m-comment "$PKG5" '$a # oracle comment')"
M_EMPTY="$(medit m-empty "$PKG5" '2G')"
M_REQ_NONE="$(medit m-req-none "$PKG5" '/^requires /d')"
M_MOD_UNLINED="$(medit m-mod-unlined "$PKG5M" '/^model models\/asr\/tokenizer\.json /d')"
M_MOD_GHOST="$(medit m-mod-ghost "$PKG5M" "/^model models\\/asr\\/tokenizer\\.json /i model models/asr/oracle-ghost.bin $(rep 0f 32) Apache-2.0 asr.oracle-demo")"
M_MOD_HASH="$(medit m-mod-hash "$PKG5M" "s|^\\(model models/asr/model\\.bin\\) [0-9a-f]* |\\1 $TKH |")"
M_MOD_LIC_BAD="$(medit m-mod-lic-bad "$PKG5M" 's|^\(model models/asr/model\.bin [^ ]*\) Apache-2\.0 |\1 -Apache-2.0 |')"
M_MOD_NO_ID="$(medit m-mod-no-id "$PKG5M" 's|^\(model models/asr/model\.bin [^ ]* [^ ]*\) [^ ]*$|\1|')"

PKG6_UNSIGNED="$(copy_of p060-unsigned "$PKG6")"
rm -f -- "$PKG6_UNSIGNED/SHA256SUMS.sig"
PKG6_UNSIGNED_TAR="$(mktar p060-unsigned "$PKG6_UNSIGNED")"
PKG6_HOM110="$(copy_of p060-hom110 "$PKG6")"
rm -f -- "$PKG6_HOM110/SHA256SUMS.sig" "$PKG6_HOM110/BUNDLE.manifest"
write_sums "$PKG6_HOM110"
PKG6_KU="$(copy_of p060-ku "$PKG6")"
sign "$PKG6_KU" "$KU"
PKG6_REWRITE="$(copy_of p060-rewrite "$PKG6")"
printf '# changed after signing\n' >>"$PKG6_REWRITE/infra/nginx/homayar.conf"
write_sums "$PKG6_REWRITE"
PKG6_SIGRND="$(copy_of p060-sigrnd "$PKG6")"
head -c 64 /dev/urandom >"$PKG6_SIGRND/SHA256SUMS.sig"
PKG6_NOMAN="$(copy_of p060-noman "$PKG6")"
rm -f -- "$PKG6_NOMAN/BUNDLE.manifest"
seal "$PKG6_NOMAN" "$K1"
PKG6_REF="$(medit p060-ref "$PKG6" "s|^\\(image images/api\\.tar [0-9a-f]*\\) homayar/api:$V6 |\\1 homayar/api:0.5.9 |")"
PKG6_NOREQ="$(medit p060-noreq "$PKG6" '/^requires /d')"
PKG6_EXTRAPEM="$(copy_of p060-extrapem "$PKG6")"
cp "$PUBU" "$PKG6_EXTRAPEM/extra.pem"
PKG6_IMGTAMPER="$(copy_of p060-imgtamper "$PKG6")"
printf 'x' >>"$PKG6_IMGTAMPER/images/api.tar"

mkdir -p "$MROOT/asr" "$MROOT/llm"
printf 'oracle asr weights v1\n' >"$MROOT/asr/model.bin"
printf '{"oracle": "tokenizer v1"}\n' >"$MROOT/asr/tokenizer.json"
printf 'oracle llm weights v1\n' >"$MROOT/llm/weights.gguf"
printf 'outside the model root\n' >"$T/outside.bin"
SH_MB="$(fsha "$MROOT/asr/model.bin")"
SH_TK="$(fsha "$MROOT/asr/tokenizer.json")"
SH_LW="$(fsha "$MROOT/llm/weights.gguf")"
SH_OUT="$(fsha "$T/outside.bin")"
mkman() { python3 -I "$PY/mkmodelman.py" "$@"; }
M_ASR="$MANS/oracle-asr.json"
mkman "$M_ASR" asr.oracle-demo Apache-2.0 "file:$SH_MB:asr/model.bin" "file:$SH_TK:asr/tokenizer.json"
M_LLM="$MANS/oracle-llm.json"
mkman "$M_LLM" llm.oracle-demo MIT "file:$SH_LW:llm/weights.gguf"
M_BADSHA="$MANS/bad-sha.json"
mkman "$M_BADSHA" asr.oracle-demo Apache-2.0 "file:$SH_TK:asr/model.bin" "file:$SH_TK:asr/tokenizer.json"
M_DIRART="$MANS/dir-artifact.json"
mkman "$M_DIRART" asr.oracle-demo Apache-2.0 "file:$SH_MB:asr/model.bin" "dir:$(rep 0c 32):asr"
M_NOLIC="$MANS/no-licence.json"
mkman "$M_NOLIC" asr.oracle-demo @absent "file:$SH_MB:asr/model.bin"
M_EMPTYLIC="$MANS/empty-licence.json"
mkman "$M_EMPTYLIC" asr.oracle-demo "" "file:$SH_MB:asr/model.bin"
M_DOTDOT="$MANS/dotdot.json"
mkman "$M_DOTDOT" asr.oracle-demo Apache-2.0 "file:$SH_OUT:../outside.bin"
M_ABS="$MANS/absolute.json"
mkman "$M_ABS" asr.oracle-demo Apache-2.0 "file:$SH_OUT:$T/outside.bin"
M_DOTSEG="$MANS/dot-segment.json"
mkman "$M_DOTSEG" asr.oracle-demo Apache-2.0 "file:$SH_MB:asr/./model.bin"
M_MISSING="$MANS/missing-file.json"
mkman "$M_MISSING" asr.oracle-demo Apache-2.0 "file:$(rep 0d 32):asr/oracle-missing.bin"
printf 'model models/asr/model.bin %s Apache-2.0 asr.oracle-demo\nmodel models/asr/tokenizer.json %s Apache-2.0 asr.oracle-demo\nmodel models/llm/weights.gguf %s MIT llm.oracle-demo\n' \
  "$SH_MB" "$SH_TK" "$SH_LW" >"$T/expect.models"

write_env() {
  cat >"$HOMEF/config/onprem.env" <<ENVEOF
APP_ENVIRONMENT=production
PUBLIC_ORIGIN=https://homayar.customer.example
CORS_ORIGINS=https://homayar.customer.example
TRUSTED_HOSTS=homayar.customer.example
BIND_ADDRESS=127.0.0.1
HTTP_PORT=8080
POSTGRES_DB=homayar
POSTGRES_USER=homayar
POSTGRES_PASSWORD=${MARKER}pgpass
DATABASE_URL=postgresql+psycopg://homayar:${MARKER}pgpass@postgres:5432/homayar
REDIS_PASSWORD=${MARKER}redis
JWT_SECRET=${MARKER}jwtXyzXyzXyzXyzXyzXyzXyzXyz
TOKEN_PEPPER=${MARKER}pepperXyzXyzXyzXyzXyzXyz
MINIO_ROOT_USER=${MARKER}rootuser
MINIO_ROOT_PASSWORD=${MARKER}rootpass
STORAGE_ACCESS_KEY=${MARKER}access
STORAGE_SECRET_KEY=${MARKER}secret
NODE_EXPORTER_TEXTFILE_DIR=${GAUGE}
ENVEOF
  chmod 600 "$HOMEF/config/onprem.env"
}

write_gauge() {
  local ts t
  ts=$(($(date +%s) - 3600))
  mkdir -p "$GAUGE"
  {
    printf '# HELP homayar_backup_last_success_timestamp_seconds Unix time of the last successful Homayar backup per target.\n'
    printf '# TYPE homayar_backup_last_success_timestamp_seconds gauge\n'
    for t in postgres objects offsite; do
      printf 'homayar_backup_last_success_timestamp_seconds{target="%s"} %s.250\n' "$t" "$ts"
    done
  } >"$GAUGE/homayar_backup.prom"
}

fresh_world() {
  rm -rf -- "${HOMEF:?}" "${STUB:?}" "${RUNTMP:?}" "${GAUGE:?}" "$T/alt-trust"
  mkdir -p "$HOMEF/config" "$STUB" "$RUNTMP" "$TRUST"
  write_env
  cp "$PUB1" "$TRUST/oracle-trusted.pem"
  : >"$STUB/calls"
  : >"$STUB/images"
  : >"$STUB/svc"
  : >"$STUB/up-from"
}

prepare_installed() {
  fresh_world
  mkdir -p "$HOMEF/releases" "$HOMEF/state" "$HOMEF/ops"
  chmod 700 "$HOMEF/state"
  cp -R -p "$PKG5" "$HOMEF/releases/$V5"
  ln -s "releases/$V5" "$HOMEF/current"
  {
    img_row "homayar/api:$V5" "$IDA5" "$SHA5" "$V5" "$H5"
    img_row "homayar/console-web:$V5" "$IDC5" "$SHA5" "$V5" "$H5"
    img_row "homayar/egress-proxy:$V5" "$IDE5" "$SHA5" "$V5" "$H5"
  } >"$STUB/images"
  cat >"$STUB/svc" <<'SVCEOF'
proxy|running|healthy|0
console-web|running|healthy|0
api|running|healthy|0
worker|running|healthy|0
scheduler|running||0
postgres|running|healthy|0
redis|running|healthy|0
minio|running|healthy|0
minio-bootstrap|exited||0
SVCEOF
  printf 'api|homayar/api:%s\nworker|homayar/api:%s\nscheduler|homayar/api:%s\nconsole-web|homayar/console-web:%s\n' \
    "$V5" "$V5" "$V5" "$V5" >"$STUB/imgs"
  printf '%s\n' "$H5" >"$STUB/db-version"
  write_gauge
}

trust_none() { rm -rf -- "${TRUST:?}"; }
trust_empty() {
  rm -rf -- "${TRUST:?}"
  mkdir -p "$TRUST"
}
trust_wrong_ext() {
  trust_empty
  cp "$PUB1" "$TRUST/oracle-trusted.pub"
}
trust_rsa() { cp "$KEYS/rsa.pub.pem" "$TRUST/oracle-rsa.pem"; }
trust_ec() { cp "$KEYS/ec.pub.pem" "$TRUST/oracle-ec.pem"; }
trust_private() { cp "$K2" "$TRUST/oracle-private.pem"; }
trust_junk() { printf 'not a key at all\n' >"$TRUST/oracle-notes.pem"; }
trust_two() { cp "$PUB2" "$TRUST/oracle-rotation.pem"; }
trust_env_alt() {
  mkdir -p "$T/alt-trust"
  cp "$PUB1" "$T/alt-trust/oracle-alt.pem"
  trust_none
  RUN_ENV=("HOMAYAR_BUNDLE_TRUST_DIR=$T/alt-trust")
}
trust_env_untrusted() {
  mkdir -p "$T/alt-trust"
  cp "$PUBU" "$T/alt-trust/oracle-alt.pem"
  RUN_ENV=("HOMAYAR_BUNDLE_TRUST_DIR=$T/alt-trust")
}

RUN_N=0
RC=0
LAST_OUT=""
LAST_ERR=""
CASE=""
FAILS=()
CASE_OUTS=()
RUN_ENV=()
PACK_SCAN=""
NPASS=0
NFAIL=0

run_s() {
  RUN_N=$((RUN_N + 1))
  LAST_OUT="$OUTS/$RUN_N.out"
  LAST_ERR="$OUTS/$RUN_N.err"
  CASE_OUTS+=("$LAST_OUT" "$LAST_ERR")
  (
    cd "$RUNCWD" || exit 99
    export PATH="$BIN:$BASE_PATH" HOMAYAR_HOME="$HOMEF" ONPREM_TEST_HOME="$HOMEF" TMPDIR="$RUNTMP" \
      NODE_EXPORTER_TEXTFILE_DIR="$GAUGE"
    unset HOMAYAR_BUNDLE_TRUST_DIR HOMAYAR_BUNDLE_SIGNING_KEY
    exec env "${RUN_ENV[@]}" timeout 300 bash "$SUBJECT" "$@"
  ) </dev/null >"$LAST_OUT" 2>"$LAST_ERR"
  RC=$?
  RUN_ENV=()
}

begin() {
  CASE="$1"
  FAILS=()
  CASE_OUTS=()
  RUN_ENV=()
  PACK_SCAN=""
}

ok() {
  local d="$1"
  shift
  if ! "$@"; then FAILS+=("$d"); fi
}

redact() {
  sed -E "s/$MARKER/<MARKER>/g; s/PRIVATE KEY/<PK>/g; s#[A-Za-z0-9+/=]{40,}#<b64>#g; s/[0-9a-fA-F]{20,}/<hex>/g"
}

end_case() {
  local f hit=""
  local -a scan=("${CASE_OUTS[@]}" "$HOMEF/ops")
  if [[ -n "$PACK_SCAN" ]]; then scan+=("$PACK_SCAN"); fi
  if ! hit="$(python3 -I "$PY/leakscan.py" "$MARKER" "$KEYS" "${scan[@]}" 2>&1)"; then
    FAILS+=("secret text leaked (A3/A10): ${hit:-the leak scan failed}")
  fi
  if [[ ${#FAILS[@]} -eq 0 ]]; then
    NPASS=$((NPASS + 1))
    printf 'PASS %s\n' "$CASE"
  else
    NFAIL=$((NFAIL + 1))
    printf 'FAIL %s\n' "$CASE"
    for f in "${FAILS[@]}"; do printf '    - %s\n' "$f"; done
    printf '    last exit code: %s\n' "$RC"
    if [[ -f "$LAST_OUT" ]]; then
      tail -n 4 "$LAST_OUT" "$LAST_ERR" 2>/dev/null | redact | sed 's/^/      | /'
    fi
  fi
}

rc_is() { [[ "$RC" == "$1" ]]; }
out_has() { grep -qE -- "$1" "$LAST_OUT" "$LAST_ERR"; }
out_hasi() { grep -qiE -- "$1" "$LAST_OUT" "$LAST_ERR"; }
out_hasF() { grep -qF -- "$1" "$LAST_OUT" "$LAST_ERR"; }
out_lacksF() { ! grep -qF -- "$1" "$LAST_OUT" "$LAST_ERR"; }
not_usage() { out_lacksF "$USAGE_LINE"; }
fp_re() { printf '(^|[^0-9a-f])%s([^0-9a-f]|$)' "$1"; }
fp_shown() { grep -qiE -- "$(fp_re "$1")" "$LAST_OUT" "$LAST_ERR"; }
fp_logged() { cat "$HOMEF"/ops/*.log 2>/dev/null | grep -qiE -- "$(fp_re "$1")"; }
requires_shown() {
  out_has 'oracle-dep-canary.{0,4}>=9\.9\.9' && out_has 'bash.{0,4}>=4\.4' && out_has 'openssl.{0,4}>=3\.0'
}
has_call() { grep -qE -- "$1" "$STUB/calls"; }
no_call() { ! grep -qE -- "$1" "$STUB/calls"; }
no_load() { no_call '^load '; }
loaded_all() { has_call "^load homayar/api:$1\$" && has_call "^load homayar/console-web:$1\$" && has_call "^load homayar/egress-proxy:$1\$"; }
stack_untouched() { no_call '^(up|stop|migrate|preflight|load|pg_dump|pg_restore-live|db-reset)( |$)'; }
current_is() { [[ -L "$HOMEF/current" && "$(readlink "$HOMEF/current")" == "releases/$1" ]]; }
no_current() { [[ ! -e "$HOMEF/current" && ! -L "$HOMEF/current" ]]; }
no_release_entries() { [[ -z "$(find "$HOMEF/releases" -mindepth 1 -maxdepth 1 2>/dev/null)" ]]; }
only_release() { [[ "$(find "$HOMEF/releases" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null)" == "$1" ]]; }
started_from() { [[ -s "$STUB/up-from" && "$(tail -n 1 "$STUB/up-from")" == "$HOMEF/releases/$1" ]]; }
db_is() { [[ "$(cat "$STUB/db-version" 2>/dev/null)" == "$1" ]]; }
no_dump() { no_call '^pg_dump' && [[ -z "$(find "$HOMEF/state" -name db.dump 2>/dev/null)" ]]; }
api_runs() { [[ "$(awk -F'|' '$1 == "api" { print $2; exit }' "$STUB/imgs" 2>/dev/null)" == "$1" ]] && grep -q '^api|running|' "$STUB/svc"; }
absent() { [[ ! -e "$1" ]]; }
no_sig() { [[ ! -e "$1/SHA256SUMS.sig" ]]; }
sig64() { [[ -f "$1/SHA256SUMS.sig" && "$(wc -c <"$1/SHA256SUMS.sig" | tr -d ' ')" == 64 ]]; }
sig_verifies() {
  [[ -f "$1/SHA256SUMS.sig" ]] &&
    openssl pkeyutl -verify -pubin -inkey "$2" -rawin -in "$1/SHA256SUMS" -sigfile "$1/SHA256SUMS.sig" >/dev/null 2>&1
}
sig_fails_with() { [[ -f "$1/SHA256SUMS.sig" ]] && ! sig_verifies "$1" "$2"; }
manifest_listed() {
  [[ -f "$1/BUNDLE.manifest" ]] &&
    grep -qxE "$(fsha "$1/BUNDLE.manifest")  BUNDLE\\.manifest" "$1/SHA256SUMS"
}
sums_verify() { (cd "$1" && sha256sum -c --strict --quiet SHA256SUMS >/dev/null 2>&1); }
sums_cover_all() {
  local listed present
  listed="$(sed -E 's/^[0-9a-f]{64}  //' "$1/SHA256SUMS" 2>/dev/null | LC_ALL=C sort)"
  present="$(cd "$1" && find . -type f ! -path ./SHA256SUMS ! -path ./SHA256SUMS.sig -printf '%P\n' | LC_ALL=C sort)"
  [[ -n "$listed" && "$listed" == "$present" ]]
}
man_line() { [[ -f "$1/BUNDLE.manifest" && "$(sed -n "${2}p" "$1/BUNDLE.manifest")" == "$3" ]]; }
man_type_is() {
  local want
  want="$(grep -E "^$2 " "$3")"
  [[ -n "$want" && -f "$1/BUNDLE.manifest" && "$(grep -E "^$2 " "$1/BUNDLE.manifest")" == "$want" ]]
}
man_is() { [[ -f "$1/BUNDLE.manifest" && "$(cat "$1/BUNDLE.manifest")" == "$(cat "$2")" ]]; }
same_file() { [[ -f "$1" && -f "$2" ]] && cmp -s "$1" "$2"; }

expect_manifest() {
  local out="$1" conf="$2" models="${3:-}" dest="$4"
  {
    printf 'schema homayar-offline-bundle/1\n'
    printf 'release %s %s\n' "$V7" "$SHA7"
    printf 'image images/api.tar %s homayar/api:%s %s\n' "$(fsha "$out/images/api.tar")" "$V7" \
      "$(awk '$1 == "license" && $2 == "api" { print $3 }' "$conf")"
    printf 'image images/console-web.tar %s homayar/console-web:%s %s\n' "$(fsha "$out/images/console-web.tar")" "$V7" \
      "$(awk '$1 == "license" && $2 == "console-web" { print $3 }' "$conf")"
    printf 'image images/egress-proxy.tar %s homayar/egress-proxy:%s %s\n' "$(fsha "$out/images/egress-proxy.tar")" "$V7" \
      "$(awk '$1 == "license" && $2 == "egress-proxy" { print $3 }' "$conf")"
    if [[ -n "$models" ]]; then cat "$models"; fi
    grep -E '^requires ' "$conf"
  } >"$dest"
}

write_conf() {
  mkdir -p "$SRC/infra/offline"
  cat >"$SRC/infra/offline/bundle.conf" <<'CONFEOF'
# oracle bundle input
license api LicenseRef-Oracle-Api

license console-web MIT
license egress-proxy Apache-2.0
# runtime dependencies of the host
requires bash >=4.4
requires openssl >=3.0
requires docker-engine >=24.0
requires docker-compose-plugin >=2.24
requires nvidia-container-toolkit gpu-profile-only
requires oracle-dep-canary >=9.9.9
CONFEOF
}

make_src() {
  rm -rf -- "${SRC:?}"
  write_tree "$SRC" "$V7"
  write_conf
  printf '%s\n' "$SHA7" >"$SRC/.oracle-git-head"
}

pack_world() {
  fresh_world
  {
    img_row "homayar/api:$V7" "$IDA7" "$SHA7" "$V7" "$H7"
    img_row "homayar/console-web:$V7" "$IDC7" "$SHA7" "$V7" "$H7"
    img_row "homayar/egress-proxy:$V7" "$IDE7" "$SHA7" "$V7" "$H7"
  } >"$STUB/images"
  make_src
}

pack_run() {
  local out="$1"
  shift
  rm -rf -- "${out:?}"
  PACK_SCAN="$out"
  run_s pack --source "$SRC" --output "$out" --version "$V7" \
    --api-image "homayar/api:$V7" --console-web-image "homayar/console-web:$V7" \
    --egress-proxy-image "homayar/egress-proxy:$V7" "$@"
}

allow_install() {
  local title="$1" pkg="$2" setup="$3" fp="$4" v="$5"
  begin "$title"
  fresh_world
  if [[ "$setup" != - ]]; then "$setup"; fi
  run_s install --package "$pkg"
  ok "exit code is 0" rc_is 0
  ok "current is the relative link releases/$v" current_is "$v"
  ok "all three image archives were loaded" loaded_all "$v"
  ok "the stack was started from releases/$v" started_from "$v"
  ok "stdout names the 16-hex key fingerprint $fp as its own token" fp_shown "$fp"
  ok "the ops/ log names the key fingerprint $fp" fp_logged "$fp"
  ok "the requires lines of BUNDLE.manifest are printed" requires_shown
  ok "the old stub warning '$STUB_WARN' is gone" out_lacksF "$STUB_WARN"
  end_case
}

deny_install() {
  local title="$1" pkg="$2" setup="$3" re
  shift 3
  begin "$title"
  fresh_world
  if [[ "$setup" != - ]]; then "$setup"; fi
  run_s install --package "$pkg"
  ok "exit code is 2" rc_is 2
  ok "prints 'Nothing was changed.'" out_hasF "Nothing was changed."
  ok "no docker load ran" no_load
  ok "no releases/ entry (final or temp) was created" no_release_entries
  ok "current was not created" no_current
  ok "the stack was not touched (no up, run, stop or migrate)" stack_untouched
  for re in "$@"; do ok "the output matches /$re/" out_hasi "$re"; done
  end_case
}

deny_upgrade() {
  local title="$1" pkg="$2" setup="$3" re
  shift 3
  begin "$title"
  prepare_installed
  if [[ "$setup" != - ]]; then "$setup"; fi
  run_s upgrade --package "$pkg" --min-disk-gib 1
  ok "exit code is 2" rc_is 2
  ok "prints 'Nothing was changed.'" out_hasF "Nothing was changed."
  ok "current still points at releases/$V5" current_is "$V5"
  ok "releases/ still holds only $V5 (no new or temp release)" only_release "$V5"
  ok "no docker load ran" no_load
  ok "the stack was not stopped, started or migrated" stack_untouched
  ok "no pre-migration dump was taken" no_dump
  ok "the api still runs homayar/api:$V5" api_runs "homayar/api:$V5"
  for re in "$@"; do ok "the output matches /$re/" out_hasi "$re"; done
  end_case
}

allow_install "A6 install: a folder package signed by the trusted key installs, prints the fingerprint and the requires lines" \
  "$PKG5" - "$FP1" "$V5"
allow_install "A6 install: the same package as a .tar installs (signature checked on the staged folder)" \
  "$PKG5_TAR" - "$FP1" "$V5"
allow_install "A7g allow-control: a package with model files and model lines installs" \
  "$PKG5M" - "$FP1" "$V5"
allow_install "A7g allow-control: licences with '.', '+' and '-' (GPL-2.0+, MIT, LicenseRef-Oracle.Test-1) install" \
  "$PKG5_LICS" - "$FP1" "$V5"
allow_install "A8 rotation: two trusted keys, a package signed by the first key installs" \
  "$PKG5" trust_two "$FP1" "$V5"
allow_install "A8 rotation: two trusted keys, a package signed by the second key installs and names the second fingerprint" \
  "$PKG5_K2" trust_two "$FP2" "$V5"
allow_install "D3 trust dir from HOMAYAR_BUNDLE_TRUST_DIR: the key there is used (the default folder is absent)" \
  "$PKG5" trust_env_alt "$FP1" "$V5"

deny_install "A7a install: a package without SHA256SUMS.sig is refused" \
  "$PKG5_UNSIGNED" - 'signature'
deny_install "A7a/D12 install: a HOM-110 package (no SHA256SUMS.sig, no BUNDLE.manifest) is refused" \
  "$PKG5_HOM110" - 'signature'
deny_install "A7a install: an unsigned .tar package is refused" \
  "$PKG5_UNSIGNED_TAR" - 'signature'
begin "D12 install: --allow-unsigned does not let an unsigned package in"
fresh_world
run_s install --allow-unsigned --package "$PKG5_UNSIGNED"
ok "exit code is 2" rc_is 2
ok "no docker load ran" no_load
ok "current was not created" no_current
end_case
deny_install "A7b install: a package signed by a key that is not in the trust dir is refused" \
  "$PKG5_KU" - 'signature'
deny_install "A7b install: an untrusted-key .tar package is refused" \
  "$PKG5_KU_TAR" - 'signature'
deny_install "A7b install: the untrusted key's public PEM shipped inside the package (bundle-keys/, config/bundle-keys/) is never trusted" \
  "$PKG5_KU_PEM" - 'signature'
deny_install "A8 rotation deny: two trusted keys, a package signed by a third key is refused" \
  "$PKG5_KU" trust_two 'signature'
deny_install "D3 trust dir from HOMAYAR_BUNDLE_TRUST_DIR wins: only an untrusted key there, the default dir is not used" \
  "$PKG5" trust_env_untrusted 'signature'
deny_install "A7c install: a 63-byte SHA256SUMS.sig (the real one cut short) is refused" \
  "$PKG5_SIG63" - 'signature'
deny_install "A7c install: a 65-byte SHA256SUMS.sig (the real one plus a newline) is refused" \
  "$PKG5_SIG65" - 'signature'
deny_install "A7c install: an empty SHA256SUMS.sig is refused" \
  "$PKG5_SIG0" - 'signature'
deny_install "A7c install: 64 random bytes as SHA256SUMS.sig are refused" \
  "$PKG5_SIGRND" - 'signature'
deny_install "A7d install: a file changed after signing with SHA256SUMS rewritten is refused" \
  "$PKG5_REWRITE" - 'signature'
deny_install "A7d install: the same tampered package as a .tar is refused" \
  "$PKG5_REWRITE_TAR" - 'signature'
deny_install "A7d install: release.env and BUNDLE.manifest moved to another API image with SHA256SUMS rewritten is refused" \
  "$PKG5_SWAPREF" - 'signature'
deny_install "A7e install: a missing trust dir refuses" \
  "$PKG5" trust_none 'signature'
deny_install "A7e install: an empty trust dir refuses" \
  "$PKG5" trust_empty 'signature'
deny_install "A7e install: a trust dir whose only key is not a *.pem file refuses" \
  "$PKG5" trust_wrong_ext 'signature'
deny_install "A7f install: an RSA public .pem next to the trusted key fails closed and names the file" \
  "$PKG5" trust_rsa 'signature' 'oracle-rsa\.pem'
deny_install "A7f install: an EC public .pem next to the trusted key fails closed and names the file" \
  "$PKG5" trust_ec 'signature' 'oracle-ec\.pem'
deny_install "A7f install: an Ed25519 PRIVATE key .pem in the trust dir fails closed and names the file" \
  "$PKG5" trust_private 'signature' 'oracle-private\.pem'
deny_install "A7f install: a .pem that is not a key at all fails closed and names the file" \
  "$PKG5" trust_junk 'signature' 'oracle-notes\.pem'

deny_install "A7g install: a signed package without BUNDLE.manifest is refused" \
  "$M_NONE" - 'BUNDLE\.manifest'
deny_install "A7g install: BUNDLE.manifest first line 'schema homayar-offline-bundle/2' is refused" \
  "$M_SCHEMA2" - 'BUNDLE\.manifest'
deny_install "A7g install: BUNDLE.manifest whose schema line is not the first line is refused" \
  "$M_SCHEMA_SECOND" - 'BUNDLE\.manifest'
deny_install "A7g install: a release line whose sha differs from release.env is refused" \
  "$M_REL_SHA" - 'BUNDLE\.manifest'
deny_install "A7g install: two release lines are refused" \
  "$M_REL_DUP" - 'BUNDLE\.manifest'
deny_install "A7g install: no release line is refused" \
  "$M_REL_NONE" - 'BUNDLE\.manifest'
deny_install "A7g install: an image line whose sha256 differs from SHA256SUMS (another listed file's hash) is refused" \
  "$M_IMG_HASH" - 'BUNDLE\.manifest'
deny_install "A7g install: an image ref that differs from release.env is refused" \
  "$M_IMG_REF" - 'BUNDLE\.manifest'
deny_install "A7g install: image refs swapped between images/api.tar and images/console-web.tar are refused" \
  "$M_IMG_SWAP" - 'BUNDLE\.manifest'
deny_install "A7g install: an image line without a licence field is refused" \
  "$M_LIC_NONE" - 'BUNDLE\.manifest'
deny_install "A7g install: an image licence 'Apache_2.0' (bad character) is refused" \
  "$M_LIC_BAD" - 'BUNDLE\.manifest'
deny_install "A7g install: an image line with an extra field is refused" \
  "$M_FIELD_EXTRA" - 'BUNDLE\.manifest'
deny_install "A7g install: two image lines for images/api.tar are refused" \
  "$M_IMG_DUP" - 'BUNDLE\.manifest'
deny_install "A7g install: an image line for a missing images/*.tar is refused" \
  "$M_IMG_GHOST" - 'BUNDLE\.manifest'
deny_install "A7g install: a listed images/*.tar without an image line is refused" \
  "$M_IMG_UNLINED" - 'BUNDLE\.manifest'
deny_install "A7g install: a listed model file without a model line is refused" \
  "$M_MOD_UNLINED" - 'BUNDLE\.manifest'
deny_install "A7g install: a model line for a missing file is refused" \
  "$M_MOD_GHOST" - 'BUNDLE\.manifest'
deny_install "A7g install: a model line whose sha256 differs from SHA256SUMS is refused" \
  "$M_MOD_HASH" - 'BUNDLE\.manifest'
deny_install "A7g install: a model licence '-Apache-2.0' (bad first character) is refused" \
  "$M_MOD_LIC_BAD" - 'BUNDLE\.manifest'
deny_install "A7g install: a model line without its manifest_id field is refused" \
  "$M_MOD_NO_ID" - 'BUNDLE\.manifest'
deny_install "A7g install: an unknown record type 'note' is refused" \
  "$M_UNKNOWN" - 'BUNDLE\.manifest'
deny_install "A7g install: a '#' comment line in BUNDLE.manifest is refused" \
  "$M_COMMENT" - 'BUNDLE\.manifest'
deny_install "A7g install: an empty line in BUNDLE.manifest is refused" \
  "$M_EMPTY" - 'BUNDLE\.manifest'
deny_install "A7g install: no requires line is refused" \
  "$M_REQ_NONE" - 'BUNDLE\.manifest'

deny_install "A7h/D9 install: an unlisted SHA256SUMS.sig2 at the root is refused" \
  "$PKG5_SIG2" - 'SHA256SUMS\.sig2'
deny_install "A7h/D9 install: an unlisted extra.pem at the root is refused" \
  "$PKG5_EXTRAPEM" - 'extra\.pem'
deny_install "D9 install: an unlisted SHA256SUMS.sig below the root (infra/) is refused" \
  "$PKG5_SUBSIG" - 'infra/SHA256SUMS\.sig'
deny_install "D9 install: an unlisted lower-case sha256sums.sig at the root is refused" \
  "$PKG5_LOWERSIG" - 'sha256sums\.sig'

deny_install "regression control: a signed package with a tampered image tar is refused by the hash check" \
  "$PKG5_IMGTAMPER" - 'images/api\.tar'
deny_install "regression control: a signed SHA256SUMS with a .. path is refused" \
  "$PKG5_DOTDOT" - 'forbidden path|\.\./outside\.txt'
deny_install "regression control: a signed package holding a symlink is refused" \
  "$PKG5_SYMLINK" - 'infra/link'
deny_install "regression control: a signed package whose release.env uses :latest is refused" \
  "$PKG5_LATEST" - 'latest'

begin "A9 upgrade: a correctly signed 0.6.0 package upgrades, prints the fingerprint and the requires lines"
prepare_installed
run_s upgrade --package "$PKG6" --min-disk-gib 1
ok "exit code is 0" rc_is 0
ok "current is the relative link releases/$V6" current_is "$V6"
ok "all three 0.6.0 image archives were loaded" loaded_all "$V6"
ok "the database is at the 0.6.0 head" db_is "$H6"
ok "the api runs homayar/api:$V6" api_runs "homayar/api:$V6"
ok "stdout names the 16-hex key fingerprint $FP1 as its own token" fp_shown "$FP1"
ok "the ops/ log names the key fingerprint $FP1" fp_logged "$FP1"
ok "the requires lines of BUNDLE.manifest are printed" requires_shown
ok "the old stub warning '$STUB_WARN' is gone" out_lacksF "$STUB_WARN"
end_case

deny_upgrade "A9a upgrade: a package without SHA256SUMS.sig is refused, the running release untouched" \
  "$PKG6_UNSIGNED" - 'signature'
deny_upgrade "A9a/D12 upgrade: a HOM-110 package (no SHA256SUMS.sig, no BUNDLE.manifest) is refused" \
  "$PKG6_HOM110" - 'signature'
deny_upgrade "A9a upgrade: an unsigned .tar package is refused" \
  "$PKG6_UNSIGNED_TAR" - 'signature'
deny_upgrade "A9b upgrade: a package signed by an untrusted key is refused" \
  "$PKG6_KU" - 'signature'
deny_upgrade "A9c upgrade: 64 random bytes as SHA256SUMS.sig are refused" \
  "$PKG6_SIGRND" - 'signature'
deny_upgrade "A9d upgrade: a file changed after signing with SHA256SUMS rewritten is refused" \
  "$PKG6_REWRITE" - 'signature'
deny_upgrade "A9e upgrade: an empty trust dir refuses" \
  "$PKG6" trust_empty 'signature'
deny_upgrade "A9f upgrade: an RSA public .pem in the trust dir fails closed and names the file" \
  "$PKG6" trust_rsa 'signature' 'oracle-rsa\.pem'
deny_upgrade "A9g upgrade: a signed package without BUNDLE.manifest is refused" \
  "$PKG6_NOMAN" - 'BUNDLE\.manifest'
deny_upgrade "A9g upgrade: an image ref that differs from release.env is refused" \
  "$PKG6_REF" - 'BUNDLE\.manifest'
deny_upgrade "A9g upgrade: no requires line is refused" \
  "$PKG6_NOREQ" - 'BUNDLE\.manifest'
deny_upgrade "A9h upgrade: an unlisted extra.pem is refused" \
  "$PKG6_EXTRAPEM" - 'extra\.pem'
deny_upgrade "regression control: upgrade of a signed package with a tampered image tar is refused" \
  "$PKG6_IMGTAMPER" - 'images/api\.tar'

PK_FLAG="$PACKS/flag"
begin "A1 pack (flag form): a 64-byte SHA256SUMS.sig that verifies with the D2 openssl command, and the D5 BUNDLE.manifest"
pack_world
pack_run "$PK_FLAG" --signing-key "$K1"
expect_manifest "$PK_FLAG" "$SRC/infra/offline/bundle.conf" "" "$T/expect.flag"
ok "exit code is 0" rc_is 0
ok "SHA256SUMS.sig is exactly 64 bytes" sig64 "$PK_FLAG"
ok "openssl pkeyutl -verify -rawin with the signing key's public key succeeds" sig_verifies "$PK_FLAG" "$PUB1"
ok "the signature does not verify with an unrelated key (control)" sig_fails_with "$PK_FLAG" "$PUBU"
ok "BUNDLE.manifest exists and is listed in SHA256SUMS with its hash" manifest_listed "$PK_FLAG"
ok "every SHA256SUMS line verifies" sums_verify "$PK_FLAG"
ok "SHA256SUMS lists every file except SHA256SUMS and SHA256SUMS.sig" sums_cover_all "$PK_FLAG"
ok "line 1 is 'schema homayar-offline-bundle/1'" man_line "$PK_FLAG" 1 "schema homayar-offline-bundle/1"
ok "line 2 is 'release $V7 <HEAD sha>' as in release.env" man_line "$PK_FLAG" 2 "release $V7 $SHA7"
ok "three image lines, sorted by path, with the file hash, the release.env ref and the bundle.conf licence" \
  man_type_is "$PK_FLAG" image "$T/expect.flag"
ok "the requires lines are the bundle.conf requires lines in file order" man_type_is "$PK_FLAG" requires "$T/expect.flag"
ok "BUNDLE.manifest is exactly those lines in the D5 order (no model, comment or empty line)" man_is "$PK_FLAG" "$T/expect.flag"
ok "pack prints the signing key fingerprint $FP1" fp_shown "$FP1"
ok "the old stub warning '$STUB_WARN' is gone" out_lacksF "$STUB_WARN"
end_case

begin "A1/A6 pack round trip: the flag-form package installs with the trusted key"
fresh_world
run_s install --package "$PK_FLAG"
ok "exit code is 0" rc_is 0
ok "current is the relative link releases/$V7" current_is "$V7"
ok "all three 0.7.0 image archives were loaded" loaded_all "$V7"
ok "stdout names the key fingerprint $FP1" fp_shown "$FP1"
ok "the bundle.conf requires lines are printed" requires_shown
end_case

PK_ENV="$PACKS/env"
begin "A1 pack (env form): HOMAYAR_BUNDLE_SIGNING_KEY signs the package"
pack_world
RUN_ENV=("HOMAYAR_BUNDLE_SIGNING_KEY=$K1")
pack_run "$PK_ENV"
ok "exit code is 0" rc_is 0
ok "SHA256SUMS.sig is exactly 64 bytes" sig64 "$PK_ENV"
ok "openssl pkeyutl -verify -rawin with the signing key's public key succeeds" sig_verifies "$PK_ENV" "$PUB1"
ok "BUNDLE.manifest exists and is listed in SHA256SUMS with its hash" manifest_listed "$PK_ENV"
ok "every SHA256SUMS line verifies" sums_verify "$PK_ENV"
ok "pack prints the signing key fingerprint $FP1" fp_shown "$FP1"
end_case

PK_WINS="$PACKS/flag-wins"
begin "D4 pack: --signing-key wins over HOMAYAR_BUNDLE_SIGNING_KEY"
pack_world
RUN_ENV=("HOMAYAR_BUNDLE_SIGNING_KEY=$KU")
pack_run "$PK_WINS" --signing-key "$K1"
ok "exit code is 0" rc_is 0
ok "the signature verifies with the flag key" sig_verifies "$PK_WINS" "$PUB1"
ok "the signature does not verify with the env key" sig_fails_with "$PK_WINS" "$PUBU"
ok "pack prints the flag key fingerprint $FP1" fp_shown "$FP1"
ok "pack does not print the env key fingerprint $FPU" bash -c '! grep -qiF -- "$1" "$2" "$3"' _ "$FPU" "$LAST_OUT" "$LAST_ERR"
end_case

PK_0400="$PACKS/k0400"
begin "A2 allow-control: a key with mode 0400 (no group or other bits) signs"
pack_world
pack_run "$PK_0400" --signing-key "$KEYS/k1-0400.pem"
ok "exit code is 0" rc_is 0
ok "the signature verifies" sig_verifies "$PK_0400" "$PUB1"
end_case

deny_pack_key() {
  local title="$1" out="$PACKS/$2" usage_ok="$3"
  shift 3
  begin "$title"
  pack_world
  pack_run "$out" "$@"
  ok "exit code is 2" rc_is 2
  ok "the output folder holds no SHA256SUMS.sig" no_sig "$out"
  if [[ "$usage_ok" != usage-ok ]]; then
    ok "the refusal is about the key, not a usage error (the flag is known)" not_usage
  fi
  end_case
}

deny_pack_key "A2 pack: no key (no flag, no env) is refused" nokey usage-ok
deny_pack_key "A2 pack: a key with mode 0644 is refused" k0644 - --signing-key "$KEYS/k1-0644.pem"
deny_pack_key "A2 pack: a key with mode 0640 (group read) is refused" k0640 - --signing-key "$KEYS/k1-0640.pem"
deny_pack_key "A2 pack: a key with mode 0604 (other read) is refused" k0604 - --signing-key "$KEYS/k1-0604.pem"
deny_pack_key "A2 pack: an RSA private key is refused" krsa - --signing-key "$KEYS/rsa.pem"
deny_pack_key "A2 pack: an EC private key is refused" kec - --signing-key "$KEYS/ec.pem"
deny_pack_key "A2 pack: an Ed25519 PUBLIC key as the signing key is refused" kpub - --signing-key "$PUB1"
deny_pack_key "A2 pack: a missing key file is refused" kmissing - --signing-key "$KEYS/oracle-missing.pem"
deny_pack_key "A2 pack: a folder as the key path is refused" kdir - --signing-key "$KEYS/keydir"

begin "A2 pack (env form): HOMAYAR_BUNDLE_SIGNING_KEY with mode 0644 is refused"
pack_world
RUN_ENV=("HOMAYAR_BUNDLE_SIGNING_KEY=$KEYS/k1-0644.pem")
pack_run "$PACKS/env0644"
ok "exit code is 2" rc_is 2
ok "the output folder holds no SHA256SUMS.sig" no_sig "$PACKS/env0644"
end_case

begin "A2 pack (env form): HOMAYAR_BUNDLE_SIGNING_KEY naming a missing file is refused"
pack_world
RUN_ENV=("HOMAYAR_BUNDLE_SIGNING_KEY=$KEYS/oracle-missing.pem")
pack_run "$PACKS/envmissing"
ok "exit code is 2" rc_is 2
ok "the output folder holds no SHA256SUMS.sig" no_sig "$PACKS/envmissing"
end_case

begin "A5 pack: a missing infra/offline/bundle.conf is refused"
pack_world
rm -f -- "$SRC/infra/offline/bundle.conf"
pack_run "$PACKS/conf-missing" --signing-key "$K1"
ok "exit code is 2" rc_is 2
ok "the output folder holds no SHA256SUMS.sig" no_sig "$PACKS/conf-missing"
ok "the refusal is not a usage error" not_usage
end_case

begin "A5 pack: bundle.conf without a licence for egress-proxy is refused"
pack_world
sed -i '/^license egress-proxy /d' "$SRC/infra/offline/bundle.conf"
pack_run "$PACKS/conf-no-eg" --signing-key "$K1"
ok "exit code is 2" rc_is 2
ok "the output folder holds no SHA256SUMS.sig" no_sig "$PACKS/conf-no-eg"
ok "the refusal is not a usage error" not_usage
end_case

begin "A5 pack: bundle.conf without a licence for api is refused"
pack_world
sed -i '/^license api /d' "$SRC/infra/offline/bundle.conf"
pack_run "$PACKS/conf-no-api" --signing-key "$K1"
ok "exit code is 2" rc_is 2
ok "the output folder holds no SHA256SUMS.sig" no_sig "$PACKS/conf-no-api"
ok "the refusal is not a usage error" not_usage
end_case

begin "A5 pack: bundle.conf with no requires line (comments only) is refused"
pack_world
sed -i 's/^requires /# requires /' "$SRC/infra/offline/bundle.conf"
pack_run "$PACKS/conf-no-req" --signing-key "$K1"
ok "exit code is 2" rc_is 2
ok "the output folder holds no SHA256SUMS.sig" no_sig "$PACKS/conf-no-req"
ok "the refusal is not a usage error" not_usage
end_case

PK_MOD="$PACKS/models"
begin "A4 pack: --model-root with two --model-manifest files copies the files, adds model lines and the manifests"
pack_world
pack_run "$PK_MOD" --signing-key "$K1" --model-root "$MROOT" --model-manifest "$M_ASR" --model-manifest "$M_LLM"
expect_manifest "$PK_MOD" "$SRC/infra/offline/bundle.conf" "$T/expect.models" "$T/expect.mod"
ok "exit code is 0" rc_is 0
ok "models/asr/model.bin is the source file" same_file "$MROOT/asr/model.bin" "$PK_MOD/models/asr/model.bin"
ok "models/asr/tokenizer.json is the source file" same_file "$MROOT/asr/tokenizer.json" "$PK_MOD/models/asr/tokenizer.json"
ok "models/llm/weights.gguf is the source file" same_file "$MROOT/llm/weights.gguf" "$PK_MOD/models/llm/weights.gguf"
ok "manifests/oracle-asr.json is the first manifest" same_file "$M_ASR" "$PK_MOD/manifests/oracle-asr.json"
ok "manifests/oracle-llm.json is the second manifest" same_file "$M_LLM" "$PK_MOD/manifests/oracle-llm.json"
ok "one model line per file, sorted by path, with each manifest's licence and manifest_id" \
  man_type_is "$PK_MOD" model "$T/expect.mod"
ok "BUNDLE.manifest is exactly the D5 lines in order (schema, release, image, model, requires)" man_is "$PK_MOD" "$T/expect.mod"
ok "SHA256SUMS lists every file except SHA256SUMS and SHA256SUMS.sig" sums_cover_all "$PK_MOD"
ok "every SHA256SUMS line verifies" sums_verify "$PK_MOD"
ok "SHA256SUMS.sig is 64 bytes and verifies" bash -c '[[ "$(wc -c <"$1/SHA256SUMS.sig" | tr -d " ")" == 64 ]] && openssl pkeyutl -verify -pubin -inkey "$2" -rawin -in "$1/SHA256SUMS" -sigfile "$1/SHA256SUMS.sig" >/dev/null 2>&1' _ "$PK_MOD" "$PUB1"
end_case

begin "A4 pack round trip: the model package installs"
fresh_world
run_s install --package "$PK_MOD"
ok "exit code is 0" rc_is 0
ok "current is the relative link releases/$V7" current_is "$V7"
ok "the model file is in the release" same_file "$MROOT/llm/weights.gguf" "$HOMEF/releases/$V7/models/llm/weights.gguf"
end_case

deny_pack_model() {
  local title="$1" out="$PACKS/$2" usage_ok="$3" re="$4"
  shift 4
  begin "$title"
  pack_world
  pack_run "$out" --signing-key "$K1" "$@"
  ok "exit code is 2" rc_is 2
  ok "the output folder holds no SHA256SUMS.sig" no_sig "$out"
  if [[ "$usage_ok" != usage-ok ]]; then
    ok "the refusal is about the model input, not a usage error" not_usage
  fi
  if [[ "$re" != - ]]; then ok "the output matches /$re/" out_hasi "$re"; fi
  end_case
}

deny_pack_model "A4 pack: a model file whose sha256 differs from the pin is refused" m-badsha - - \
  --model-root "$MROOT" --model-manifest "$M_BADSHA"
deny_pack_model "A4 pack: a dir artifact is refused with a 'not supported' message" m-dir - 'dir.*not supported|not supported.*dir' \
  --model-root "$MROOT" --model-manifest "$M_DIRART"
deny_pack_model "A4 pack: a manifest without a license key is refused" m-nolic - - \
  --model-root "$MROOT" --model-manifest "$M_NOLIC"
deny_pack_model "A4 pack: a manifest with an empty license is refused" m-emptylic - - \
  --model-root "$MROOT" --model-manifest "$M_EMPTYLIC"
deny_pack_model "A4 pack: a '..' artifact path (the file exists and its hash matches) is refused" m-dotdot - - \
  --model-root "$MROOT" --model-manifest "$M_DOTDOT"
deny_pack_model "A4/D7 pack: an absolute artifact path (the file exists and its hash matches) is refused" m-abs - - \
  --model-root "$MROOT" --model-manifest "$M_ABS"
deny_pack_model "A4/D7 pack: a '.' segment in an artifact path is refused" m-dotseg - - \
  --model-root "$MROOT" --model-manifest "$M_DOTSEG"
deny_pack_model "A4/D7 pack: an artifact file missing under the model root is refused" m-missing - - \
  --model-root "$MROOT" --model-manifest "$M_MISSING"
deny_pack_model "A4 pack: --model-root without --model-manifest is refused" m-rootonly usage-ok - \
  --model-root "$MROOT"
deny_pack_model "A4 pack: --model-manifest without --model-root is refused" m-manonly usage-ok - \
  --model-manifest "$M_ASR"

REPO_CONF="$WORK/infra/offline/bundle.conf"
conf_line() { [[ -f "$REPO_CONF" ]] && grep -qxE -- "$1" "$REPO_CONF"; }
begin "D6 committed infra/offline/bundle.conf: three LicenseRef-Homayar-Proprietary licences and the required requires lines"
ok "the file exists" test -f "$REPO_CONF"
ok "license api LicenseRef-Homayar-Proprietary" conf_line 'license api LicenseRef-Homayar-Proprietary'
ok "license console-web LicenseRef-Homayar-Proprietary" conf_line 'license console-web LicenseRef-Homayar-Proprietary'
ok "license egress-proxy LicenseRef-Homayar-Proprietary" conf_line 'license egress-proxy LicenseRef-Homayar-Proprietary'
ok "requires bash >=4.4" conf_line 'requires bash >=4\.4'
ok "requires openssl >=3.0" conf_line 'requires openssl >=3\.0'
ok "a docker engine requires line with a >= minimum" conf_line 'requires docker(-engine|-ce)? >=[0-9][^ ]*'
ok "a docker compose plugin requires line with a >= minimum" conf_line 'requires [^ ]*compose[^ ]* >=[0-9][^ ]*'
ok "nvidia-container-toolkit marked as needed only for the GPU profile" conf_line 'requires nvidia-container-toolkit [^ ]*gpu[^ ]*'
end_case

PK_REPO="$PACKS/repo-conf"
begin "D6/A1 pack with the committed bundle.conf: its licences and requires lines reach BUNDLE.manifest"
pack_world
if [[ -f "$REPO_CONF" ]]; then cp "$REPO_CONF" "$SRC/infra/offline/bundle.conf"; else rm -f -- "$SRC/infra/offline/bundle.conf"; fi
pack_run "$PK_REPO" --signing-key "$K1"
ok "exit code is 0" rc_is 0
ok "the requires lines equal the committed file's requires lines in order" man_type_is "$PK_REPO" requires "$REPO_CONF"
ok "every image line carries LicenseRef-Homayar-Proprietary" \
  bash -c '[[ "$(grep -c "^image .* LicenseRef-Homayar-Proprietary\$" "$1/BUNDLE.manifest")" == 3 ]]' _ "$PK_REPO"
end_case

ADR="$WORK/docs/architecture/decisions/0070-signed-offline-bundles.md"
adr_has() { [[ -f "$ADR" ]] && grep -qiE -- "$1" "$ADR"; }
adr_heading() { [[ -f "$ADR" ]] && grep -qE -- "^## $1" "$ADR"; }
adr_all_d() {
  local i
  [[ -f "$ADR" ]] || return 1
  for i in 1 2 3 4 5 6 7 8 9 10 11 12; do grep -qwE "D$i" "$ADR" || return 1; done
}
begin "A12 ADR 0070 records the decisions with context, decision, consequences and rejected alternatives"
ok "docs/architecture/decisions/0070-signed-offline-bundles.md exists" test -f "$ADR"
ok "has a Context section" adr_heading Context
ok "has a Decision section" adr_heading Decision
ok "has a Consequences section" adr_heading Consequences
ok "has an Alternatives section" adr_heading Alternatives
ok "names D1 to D12" adr_all_d
ok "names Ed25519" adr_has 'ed25519'
ok "records minisign as rejected" adr_has 'minisign'
ok "records cosign as rejected" adr_has 'cosign'
ok "records GPG as rejected" adr_has 'gpg|gnupg'
ok "names SHA256SUMS.sig" adr_has 'SHA256SUMS\.sig'
ok "names BUNDLE.manifest" adr_has 'BUNDLE\.manifest'
ok "names HOMAYAR_BUNDLE_TRUST_DIR" adr_has 'HOMAYAR_BUNDLE_TRUST_DIR'
end_case

RUNBOOK="$WORK/docs/operations/onprem-lifecycle.md"
rb_has() { [[ -f "$RUNBOOK" ]] && grep -qiE -- "$1" "$RUNBOOK"; }
begin "A12 runbook: key generation, where the private key lives, the trust dir, rotation; the unsigned line is gone"
ok "the line 'Packages are hash-checked, not signed' is gone" bash -c '! grep -qF "Packages are hash-checked, not signed" "$1"' _ "$RUNBOOK"
ok "shows openssl genpkey for ed25519" rb_has 'genpkey.*ed25519'
ok "says the private key lives on a signing host" rb_has 'signing host'
ok "names the bundle-keys trust directory" rb_has 'bundle-keys'
ok "explains key rotation" rb_has 'rotat'
ok "names the signing key flag or env" rb_has '--signing-key|HOMAYAR_BUNDLE_SIGNING_KEY'
end_case

CI_YML="$WORK/.github/workflows/ci.yml"
begin "A13 CI shellchecks and runs this oracle"
ok "ci.yml lists the oracle in the shellcheck file list" grep -qE '^[[:space:]]+infra/ci/tests/oracle-homayar-onprem-signing\.sh[[:space:]]*$' "$CI_YML"
ok "ci.yml has a run step 'bash infra/ci/tests/oracle-homayar-onprem-signing.sh'" \
  grep -qE '^[[:space:]]+run: bash infra/ci/tests/oracle-homayar-onprem-signing\.sh[[:space:]]*$' "$CI_YML"
end_case

printf '%s passed, %s failed\n' "$NPASS" "$NFAIL"
[[ "$NFAIL" == 0 ]]
