#!/usr/bin/env bash
set -Eu
export LC_ALL=C

WORK="$(pwd -P)"
SUBJECT="${ONPREM_SUBJECT:-infra/scripts/homayar-onprem.sh}"
case "$SUBJECT" in
  /*) ;;
  *) SUBJECT="$WORK/$SUBJECT" ;;
esac

T="$(mktemp -d "${TMPDIR:-/tmp}/onprem-oracle.XXXXXX")"
trap 'rm -rf -- "${T:?}"' EXIT

BIN="$T/bin"
PY="$T/py"
STUB="$T/stub"
HOMEF="$T/home"
PKGS="$T/pkgs"
RUNTMP="$T/runtmp"
OUTS="$T/outs"
TEXTFILE="$T/textfile"
RUNCWD="$T/cwd"
SRC="$T/src"
MARKER="ZzOracleSeeKrit77"
BASE_PATH="$PATH"
mkdir -p "$BIN" "$PY" "$PKGS" "$OUTS" "$RUNCWD" "$STUB"

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

# HOM-107: every fixture package is signed at run time with a throwaway
# Ed25519 key (never committed); its public half seeds the trust directory of
# the fake home, and the real openssl of the test image signs and verifies.
KEY="$T/keys/bundle-signing-key.pem"
PUB="$T/keys/bundle-signing-key.pub.pem"
mkdir -p "$T/keys"
openssl genpkey -algorithm ed25519 -out "$KEY" 2>/dev/null
chmod 600 "$KEY"
openssl pkey -in "$KEY" -pubout -out "$PUB" 2>/dev/null
FINGERPRINT="$(openssl pkey -in "$KEY" -pubout -outform DER 2>/dev/null | sha256sum | cut -c1-16)"
sign_pkg() { openssl pkeyutl -sign -inkey "$KEY" -rawin -in "$1/SHA256SUMS" -out "$1/SHA256SUMS.sig" 2>/dev/null; }

cat >"$PY/mkimg.py" <<'PYEOF'
import io
import json
import sys
import tarfile

out, spec = sys.argv[1], json.loads(sys.argv[2])
files, manifest, repos = [], [], {}
for item in spec:
    idhex = item["id"].split(":", 1)[-1]
    cfg = {
        "architecture": "amd64",
        "os": "linux",
        "config": {
            "Labels": {
                "org.opencontainers.image.revision": item["sha"],
                "org.opencontainers.image.version": item["version"],
                "oracle.head": item["head"],
            }
        },
    }
    files.append((idhex + ".json", json.dumps(cfg, sort_keys=True)))
    manifest.append({"Config": idhex + ".json", "RepoTags": [item["ref"]], "Layers": []})
    repo, tag = item["ref"].rsplit(":", 1)
    repos.setdefault(repo, {})[tag] = idhex
files.append(("manifest.json", json.dumps(manifest, sort_keys=True)))
files.append(("repositories", json.dumps(repos, sort_keys=True)))
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
    for name, text in files:
        data = text.encode()
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mtime = 1794264000
        tf.addfile(info, io.BytesIO(data))
blob = buf.getvalue()
if out == "-":
    sys.stdout.buffer.write(blob)
else:
    with open(out, "wb") as fh:
        fh.write(blob)
PYEOF

cat >"$PY/loadtar.py" <<'PYEOF'
import io
import json
import sys
import tarfile

with open(sys.argv[1], "rb") as fh:
    data = fh.read()
try:
    tf = tarfile.open(fileobj=io.BytesIO(data))
    manifest = json.load(tf.extractfile("manifest.json"))
except Exception:
    sys.exit(1)
for entry in manifest:
    cfgname = entry["Config"]
    idhex = cfgname.rsplit("/", 1)[-1].removesuffix(".json")
    try:
        cfg = json.load(tf.extractfile(cfgname))
    except Exception:
        cfg = {}
    labels = (cfg.get("config") or {}).get("Labels") or {}
    for ref in entry.get("RepoTags") or []:
        print("|".join([
            ref,
            "sha256:" + idhex,
            labels.get("oracle.head", ""),
            labels.get("org.opencontainers.image.revision", ""),
            labels.get("org.opencontainers.image.version", ""),
        ]))
PYEOF

cat >"$PY/shiftts.py" <<'PYEOF'
import datetime as dt
import re
import sys

value, delta = sys.argv[1], int(sys.argv[2])
if re.fullmatch(r"\d{9,11}", value):
    print(int(value) + delta)
    sys.exit(0)
for fmt in (
    "%Y%m%dT%H%M%SZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S+00:00",
    "%Y-%m-%d %H:%M:%S UTC",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%d %H:%M:%SZ",
):
    try:
        parsed = dt.datetime.strptime(value, fmt)
    except ValueError:
        continue
    print((parsed + dt.timedelta(seconds=delta)).strftime(fmt))
    sys.exit(0)
sys.exit(1)
PYEOF

cat >"$PY/extract.py" <<'PYEOF'
import os
import sys
import tarfile

src, dst = sys.argv[1], sys.argv[2]
with tarfile.open(src, "r:gz") as tf:
    for member in tf.getmembers():
        parts = [p for p in member.name.split("/") if p not in ("", ".")]
        if member.name.startswith("/") or ".." in parts:
            print("UNSAFE " + member.name)
            sys.exit(3)
        if not member.isfile():
            continue
        path = os.path.join(dst, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with tf.extractfile(member) as fh, open(path, "wb") as out:
            out.write(fh.read())
        print("/".join(parts))
PYEOF

cat >"$PY/mancheck.py" <<'PYEOF'
import hashlib
import json
import os
import sys

root = sys.argv[1]
digests = {}
manifest = None
for folder, _, names in os.walk(root):
    for name in names:
        path = os.path.join(folder, name)
        if name == "MANIFEST.json":
            manifest = path
            continue
        with open(path, "rb") as fh:
            digests[os.path.relpath(path, root)] = hashlib.sha256(fh.read()).hexdigest()
if manifest is None:
    print("no MANIFEST.json")
    sys.exit(1)
with open(manifest, encoding="utf-8") as fh:
    doc = json.load(fh)
strings, pairs = [], []


def walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            pairs.append((str(key).lower(), item))
            walk(item)
    elif isinstance(value, list):
        for item in value:
            walk(item)
    else:
        strings.append(str(value))


walk(doc)
missing = sorted(rel for rel, digest in digests.items() if not any(digest in s for s in strings))
if missing:
    print("sha256 missing from MANIFEST.json for: " + ", ".join(missing))
    sys.exit(1)
if not any(("format" in k or "version" in k) and item in (1, "1") for k, item in pairs):
    print("no bundle format version 1 in MANIFEST.json")
    sys.exit(1)
if not any("creat" in k for k, _ in pairs):
    print("no created-at in MANIFEST.json")
    sys.exit(1)
print("ok")
PYEOF

{
  printf '#!/usr/bin/env bash\n'
  printf 'S=%q\nPYD=%q\nMARK=%q\n' "$STUB" "$PY" "$MARKER"
  cat <<'DOCKEREOF'
set -u
{
  printf 'docker'
  for a in "$@"; do printf ' %s' "${a//$'\n'/ }"; done
  printf '\n'
} >>"$S/calls"

ev() { local j="$*"; printf '%s\n' "${j//$'\n'/ }" >>"$S/events"; }
destructive() { printf '%s\n' "$*" >>"$S/destructive"; ev "destructive $*"; }
ONPREM=(proxy console-web api scheduler worker postgres redis minio minio-bootstrap)
ENVFILES=()
CFILES=()
PROFILES=()
TT=""

svc_field() { awk -F'|' -v s="$1" -v n="$2" '$1 == s { print $n; exit }' "$S/svc" 2>/dev/null; }
svc_exists() { [[ -n "$(svc_field "$1" 1)" ]]; }
svc_running() { [[ "$(svc_field "$1" 2)" == running ]]; }
set_svc() {
  awk -F'|' -v s="$1" '$1 != s' "$S/svc" >"$S/svc.tmp" 2>/dev/null || :
  printf '%s|%s|%s|%s|%s\n' "$1" "$2" "$3" "$4" "$5" >>"$S/svc.tmp"
  mv -f "$S/svc.tmp" "$S/svc"
}
del_svc() {
  awk -F'|' -v s="$1" '$1 != s' "$S/svc" >"$S/svc.tmp" 2>/dev/null || :
  mv -f "$S/svc.tmp" "$S/svc"
}
img_get() { awk -F'|' -v r="$1" -v n="$2" '($1 == r || $2 == r) { print $n; exit }' "$S/images" 2>/dev/null; }
img_known() { [[ -n "${1:-}" && -n "$(img_get "$1" 1)" ]]; }
ref_id() {
  local i
  i="$(img_get "$1" 2)"
  if [[ -n "$i" ]]; then printf '%s' "$i"; else printf 'sha256:%s' "$(printf '%s' "$1" | sha256sum | cut -c1-64)"; fi
}
db() { cat "$S/db" 2>/dev/null || :; }
sub() { TT="${TT//"$1"/"$2"}"; }
norm_fmt() { printf '%s' "$1" | sed -E 's/\{\{ *([^ {}]+) *\}\}/{{\1}}/g'; }
finish_tt() {
  local bs_t='\t' bs_n='\n'
  TT="${TT//"$bs_t"/$'\t'}"
  TT="${TT//"$bs_n"/$'\n'}"
  case "$TT" in table\ *) TT="${TT#table }" ;; esac
  printf '%s\n' "$(printf '%s' "$TT" | sed -E 's/\{\{[^}]*\}\}//g')"
}
strip_quotes() {
  local v="$1"
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  printf '%s' "$v"
}
file_value() {
  local line
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?$2=" "$1" 2>/dev/null | tail -n 1)"
  [[ -n "$line" ]] || return 1
  strip_quotes "${line#*=}"
}
resolve_var() {
  local name="$1" v="" f d c
  v="${!name:-}"
  if [[ -n "$v" ]]; then printf '%s' "$v"; return 0; fi
  for f in "${ENVFILES[@]}"; do
    c="$(file_value "$f" "$name")" && v="$c"
  done
  if [[ -n "$v" ]]; then printf '%s' "$v"; return 0; fi
  for f in "${CFILES[@]}" "$PWD/x"; do
    d="$(dirname -- "$f")"
    for c in "$d/../../release.env" "$d/../release.env" "$d/release.env"; do
      if [[ -f "$c" ]] && v="$(file_value "$c" "$name")"; then printf '%s' "$v"; return 0; fi
    done
  done
  return 0
}
profile_on() {
  local p
  for p in "${PROFILES[@]}"; do [[ "$p" == "$1" || "$p" == "*" ]] && return 0; done
  [[ ",${COMPOSE_PROFILES:-}," == *",$1,"* || "${COMPOSE_PROFILES:-}" == "*" ]]
}
svc_ref() {
  case "$1" in
    api|worker|scheduler|migrate|backup-tool|tombstone-reapply) resolve_var API_IMAGE ;;
    console-web) resolve_var CONSOLE_WEB_IMAGE ;;
    egress-proxy) resolve_var EGRESS_PROXY_IMAGE ;;
    *) printf '%s:oracle-third-party' "$1" ;;
  esac
}
own_image() {
  case "$1" in api|worker|scheduler|migrate|backup-tool|tombstone-reapply|console-web|egress-proxy) return 0 ;; esac
  return 1
}
has_health() {
  case "$1" in proxy|console-web|api|worker|postgres|redis|minio|egress-proxy) return 0 ;; esac
  return 1
}
missing_image() {
  printf 'Error response from daemon: pull access denied for %s, repository does not exist (oracle: image not loaded)\n' "${1:-homayar/unset:local}" >&2
}
compose_dir() {
  local f="${CFILES[0]:-$PWD/compose.yaml}"
  (cd "$(dirname -- "$f")" 2>/dev/null && pwd -P) || printf '%s' "$PWD"
}
start_one() {
  local s="$1" ref
  ref="$(svc_ref "$s")"
  if own_image "$s" && ! img_known "$ref"; then missing_image "$ref"; return 1; fi
  if svc_running "$s" && [[ "$(svc_field "$s" 5)" == "$ref" ]]; then return 0; fi
  if [[ "$s" == minio-bootstrap ]]; then
    set_svc "$s" exited "" 0 "$ref"
  elif has_health "$s"; then
    set_svc "$s" running healthy "" "$ref"
  else
    set_svc "$s" running "" "" "$ref"
  fi
  printf '%s|%s\n' "$s" "$(compose_dir)" >>"$S/started-from"
  ev "start $s $ref"
}
start_existing() {
  local s="$1" ref
  svc_exists "$s" || { printf 'no container found for service "%s"\n' "$s" >&2; return 1; }
  ref="$(svc_field "$s" 5)"
  if [[ "$s" == minio-bootstrap ]]; then
    set_svc "$s" exited "" 0 "$ref"
  elif has_health "$s"; then
    set_svc "$s" running healthy "" "$ref"
  else
    set_svc "$s" running "" "" "$ref"
  fi
  ev "start $s $ref"
}
stop_one() {
  local s="$1"
  svc_exists "$s" || return 0
  if svc_running "$s"; then ev "stop $s"; fi
  set_svc "$s" exited "" 0 "$(svc_field "$s" 5)"
}
all_existing() { awk -F'|' 'NF { print $1 }' "$S/svc" 2>/dev/null; }

do_migrate() {
  local ref="$1" head
  cp "$S/svc" "$S/migrate-snapshot" 2>/dev/null || :
  if ! img_known "$ref"; then missing_image "$ref"; ev "migrate fail noimage"; return 1; fi
  if ! svc_running postgres; then echo "migrate: could not connect to postgres" >&2; ev "migrate fail nodb"; return 1; fi
  if [[ -e "$S/fail-migrate" ]]; then
    printf '%s\n' 9f9f9f9f9f9f >"$S/db"
    echo "alembic: migration failed (oracle injected)" >&2
    ev "migrate fail"
    return 1
  fi
  head="$(img_get "$ref" 3)"
  printf '%s\n' "$head" >"$S/db"
  ev "migrate ok $head"
  printf 'INFO  [alembic.runtime.migration] Running upgrade -> %s\n' "$head"
}

build_info_json() {
  local ref="$1"
  printf '{"application":{"name":"homayar-platform","version":"%s"},"profile":"standard","runtime":{"python_version":"3.12.8"},"schema":{"migration_head":"%s"},"schema_version":1,"source":{"date_epoch":1794264000,"released":true,"sha":"%s"}}\n' \
    "$(img_get "$ref" 5)" "$(img_get "$ref" 3)" "$(img_get "$ref" 4)"
}

app_cmd() {
  local ref="$1" svc="$2"
  shift 2
  local words=" $* "
  words="${words//$'\n'/ }"
  case "$words" in
    *model_preflight*)
      local rc
      rc="$(cat "$S/preflight-rc" 2>/dev/null || echo 0)"
      ev "preflight $words"
      printf '{"mode": "host", "status": "%s", "checks": {"disk": "ok", "gpu_driver": "skip"}}\n' "$([[ "$rc" == 0 ]] && echo ok || echo fail)"
      return "$rc"
      ;;
    *support_bundle*" scan "*)
      local rc out
      printf '%s\n' "${words#* scan }" >"$S/scan-args"
      out="$(cat "$S/bundle-out" 2>/dev/null || :)"
      if [[ -n "$out" && -e "$out" ]]; then : >"$S/out-existed-at-scan"; fi
      rc="$(cat "$S/scan-rc" 2>/dev/null || echo 0)"
      ev "scan $rc"
      if [[ "$rc" == 1 ]]; then echo "container.json: email"; fi
      if [[ "$rc" == 2 ]]; then echo "scan: error reading bundle (oracle injected)" >&2; fi
      return "$rc"
      ;;
    *support_bundle*collect*)
      ev "collect"
      printf '{"oracle": "oracle-collect-ok", "release": {"version": "%s"}, "settings": {"DEPLOYMENT_MODE": "onprem", "DATABASE_URL": "set"}, "database": {"reachable": true, "alembic_version": "%s"}}\n' \
        "$(img_get "$ref" 5)" "$(db)"
      return 0
      ;;
    *alembic*upgrade*) do_migrate "$ref"; return ;;
    *migration_head*) printf '%s\n' "$(img_get "$ref" 3)"; return 0 ;;
    *alembic*heads*) printf '%s (head)\n' "$(img_get "$ref" 3)"; return 0 ;;
    *alembic*current*) printf '%s (head)\n' "$(db)"; return 0 ;;
    *observability.release*|*build-info*|*build_info*) build_info_json "$ref"; return 0 ;;
    *printenv*)
      local last="${!#}" v="" f
      for f in "${ENVFILES[@]}"; do v="$(file_value "$f" "$last")" || v=""; [[ -n "$v" ]] && break; done
      [[ -n "$v" ]] || return 1
      printf '%s\n' "$v"
      return 0
      ;;
  esac
  return 0
}

restore_is_live() {
  local after tgt
  after="${1#*pg_restore}"
  case " $after " in *" --create "*|*" -C "*) return 0 ;; esac
  tgt="$(printf '%s' "$after" | grep -oE -- '(-d|--dbname)[= ]+[^ ;&|)]+' | head -n 1 | sed -E 's/^(-d|--dbname)[= ]+//' | tr -d "\"'")"
  [[ -n "$tgt" ]] || return 0
  case "$tgt" in
    '$POSTGRES_DB'|'${POSTGRES_DB}'|homayar|dbname=homayar) return 0 ;;
  esac
  return 1
}

pg_cmd() {
  local p=" $* "
  p="${p//$'\n'/ }"
  case "$p" in
    *pg_dump*)
      local w
      for w in proxy console-web api worker scheduler; do
        if svc_running "$w"; then ev "pg_dump while writers run"; break; fi
      done
      if [[ -e "$S/fail-dump" ]]; then ev "pg_dump fail"; echo "pg_dump: error: connection lost (oracle injected)" >&2; return 1; fi
      cp "$S/svc" "$S/dump-snapshot"
      db >"$S/dump-version"
      ev "pg_dump ok"
      printf 'PGDMP oracle-dump version=%s\n' "$(db)"
      return 0
      ;;
    *pg_restore*)
      cat >/dev/null 2>&1 || :
      case "$p" in
        *" --list"*|*" -l "*)
          printf '; oracle archive listing\n215; 1259 16401 TABLE public alembic_version homayar\n321; 0 16401 TABLE DATA public alembic_version homayar\n'
          return 0
          ;;
      esac
      if restore_is_live "$p"; then
        if [[ -e "$S/fail-live-restore" ]]; then ev "pg_restore live fail"; echo "pg_restore: error: could not execute query (oracle injected)" >&2; return 1; fi
        cat "$S/dump-version" >"$S/db" 2>/dev/null || :
        ev "pg_restore live ok"
      else
        ev "pg_restore scratch ok"
      fi
      return 0
      ;;
    *alembic_version*) db; return 0 ;;
    *'count('*) echo 42; return 0 ;;
    *server_version*|*'version()'*) echo 17.2; return 0 ;;
    *pg_isready*) echo "/var/run/postgresql:5432 - accepting connections"; return 0 ;;
    *psql*) echo 1; return 0 ;;
  esac
  return 0
}

svc_exec() {
  local svc="$1"
  shift
  if ! svc_running "$svc"; then printf 'service "%s" is not running\n' "$svc" >&2; return 1; fi
  if [[ "$svc" == postgres ]]; then pg_cmd "$@"; return; fi
  app_cmd "$(svc_field "$svc" 5)" "$svc" "$@"
}

fill_row() {
  F_NAME="homayar-onprem-$F_SERVICE-1"
  F_ID="cid-$F_SERVICE"
  if [[ "$F_STATE" == running ]]; then
    F_STATUS="Up 5 minutes"
    [[ -n "$F_HEALTH" ]] && F_STATUS+=" ($F_HEALTH)"
  else
    F_STATUS="Exited (${F_EXIT:-0}) 1 minute ago"
  fi
}
row_json() {
  printf '{"ID":"%s","Name":"%s","Names":"%s","Image":"%s","Command":"oracle","Project":"homayar-onprem","Service":"%s","State":"%s","Health":"%s","ExitCode":%s,"Status":"%s","Publishers":[]}' \
    "$F_ID" "$F_NAME" "$F_NAME" "$F_IMAGE" "$F_SERVICE" "$F_STATE" "$F_HEALTH" "${F_EXIT:-0}" "$F_STATUS"
}
render_row() {
  TT="$(norm_fmt "$1")"
  sub '{{json .}}' "$(row_json)"
  sub '{{.Service}}' "$F_SERVICE"
  sub '{{.State}}' "$F_STATE"
  sub '{{.Health}}' "$F_HEALTH"
  sub '{{.ExitCode}}' "${F_EXIT:-0}"
  sub '{{.Name}}' "$F_NAME"
  sub '{{.Names}}' "$F_NAME"
  sub '{{.Image}}' "$F_IMAGE"
  sub '{{.ID}}' "$F_ID"
  sub '{{.Status}}' "$F_STATUS"
  sub '{{.Project}}' homayar-onprem
  sub '{{.RunningFor}}' "5 minutes ago"
  sub '{{.CreatedAt}}' "2026-11-13 01:00:00 +0000 UTC"
  sub '{{.Ports}}' ""
  sub '{{.Publishers}}' "[]"
  sub '{{.Label "com.docker.compose.service"}}' "$F_SERVICE"
  sub '{{.Command}}' '"oracle"'
  sub '{{.Labels}}' "com.docker.compose.service=$F_SERVICE"
  sub '{{.LocalVolumes}}' 0
  sub '{{.Mounts}}' ""
  sub '{{.Networks}}' homayar-onprem_backend
  sub '{{.Size}}' 0B
  if [[ "$TT" =~ \{\{\.([A-Za-z]+) ]]; then
    printf "template parsing error: template: :1: executing \"\" at <.%s>: can't evaluate field %s in type *formatter.ContainerContext\n" \
      "${BASH_REMATCH[1]}" "${BASH_REMATCH[1]}" >&2
    return 1
  fi
  finish_tt
}

compose_ps() {
  local all=0 quiet=0 services=0 fmt="" status="" want=() w hit fv
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -a|--all) all=1; shift ;;
      -q|--quiet) quiet=1; shift ;;
      --services) services=1; shift ;;
      --format) fmt="$2"; shift 2 ;;
      --format=*) fmt="${1#*=}"; shift ;;
      --status) status="$2"; shift 2 ;;
      --status=*) status="${1#*=}"; shift ;;
      --filter) [[ "$2" == status=* ]] && status="${2#status=}"; shift 2 ;;
      --filter=*) fv="${1#*=}"; [[ "$fv" == status=* ]] && status="${fv#status=}"; shift ;;
      -*) shift ;;
      *) want+=("$1"); shift ;;
    esac
  done
  ev "ps"
  if [[ -z "$fmt" || "$fmt" == table ]] && [[ "$quiet" == 0 && "$services" == 0 ]]; then
    printf 'NAME\tIMAGE\tCOMMAND\tSERVICE\tCREATED\tSTATUS\tPORTS\n'
  fi
  while IFS='|' read -r F_SERVICE F_STATE F_HEALTH F_EXIT F_IMAGE; do
    [[ -n "$F_SERVICE" ]] || continue
    if [[ ${#want[@]} -gt 0 ]]; then
      hit=0
      for w in "${want[@]}"; do [[ "$w" == "$F_SERVICE" ]] && hit=1; done
      [[ "$hit" == 1 ]] || continue
    fi
    if [[ -n "$status" ]]; then
      [[ "$F_STATE" == "$status" ]] || continue
    elif [[ "$all" == 0 && "$F_STATE" != running ]]; then
      continue
    fi
    fill_row
    if [[ "$quiet" == 1 ]]; then
      printf '%s\n' "$F_ID"
    elif [[ "$services" == 1 ]]; then
      printf '%s\n' "$F_SERVICE"
    elif [[ -z "$fmt" || "$fmt" == table ]]; then
      printf '%s\t%s\t"oracle"\t%s\t5 minutes ago\t%s\t\n' "$F_NAME" "$F_IMAGE" "$F_SERVICE" "$F_STATUS"
    elif [[ "$fmt" == json ]]; then
      row_json
      printf '\n'
    else
      render_row "$fmt" || return 1
    fi
  done <"$S/svc"
  return 0
}

ps_cmd() {
  local all=0 quiet=0 fmt=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -a|--all) all=1; shift ;;
      -q|--quiet) quiet=1; shift ;;
      --format) fmt="$2"; shift 2 ;;
      --format=*) fmt="${1#*=}"; shift ;;
      --filter|-f|-n|--last) shift 2 ;;
      *) shift ;;
    esac
  done
  while IFS='|' read -r F_SERVICE F_STATE F_HEALTH F_EXIT F_IMAGE; do
    [[ -n "$F_SERVICE" ]] || continue
    [[ "$all" == 1 || "$F_STATE" == running ]] || continue
    fill_row
    if [[ "$quiet" == 1 ]]; then
      printf '%s\n' "$F_ID"
    elif [[ -z "$fmt" ]]; then
      printf '%s\t%s\t%s\t%s\n' "$F_ID" "$F_IMAGE" "$F_STATUS" "$F_NAME"
    elif [[ "$fmt" == json ]]; then
      row_json
      printf '\n'
    else
      render_row "$fmt"
    fi
  done <"$S/svc"
  return 0
}

cont_svc() {
  local r="$1" s=""
  case "$r" in
    cid-*) s="${r#cid-}" ;;
    homayar-onprem-*-1) s="${r#homayar-onprem-}"; s="${s%-1}" ;;
    *) return 1 ;;
  esac
  svc_exists "$s" || return 1
  printf '%s' "$s"
}

cont_out() {
  local s="$1" fmt="$2" first="$3" ref id st he running nm env stj hj obj
  ref="$(svc_field "$s" 5)"
  id="$(ref_id "$ref")"
  st="$(svc_field "$s" 2)"
  he="$(svc_field "$s" 3)"
  running=false
  [[ "$st" == running ]] && running=true
  nm="/homayar-onprem-$s-1"
  env="[\"DATABASE_URL=postgresql+psycopg://homayar:${MARK}pgpass@postgres:5432/homayar\",\"PATH=/usr/local/bin:/usr/bin\"]"
  hj="{\"Status\":\"$he\"}"
  stj="{\"Status\":\"$st\",\"Running\":$running,\"ExitCode\":0,\"Health\":$hj}"
  obj="{\"Id\":\"cid-$s\",\"Name\":\"$nm\",\"Image\":\"$id\",\"RestartCount\":0,\"State\":$stj,\"Config\":{\"Image\":\"$ref\",\"Env\":$env,\"Labels\":{\"com.docker.compose.service\":\"$s\"}}}"
  if [[ -z "$fmt" ]]; then
    [[ "$first" == 1 ]] || printf ','
    printf '%s' "$obj"
    return 0
  fi
  TT="$(norm_fmt "$fmt")"
  sub '{{json .}}' "$obj"
  sub '{{.Image}}' "$id"
  sub '{{.Id}}' "cid-$s"
  sub '{{.ID}}' "cid-$s"
  sub '{{.Name}}' "$nm"
  sub '{{.RestartCount}}' 0
  sub '{{.State.Status}}' "$st"
  sub '{{.State.Running}}' "$running"
  sub '{{.State.ExitCode}}' 0
  sub '{{.State.Health.Status}}' "$he"
  sub '{{json .State}}' "$stj"
  sub '{{json .State.Health}}' "$hj"
  sub '{{.Config.Image}}' "$ref"
  sub '{{json .Config.Env}}' "$env"
  sub '{{.Config.Env}}' "$env"
  sub '{{index .Config.Labels "com.docker.compose.service"}}' "$s"
  finish_tt
}

img_out() {
  local r="$1" fmt="$2" first="$3" id sha ver ref repo dig labels obj
  ref="$(img_get "$r" 1)"
  id="$(img_get "$r" 2)"
  sha="$(img_get "$r" 4)"
  ver="$(img_get "$r" 5)"
  repo="${ref%:*}"
  dig="$repo@sha256:$(printf '%s' "$id" | sha256sum | cut -c1-64)"
  labels="{\"org.opencontainers.image.revision\":\"$sha\",\"org.opencontainers.image.version\":\"$ver\"}"
  obj="{\"Id\":\"$id\",\"RepoTags\":[\"$ref\"],\"RepoDigests\":[\"$dig\"],\"Created\":\"2026-11-01T00:00:00Z\",\"Size\":123456789,\"Architecture\":\"amd64\",\"Os\":\"linux\",\"Config\":{\"Labels\":$labels,\"Env\":[\"PATH=/usr/local/bin:/usr/bin\"]}}"
  if [[ -z "$fmt" ]]; then
    [[ "$first" == 1 ]] || printf ','
    printf '%s' "$obj"
    return 0
  fi
  TT="$(norm_fmt "$fmt")"
  sub '{{json .}}' "$obj"
  sub '{{.Id}}' "$id"
  sub '{{.ID}}' "$id"
  sub '{{json .RepoTags}}' "[\"$ref\"]"
  sub '{{.RepoTags}}' "[$ref]"
  sub '{{index .RepoTags 0}}' "$ref"
  sub '{{json .RepoDigests}}' "[\"$dig\"]"
  sub '{{.RepoDigests}}' "[$dig]"
  sub '{{index .RepoDigests 0}}' "$dig"
  sub '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$sha"
  sub '{{index .Config.Labels "org.opencontainers.image.version"}}' "$ver"
  sub '{{json .Config.Labels}}' "$labels"
  sub '{{.Config.Labels}}' "map[org.opencontainers.image.revision:$sha org.opencontainers.image.version:$ver]"
  sub '{{.Created}}' 2026-11-01T00:00:00Z
  sub '{{.Size}}' 123456789
  sub '{{.Architecture}}' amd64
  sub '{{.Os}}' linux
  finish_tt
}

inspect_cmd() {
  local mode="$1" fmt="" refs=() r rc=0 first=1 s
  shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -f|--format) fmt="$2"; shift 2 ;;
      --format=*) fmt="${1#*=}"; shift ;;
      --type) mode="$2"; shift 2 ;;
      --type=*) mode="${1#*=}"; shift ;;
      -*) shift ;;
      *) refs+=("$1"); shift ;;
    esac
  done
  [[ -z "$fmt" ]] && printf '['
  for r in "${refs[@]}"; do
    if [[ "$mode" != image ]] && s="$(cont_svc "$r")"; then
      cont_out "$s" "$fmt" "$first"
    elif [[ "$mode" != container ]] && img_known "$r"; then
      img_out "$r" "$fmt" "$first"
    else
      if [[ "$mode" == image ]]; then
        printf 'Error response from daemon: No such image: %s\n' "$r" >&2
      else
        printf 'Error: No such object: %s\n' "$r" >&2
      fi
      rc=1
      continue
    fi
    first=0
  done
  [[ -z "$fmt" ]] && printf ']\n'
  return "$rc"
}

images_cmd() {
  local fmt="" quiet=0 notrunc=0 want="" ref id repo tag showid head=1
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --format) fmt="$2"; shift 2 ;;
      --format=*) fmt="${1#*=}"; shift ;;
      -q|--quiet) quiet=1; shift ;;
      --no-trunc) notrunc=1; shift ;;
      -f|--filter) shift 2 ;;
      -*) shift ;;
      *) want="$1"; shift ;;
    esac
  done
  while IFS='|' read -r ref id _ _ _; do
    [[ -n "$ref" ]] || continue
    repo="${ref%:*}"
    tag="${ref##*:}"
    if [[ -n "$want" && "$want" != "$repo" && "$want" != "$ref" ]]; then continue; fi
    showid="${id#sha256:}"
    showid="${showid:0:12}"
    [[ "$notrunc" == 1 ]] && showid="$id"
    if [[ "$quiet" == 1 ]]; then
      printf '%s\n' "$showid"
    elif [[ -z "$fmt" ]]; then
      if [[ "$head" == 1 ]]; then printf 'REPOSITORY\tTAG\tIMAGE ID\tCREATED\tSIZE\n'; head=0; fi
      printf '%s\t%s\t%s\t12 days ago\t123MB\n' "$repo" "$tag" "$showid"
    else
      TT="$(norm_fmt "$fmt")"
      sub '{{json .}}' "{\"Repository\":\"$repo\",\"Tag\":\"$tag\",\"ID\":\"$showid\",\"Size\":\"123MB\"}"
      sub '{{.Repository}}' "$repo"
      sub '{{.Tag}}' "$tag"
      sub '{{.ID}}' "$showid"
      sub '{{.Digest}}' "sha256:$(printf '%s' "$id" | sha256sum | cut -c1-64)"
      sub '{{.Size}}' 123MB
      sub '{{.CreatedSince}}' "12 days ago"
      sub '{{.CreatedAt}}' "2026-11-01 00:00:00 +0000 UTC"
      finish_tt
    fi
  done <"$S/images"
  return 0
}

load_cmd() {
  local in="" tmpf="" lines l ref
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -i|--input) in="$2"; shift 2 ;;
      --input=*) in="${1#*=}"; shift ;;
      *) shift ;;
    esac
  done
  if [[ -z "$in" ]]; then
    tmpf="$S/load.$$.tar"
    cat >"$tmpf"
    in="$tmpf"
  fi
  if [[ -e "$S/fail-load" ]]; then
    echo "Error processing tar file: oracle injected" >&2
    [[ -n "$tmpf" ]] && rm -f "$tmpf"
    return 1
  fi
  if ! lines="$(python3 -I "$PYD/loadtar.py" "$in")" || [[ -z "$lines" ]]; then
    echo "Error processing tar file(exit status 1): unexpected EOF" >&2
    [[ -n "$tmpf" ]] && rm -f "$tmpf"
    return 1
  fi
  [[ -n "$tmpf" ]] && rm -f "$tmpf"
  while IFS= read -r l; do
    [[ -n "$l" ]] || continue
    ref="${l%%|*}"
    awk -F'|' -v r="$ref" '$1 != r' "$S/images" >"$S/images.tmp"
    mv -f "$S/images.tmp" "$S/images"
    printf '%s\n' "$l" >>"$S/images"
    ev "load $ref"
    printf 'Loaded image: %s\n' "$ref"
  done <<<"$lines"
  return 0
}

save_cmd() {
  local out="" refs=() r spec="[" sep=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -o|--output) out="$2"; shift 2 ;;
      --output=*) out="${1#*=}"; shift ;;
      -*) shift ;;
      *) refs+=("$1"); shift ;;
    esac
  done
  for r in "${refs[@]}"; do
    img_known "$r" || { printf 'Error response from daemon: reference does not exist: %s\n' "$r" >&2; return 1; }
    spec+="$sep{\"ref\":\"$(img_get "$r" 1)\",\"id\":\"$(img_get "$r" 2)\",\"head\":\"$(img_get "$r" 3)\",\"sha\":\"$(img_get "$r" 4)\",\"version\":\"$(img_get "$r" 5)\"}"
    sep=","
  done
  spec+="]"
  ev "save ${refs[*]}"
  if [[ -n "$out" ]]; then
    python3 -I "$PYD/mkimg.py" "$out" "$spec"
  else
    python3 -I "$PYD/mkimg.py" - "$spec"
  fi
}

tag_cmd() {
  local src="${1:-}" dst="${2:-}" row
  img_known "$src" || { printf 'Error response from daemon: No such image: %s\n' "$src" >&2; return 1; }
  row="$dst|$(img_get "$src" 2)|$(img_get "$src" 3)|$(img_get "$src" 4)|$(img_get "$src" 5)"
  printf '%s\n' "$row" >>"$S/images"
  return 0
}

cp_cmd() {
  local pos=() src dst
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --index) shift 2 ;;
      -*) shift ;;
      *) pos+=("$1"); shift ;;
    esac
  done
  src="${pos[0]:-}"
  dst="${pos[1]:-}"
  if [[ "$src" == *:/* && "$src" != /* ]]; then
    [[ -d "$dst" ]] && dst="$dst/$(basename -- "${src#*:}")"
    printf 'PGDMP oracle-dump version=%s\n' "$(cat "$S/dump-version" 2>/dev/null)" >"$dst"
  else
    cat >/dev/null 2>&1 || :
  fi
  return 0
}

logs_for() {
  local noprefix="$1" s p
  shift
  for s in "$@"; do
    p="$s-1  | "
    [[ "$noprefix" == 1 ]] && p=""
    printf '%s2026-11-13T01:00:00Z INFO request served oracle-log-line-text-canary\n' "$p"
    printf '%s2026-11-13T01:00:01Z ERROR login failed password=%sdb\n' "$p" "$MARK"
    printf '%s2026-11-13T01:00:02Z WARNING slow query from 10.77.0.9\n' "$p"
    printf '%s2026-11-13T01:00:03Z CRITICAL disk nearly full for oracle-person@example.org\n' "$p"
  done
}

compose_logs() {
  local svcs=() noprefix=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --since|--until|--tail|-n|--index) shift 2 ;;
      --no-log-prefix) noprefix=1; shift ;;
      -*) shift ;;
      *) svcs+=("$1"); shift ;;
    esac
  done
  [[ ${#svcs[@]} -gt 0 ]] || svcs=(proxy console-web api worker scheduler postgres redis minio)
  ev "logs ${svcs[*]}"
  logs_for "$noprefix" "${svcs[@]}"
}

compose_config() {
  local quiet=0 services=0 images=0 a f line
  for a in "$@"; do
    case "$a" in
      -q|--quiet) quiet=1 ;;
      --services) services=1 ;;
      --images) images=1 ;;
    esac
  done
  ev "config envfiles=${ENVFILES[*]:-} files=${CFILES[*]:-}"
  if [[ -e "$S/fail-config" ]]; then echo 'service "api" refers to undefined volume (oracle injected)' >&2; return 15; fi
  [[ "$quiet" == 1 ]] && return 0
  if [[ "$services" == 1 ]]; then printf '%s\n' "${ONPREM[@]}" migrate backup-tool egress-proxy; return 0; fi
  if [[ "$images" == 1 ]]; then printf '%s\n' "$(svc_ref api)" "$(svc_ref console-web)" "$(svc_ref egress-proxy)"; return 0; fi
  printf 'name: homayar-onprem\nservices:\n  api:\n    environment:\n'
  for f in "${ENVFILES[@]}"; do
    while IFS= read -r line; do
      [[ "$line" == *=* ]] || continue
      printf '      %s: "%s"\n' "${line%%=*}" "${line#*=}"
    done <"$f"
  done
  return 0
}

compose_up() {
  local nodeps=0 svcs=() list=() seen=" " s
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --no-deps) nodeps=1; shift ;;
      --pull|--wait-timeout|-t|--timeout|--exit-code-from|--scale|--attach|--no-attach) shift 2 ;;
      -*) shift ;;
      *) svcs+=("$1"); shift ;;
    esac
  done
  if [[ ${#svcs[@]} -eq 0 ]]; then
    profile_on onprem && svcs+=("${ONPREM[@]}")
    profile_on egress && svcs+=(egress-proxy)
    profile_on migrate && svcs+=(migrate)
    if [[ ${#svcs[@]} -eq 0 ]]; then echo "no service selected" >&2; return 1; fi
  fi
  if [[ -e "$S/fail-up" ]]; then echo "compose up failed (oracle injected)" >&2; return 1; fi
  if [[ "$nodeps" == 0 ]]; then
    for s in "${svcs[@]}"; do
      case "$s" in
        postgres|redis|minio|minio-bootstrap) ;;
        *) list+=(postgres redis minio minio-bootstrap); break ;;
      esac
    done
  fi
  list+=("${svcs[@]}")
  for s in "${list[@]}"; do
    [[ "$seen" == *" $s "* ]] && continue
    seen+="$s "
    if [[ "$s" == migrate ]]; then do_migrate "$(svc_ref migrate)" || return 1; continue; fi
    start_one "$s" || return 1
  done
  return 0
}

compose_start() {
  local svcs=() s
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -t|--timeout) shift 2 ;;
      -*) shift ;;
      *) svcs+=("$1"); shift ;;
    esac
  done
  if [[ ${#svcs[@]} -eq 0 ]]; then mapfile -t svcs < <(all_existing); fi
  for s in "${svcs[@]}"; do start_existing "$s" || return 1; done
  return 0
}

compose_stop() {
  local svcs=() s
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -t|--timeout|-s|--signal) shift 2 ;;
      -*) shift ;;
      *) svcs+=("$1"); shift ;;
    esac
  done
  if [[ ${#svcs[@]} -eq 0 ]]; then mapfile -t svcs < <(all_existing); fi
  for s in "${svcs[@]}"; do stop_one "$s"; done
  return 0
}

compose_down() {
  local a s
  for a in "$@"; do
    case "$a" in -v|--volumes) destructive "compose down $*" ;; esac
  done
  while IFS= read -r s; do
    [[ -n "$s" ]] || continue
    stop_one "$s"
    del_svc "$s"
  done < <(all_existing)
  return 0
}

compose_rm() {
  local svcs=() stop=0 s
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -s|--stop) stop=1; shift ;;
      -v|--volumes) destructive "compose rm $*"; shift ;;
      -*) shift ;;
      *) svcs+=("$1"); shift ;;
    esac
  done
  if [[ ${#svcs[@]} -eq 0 ]]; then mapfile -t svcs < <(all_existing); fi
  for s in "${svcs[@]}"; do
    if svc_running "$s"; then
      [[ "$stop" == 1 ]] || continue
      stop_one "$s"
    fi
    del_svc "$s"
  done
  return 0
}

compose_run() {
  local nodeps=0 svc="" d ref
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --no-deps) nodeps=1; shift ;;
      -e|--env|-v|--volume|--entrypoint|--name|-u|--user|-w|--workdir|-l|--label|--pull|--cap-add|--cap-drop|-p|--publish|--env-from-file) shift 2 ;;
      -*) shift ;;
      *) svc="$1"; shift; break ;;
    esac
  done
  [[ -n "$svc" ]] || { echo "compose run: service name required" >&2; return 1; }
  if [[ "$nodeps" == 0 ]]; then
    case "$svc" in
      api|worker|scheduler|migrate|backup-tool|tombstone-reapply|console-web)
        for d in postgres redis minio; do svc_running "$d" || start_one "$d" || return 1; done
        ;;
    esac
  fi
  if [[ "$svc" == migrate ]]; then do_migrate "$(svc_ref migrate)"; return; fi
  ref="$(svc_ref "$svc")"
  if own_image "$svc" && ! img_known "$ref"; then missing_image "$ref"; return 1; fi
  app_cmd "$ref" "$svc" "$@"
}

compose_exec() {
  local svc=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -u|--user|-e|--env|-w|--workdir|--index) shift 2 ;;
      -*) shift ;;
      *) svc="$1"; shift; break ;;
    esac
  done
  svc_exec "$svc" "$@"
}

compose_images() {
  local fmt="" quiet=0 want=() first=1 w hit
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --format) fmt="$2"; shift 2 ;;
      --format=*) fmt="${1#*=}"; shift ;;
      -q|--quiet) quiet=1; shift ;;
      -*) shift ;;
      *) want+=("$1"); shift ;;
    esac
  done
  [[ "$fmt" == json ]] && printf '['
  [[ -z "$fmt" && "$quiet" == 0 ]] && printf 'CONTAINER\tREPOSITORY\tTAG\tIMAGE ID\tSIZE\n'
  while IFS='|' read -r F_SERVICE F_STATE F_HEALTH F_EXIT F_IMAGE; do
    [[ -n "$F_SERVICE" ]] || continue
    if [[ ${#want[@]} -gt 0 ]]; then
      hit=0
      for w in "${want[@]}"; do [[ "$w" == "$F_SERVICE" ]] && hit=1; done
      [[ "$hit" == 1 ]] || continue
    fi
    local id
    id="$(ref_id "$F_IMAGE")"
    if [[ "$quiet" == 1 ]]; then
      printf '%s\n' "$id"
    elif [[ "$fmt" == json ]]; then
      [[ "$first" == 1 ]] || printf ','
      printf '{"ContainerName":"homayar-onprem-%s-1","Repository":"%s","Tag":"%s","ID":"%s","Size":123456789}' \
        "$F_SERVICE" "${F_IMAGE%:*}" "${F_IMAGE##*:}" "$id"
      first=0
    else
      printf 'homayar-onprem-%s-1\t%s\t%s\t%s\t123MB\n' "$F_SERVICE" "${F_IMAGE%:*}" "${F_IMAGE##*:}" "${id:7:12}"
    fi
  done <"$S/svc"
  [[ "$fmt" == json ]] && printf ']\n'
  return 0
}

compose_main() {
  local f sub
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --env-file) ENVFILES+=("$2"); shift 2 ;;
      --env-file=*) ENVFILES+=("${1#*=}"); shift ;;
      -f|--file) CFILES+=("$2"); shift 2 ;;
      --file=*) CFILES+=("${1#*=}"); shift ;;
      --profile) PROFILES+=("$2"); shift 2 ;;
      --profile=*) PROFILES+=("${1#*=}"); shift ;;
      -p|--project-name|--project-directory|--ansi|--progress|--parallel) shift 2 ;;
      -*) shift ;;
      *) break ;;
    esac
  done
  if [[ ${#CFILES[@]} -eq 0 && -n "${COMPOSE_FILE:-}" ]]; then IFS=: read -r -a CFILES <<<"$COMPOSE_FILE"; fi
  if [[ ${#ENVFILES[@]} -eq 0 && -n "${COMPOSE_ENV_FILES:-}" ]]; then IFS=, read -r -a ENVFILES <<<"$COMPOSE_ENV_FILES"; fi
  for f in "${CFILES[@]}"; do
    [[ -f "$f" ]] || { printf 'open %s: no such file or directory\n' "$f" >&2; return 1; }
  done
  for f in "${ENVFILES[@]}"; do
    [[ -f "$f" ]] || { printf "couldn't find env file: %s\n" "$f" >&2; return 1; }
  done
  sub="${1:-}"
  [[ $# -gt 0 ]] && shift
  case "$sub" in
    version) printf 'Docker Compose version v2.29.7\n' ;;
    config) compose_config "$@" ;;
    ps) compose_ps "$@" ;;
    up) compose_up "$@" ;;
    start|restart|unpause) compose_start "$@" ;;
    stop|kill|pause) compose_stop "$@" ;;
    down) compose_down "$@" ;;
    rm) compose_rm "$@" ;;
    run) compose_run "$@" ;;
    exec) compose_exec "$@" ;;
    cp) cp_cmd "$@" ;;
    logs) compose_logs "$@" ;;
    images) compose_images "$@" ;;
    pull) ev "pull compose $*"; echo "pull: no network (oracle)" >&2; return 1 ;;
    ls) printf 'NAME\tSTATUS\tCONFIG FILES\nhomayar-onprem\trunning(8)\tcompose.onprem.yaml\n' ;;
    *) return 0 ;;
  esac
}

info_cmd() {
  local fmt="" root="$S/dockerroot" obj
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -f|--format) fmt="$2"; shift 2 ;;
      --format=*) fmt="${1#*=}"; shift ;;
      *) shift ;;
    esac
  done
  obj="{\"Name\":\"oracle-host-canary\",\"ServerVersion\":\"27.3.1\",\"DockerRootDir\":\"$root\",\"Driver\":\"overlay2\",\"OperatingSystem\":\"Ubuntu 24.04 LTS\",\"Swarm\":{\"NodeAddr\":\"10.77.0.5\"}}"
  if [[ -z "$fmt" ]]; then
    printf 'Client:\n Version:    27.3.1\n\nServer:\n Server Version: 27.3.1\n Storage Driver: overlay2\n Docker Root Dir: %s\n Name: oracle-host-canary\n Operating System: Ubuntu 24.04 LTS\n Node Address: 10.77.0.5\n' "$root"
    return 0
  fi
  TT="$(norm_fmt "$fmt")"
  sub '{{json .}}' "$obj"
  sub '{{.DockerRootDir}}' "$root"
  sub '{{.ServerVersion}}' 27.3.1
  sub '{{.Name}}' oracle-host-canary
  sub '{{.Driver}}' overlay2
  sub '{{.OperatingSystem}}' "Ubuntu 24.04 LTS"
  sub '{{.Architecture}}' x86_64
  sub '{{.NCPU}}' 8
  sub '{{.MemTotal}}' 67108864000
  finish_tt
}

version_cmd() {
  local fmt="" obj
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -f|--format) fmt="$2"; shift 2 ;;
      --format=*) fmt="${1#*=}"; shift ;;
      *) shift ;;
    esac
  done
  obj='{"Client":{"Version":"27.3.1","ApiVersion":"1.47"},"Server":{"Version":"27.3.1","ApiVersion":"1.47","Os":"linux","Arch":"amd64"}}'
  if [[ -z "$fmt" ]]; then
    printf 'Client: Docker Engine - Community\n Version:           27.3.1\n\nServer: Docker Engine - Community\n Engine:\n  Version:          27.3.1\n'
    return 0
  fi
  TT="$(norm_fmt "$fmt")"
  sub '{{json .}}' "$obj"
  sub '{{json .Server}}' '{"Version":"27.3.1","ApiVersion":"1.47","Os":"linux","Arch":"amd64"}'
  sub '{{json .Client}}' '{"Version":"27.3.1","ApiVersion":"1.47"}'
  sub '{{.Server.Version}}' 27.3.1
  sub '{{.Client.Version}}' 27.3.1
  sub '{{.Server.APIVersion}}' 1.47
  sub '{{.Client.APIVersion}}' 1.47
  sub '{{.Server.Os}}' linux
  sub '{{.Server.Arch}}' amd64
  finish_tt
}

drun_cmd() {
  local image=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --network|--net|--entrypoint|-v|--volume|--mount|-e|--env|--env-file|-u|--user|-w|--workdir|--name|--security-opt|--cap-drop|--cap-add|--tmpfs|--pull|--platform|-l|--label|-m|--memory|--cpus|--gpus|--ulimit|--add-host|--dns|--log-driver|--log-opt|--stop-timeout|--shm-size|--pids-limit|--restart|-h|--hostname|--group-add|--device|--runtime|--ipc|--pid|--memory-swap|--cpu-period|--cpu-quota|--stop-signal|--uts|--userns) shift 2 ;;
      -*) shift ;;
      *) image="$1"; shift; break ;;
    esac
  done
  if ! img_known "$image"; then
    printf "Unable to find image '%s' locally\ndocker: Error response from daemon: pull access denied (oracle: no network)\n" "$image" >&2
    return 125
  fi
  app_cmd "$image" run "$@"
}

dexec_cmd() {
  local c="" s
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -u|--user|-e|--env|-w|--workdir|--env-file) shift 2 ;;
      -*) shift ;;
      *) c="$1"; shift; break ;;
    esac
  done
  s="$(cont_svc "$c")" || { printf 'Error response from daemon: No such container: %s\n' "$c" >&2; return 1; }
  svc_exec "$s" "$@"
}

dlogs_cmd() {
  local c="" s
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --since|--until|--tail|-n) shift 2 ;;
      -*) shift ;;
      *) c="$1"; shift ;;
    esac
  done
  s="$(cont_svc "$c")" || { printf 'Error response from daemon: No such container: %s\n' "$c" >&2; return 1; }
  ev "logs $s"
  logs_for 1 "$s"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context|-c|-H|--host|--config|-l|--log-level) shift 2 ;;
    -D|--debug) shift ;;
    *) break ;;
  esac
done
cmd="${1:-}"
[[ $# -gt 0 ]] && shift
case "$cmd" in
  --version|-v) echo "Docker version 27.3.1, build oracle1"; exit 0 ;;
  version) version_cmd "$@"; exit $? ;;
  info) info_cmd "$@"; exit $? ;;
  compose) compose_main "$@"; exit $? ;;
  image)
    sc="${1:-}"
    [[ $# -gt 0 ]] && shift
    case "$sc" in
      inspect) inspect_cmd image "$@"; exit $? ;;
      ls|list) images_cmd "$@"; exit $? ;;
      load) load_cmd "$@"; exit $? ;;
      save) save_cmd "$@"; exit $? ;;
      tag) tag_cmd "$@"; exit $? ;;
      rm|remove|prune) destructive "image $sc $*"; exit 0 ;;
      pull) ev "pull $*"; echo "pull: no network (oracle)" >&2; exit 1 ;;
      *) exit 0 ;;
    esac
    ;;
  container)
    sc="${1:-}"
    [[ $# -gt 0 ]] && shift
    case "$sc" in
      inspect) inspect_cmd container "$@"; exit $? ;;
      ls|list|ps) ps_cmd "$@"; exit $? ;;
      logs) dlogs_cmd "$@"; exit $? ;;
      exec) dexec_cmd "$@"; exit $? ;;
      prune) destructive "container prune"; exit 0 ;;
      *) exit 0 ;;
    esac
    ;;
  inspect) inspect_cmd any "$@"; exit $? ;;
  images) images_cmd "$@"; exit $? ;;
  load) load_cmd "$@"; exit $? ;;
  save) save_cmd "$@"; exit $? ;;
  tag) tag_cmd "$@"; exit $? ;;
  rmi) destructive "rmi $*"; exit 0 ;;
  run) drun_cmd "$@"; exit $? ;;
  exec) dexec_cmd "$@"; exit $? ;;
  ps) ps_cmd "$@"; exit $? ;;
  logs) dlogs_cmd "$@"; exit $? ;;
  cp) cp_cmd "$@"; exit $? ;;
  pull) ev "pull $*"; echo "pull: no network (oracle)" >&2; exit 1 ;;
  volume)
    sc="${1:-}"
    case "$sc" in
      rm|remove|prune) destructive "volume $*"; exit 0 ;;
      ls) echo "homayar-onprem_postgres-data"; exit 0 ;;
      *) exit 0 ;;
    esac
    ;;
  system|builder)
    sc="${1:-}"
    [[ "$sc" == prune ]] && { destructive "$cmd prune"; exit 0; }
    [[ "$sc" == df ]] && { printf 'TYPE\tTOTAL\tACTIVE\tSIZE\tRECLAIMABLE\n'; exit 0; }
    exit 0
    ;;
  *) exit 0 ;;
esac
DOCKEREOF
} >"$BIN/docker"

{
  printf '#!/usr/bin/env bash\n'
  printf 'S=%q\n' "$STUB"
  cat <<'CURLEOF'
set -u
{
  printf 'curl'
  for a in "$@"; do printf ' %s' "${a//$'\n'/ }"; done
  printf '\n'
} >>"$S/calls"
out=""
fmt=""
fail=0
url=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output) out="$2"; shift 2 ;;
    -w|--write-out) fmt="$2"; shift 2 ;;
    -m|--max-time|--connect-timeout|--retry|--retry-delay|--retry-max-time|-H|--header|-X|--request|-A|--user-agent|-d|--data|--data-binary|--data-raw|-u|--user|--resolve|-e|--referer|--cacert|--cert|--key|-x|--proxy|--noproxy|--unix-socket) shift 2 ;;
    --fail|--fail-with-body) fail=1; shift ;;
    --*) shift ;;
    -*)
      [[ "$1" == *f* ]] && fail=1
      if [[ "$1" == *o ]]; then out="$2"; shift 2
      elif [[ "$1" == *w ]]; then fmt="$2"; shift 2
      elif [[ "$1" == *m ]]; then shift 2
      else shift; fi
      ;;
    *) url="$1"; shift ;;
  esac
done
running() { awk -F'|' -v s="$1" '$1 == s && $2 == "running" { f = 1 } END { exit !f }' "$S/svc" 2>/dev/null; }
path="${url#*://}"
if [[ "$path" == */* ]]; then path="/${path#*/}"; else path="/"; fi
code=000
body=""
case "$path" in
  /healthz*) if running proxy; then code=200; body='{"status":"ok"}'; fi ;;
  /readyz*)
    if running proxy; then
      if running api && [[ ! -e "$S/readyz-down" ]]; then code=200; body='{"status":"ready"}'; else code=503; body='{"status":"not_ready"}'; fi
    fi
    ;;
  *) if running proxy; then code=404; body='{"detail":"Not Found"}'; fi ;;
esac
p1='%{http_code}'
p2='%{response_code}'
p3='%{content_type}'
p4='\n'
render() {
  local t="$fmt"
  t="${t//"$p1"/$code}"
  t="${t//"$p2"/$code}"
  t="${t//"$p3"/application/json}"
  t="${t//"$p4"/$'\n'}"
  printf '%s' "$t"
}
if [[ "$code" == 000 ]]; then
  [[ -n "$fmt" ]] && render
  echo "curl: (7) Failed to connect to server: Connection refused" >&2
  exit 7
fi
if [[ -n "$out" ]]; then printf '%s' "$body" >"$out"; else printf '%s' "$body"; fi
[[ -n "$fmt" ]] && render
if [[ "$fail" == 1 && "$code" -ge 400 ]]; then
  echo "curl: (22) The requested URL returned error: $code" >&2
  exit 22
fi
exit 0
CURLEOF
} >"$BIN/curl"

{
  printf '#!/usr/bin/env bash\n'
  printf 'S=%q\n' "$STUB"
  cat <<'GITEOF'
set -u
{
  printf 'git'
  for a in "$@"; do printf ' %s' "$a"; done
  printf '\n'
} >>"$S/calls"
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
dirty=0
[[ -e "$dir/.oracle-git-dirty" ]] && dirty=1
case "$sc" in
  rev-parse)
    case " $* " in
      *--show-toplevel*) printf '%s\n' "$dir" ;;
      *--is-inside-work-tree*) echo true ;;
      *--short*) printf '%s\n' "${sha:0:12}" ;;
      *) printf '%s\n' "$sha" ;;
    esac
    ;;
  log|show) printf '%s\n' "$sha" ;;
  status) [[ "$dirty" == 1 ]] && printf ' M infra/compose/compose.onprem.yaml\n' ;;
  diff|diff-index|diff-files)
    if [[ "$dirty" == 1 ]]; then
      case " $* " in *--quiet*|*--exit-code*) exit 1 ;; esac
      printf 'diff --git a/infra/compose/compose.onprem.yaml b/infra/compose/compose.onprem.yaml\n'
    fi
    ;;
  update-index) [[ "$dirty" == 1 ]] && exit 1 ;;
  describe) printf 'v0.7.0\n' ;;
esac
exit 0
GITEOF
} >"$BIN/git"

cat >"$BIN/uname" <<'UNAMEEOF'
#!/usr/bin/env bash
flags=""
for a in "$@"; do
  case "$a" in
    --all) flags+="a" ;;
    --kernel-name) flags+="s" ;;
    --nodename) flags+="n" ;;
    --kernel-release) flags+="r" ;;
    --kernel-version) flags+="v" ;;
    --machine) flags+="m" ;;
    --operating-system) flags+="o" ;;
    -*) flags+="${a#-}" ;;
  esac
done
[[ -n "$flags" ]] || flags="s"
[[ "$flags" == *a* ]] && flags="snrvmo"
out=()
[[ "$flags" == *s* ]] && out+=(Linux)
[[ "$flags" == *n* ]] && out+=(oracle-host-canary)
[[ "$flags" == *r* ]] && out+=(6.8.0-49-oracle)
[[ "$flags" == *v* ]] && out+=("#49-Ubuntu SMP PREEMPT_DYNAMIC")
[[ "$flags" == *m* ]] && out+=(x86_64)
[[ "$flags" == *p* ]] && out+=(x86_64)
[[ "$flags" == *i* ]] && out+=(x86_64)
[[ "$flags" == *o* ]] && out+=(GNU/Linux)
printf '%s\n' "${out[*]}"
UNAMEEOF

cat >"$BIN/hostname" <<'HOSTEOF'
#!/usr/bin/env bash
case "${1:-}" in
  -I|--all-ip-addresses|-i|--ip-address) echo 10.77.0.5 ;;
  *) echo oracle-host-canary ;;
esac
HOSTEOF

{
  printf '#!/usr/bin/env bash\n'
  printf 'S=%q\n' "$STUB"
  cat <<'SMIEOF'
{
  printf 'nvidia-smi'
  for a in "$@"; do printf ' %s' "$a"; done
  printf '\n'
} >>"$S/calls"
case " $* " in
  *compute-apps*)
    echo "41234, python oracle-gpu-process-canary, 480 MiB"
    ;;
  *--query-gpu*)
    [[ " $* " == *noheader* ]] || echo "name, driver_version, memory.total [MiB], memory.used [MiB]"
    echo "Tesla P40, 535.183.01, 23040 MiB, 512 MiB"
    ;;
  *)
    cat <<'TABLE'
+---------------------------------------------------------------------------------------+
| NVIDIA-SMI 535.183.01             Driver Version: 535.183.01   CUDA Version: 12.2     |
|   0  Tesla P40                      Off | 00000000:3B:00.0 Off |                    0 |
| Processes:                                                                            |
|    0   N/A  N/A     41234      C   python oracle-gpu-process-canary          480MiB |
+---------------------------------------------------------------------------------------+
TABLE
    ;;
esac
exit 0
SMIEOF
} >"$BIN/nvidia-smi"

printf '#!/usr/bin/env bash\nexec docker compose "$@"\n' >"$BIN/docker-compose"
chmod 755 "$BIN"/*

write_ops_stub() {
  {
    printf '#!/usr/bin/env bash\n'
    printf 'S=%q\nKIND=%q\n' "$STUB" "$2"
    cat <<'OPSEOF'
here="$(cd -- "$(dirname -- "$0")" && pwd -P)/$(basename -- "$0")"
{
  printf 'script=%s\n' "$here"
  printf 'args=%s\n' "$*"
  printf 'compose_env_file=%s\n' "${COMPOSE_ENV_FILE:-}"
} >"$S/$KIND-called"
rc="$(cat "$S/$KIND-rc" 2>/dev/null || echo 0)"
exit "$rc"
OPSEOF
  } >"$1"
  chmod 755 "$1"
}

write_infra() {
  local d="$1" tag="$2"
  mkdir -p "$d/infra/compose" "$d/infra/scripts/lib" "$d/infra/nginx/conf.d" "$d/infra/nginx/includes" \
    "$d/infra/manifests" "$d/infra/egress"
  printf 'name: homayar-onprem\n# release %s\nservices:\n  api:\n    image: "${API_IMAGE:-homayar/api:local}"\n    profiles: [onprem]\n' "$tag" \
    >"$d/infra/compose/compose.onprem.yaml"
  printf 'worker_processes 1;\n' >"$d/infra/nginx/nginx.conf"
  printf 'server { listen 8080; }\n' >"$d/infra/nginx/conf.d/homayar.conf"
  printf 'add_header X-Oracle-Release %s;\n' "$tag" >"$d/infra/nginx/includes/oracle.conf"
  printf '# model manifests %s\n' "$tag" >"$d/infra/manifests/README.md"
  printf 'example.invalid\n' >"$d/infra/egress/allowlist.txt"
  printf '#!/usr/bin/env bash\n# compose lib %s\n' "$tag" >"$d/infra/scripts/lib/compose.sh"
  write_ops_stub "$d/infra/scripts/backup.sh" backup
  write_ops_stub "$d/infra/scripts/restore-isolated.sh" restore
}

img_spec() {
  printf '[{"ref":"%s","id":"%s","head":"%s","sha":"%s","version":"%s"}]' "$1" "$2" "$3" "$4" "$5"
}

write_sums() {
  local d="$1"
  (
    cd "$d" || exit 1
    find . -type f ! -path ./SHA256SUMS ! -path ./SHA256SUMS.sig | sed 's|^\./||' | LC_ALL=C sort | while IFS= read -r f; do
      sha256sum -- "$f"
    done >"$T/sums.tmp"
  )
  mv -f "$T/sums.tmp" "$d/SHA256SUMS"
  sign_pkg "$d"
}

write_release_env() {
  printf 'HOMAYAR_RELEASE_VERSION=%s\nHOMAYAR_RELEASE_SHA=%s\nAPI_IMAGE=homayar/api:%s\nCONSOLE_WEB_IMAGE=homayar/console-web:%s\nEGRESS_PROXY_IMAGE=homayar/egress-proxy:%s\n' \
    "$2" "$3" "$2" "$2" "$2" >"$1/release.env"
}

make_pkg() {
  local d="$1" v="$2" sha="$3" head="$4" ida="$5" idc="$6" ide="$7"
  rm -rf -- "${d:?}"
  mkdir -p "$d/images"
  write_infra "$d" "$v"
  python3 -I "$PY/mkimg.py" "$d/images/api.tar" "$(img_spec "homayar/api:$v" "$ida" "$head" "$sha" "$v")"
  python3 -I "$PY/mkimg.py" "$d/images/console-web.tar" "$(img_spec "homayar/console-web:$v" "$idc" "$head" "$sha" "$v")"
  python3 -I "$PY/mkimg.py" "$d/images/egress-proxy.tar" "$(img_spec "homayar/egress-proxy:$v" "$ide" "$head" "$sha" "$v")"
  write_release_env "$d" "$v" "$sha"
  {
    printf 'schema homayar-offline-bundle/1\n'
    printf 'release %s %s\n' "$v" "$sha"
    printf 'image images/api.tar %s homayar/api:%s LicenseRef-Homayar-Proprietary\n' \
      "$(sha256sum "$d/images/api.tar" | cut -d' ' -f1)" "$v"
    printf 'image images/console-web.tar %s homayar/console-web:%s LicenseRef-Homayar-Proprietary\n' \
      "$(sha256sum "$d/images/console-web.tar" | cut -d' ' -f1)" "$v"
    printf 'image images/egress-proxy.tar %s homayar/egress-proxy:%s LicenseRef-Homayar-Proprietary\n' \
      "$(sha256sum "$d/images/egress-proxy.tar" | cut -d' ' -f1)" "$v"
    printf 'requires bash >=4.4\n'
    printf 'requires openssl >=3.0\n'
    printf 'requires docker-engine >=27.3.1\n'
    printf 'requires docker-compose-plugin >=2.29.7\n'
    printf 'requires nvidia-container-toolkit gpu-profile-only\n'
  } >"$d/BUNDLE.manifest"
  write_sums "$d"
}

PKG5="$PKGS/p050"
PKG5X="$PKGS/p050-other-sha"
PKG6="$PKGS/p060"
make_pkg "$PKG5" "$V5" "$SHA5" "$H5" "$IDA5" "$IDC5" "$IDE5"
make_pkg "$PKG5X" "$V5" "$SHA5X" "$H5" "$IDA5" "$IDC5" "$IDE5"
make_pkg "$PKG6" "$V6" "$SHA6" "$H6" "$IDA6" "$IDC6" "$IDE6"

deny_copy() {
  local d="$PKGS/$1"
  rm -rf -- "${d:?}"
  cp -R -p "$PKG5" "$d"
  printf '%s' "$d"
}

D_TAMPER="$(deny_copy deny-tamper)"
printf '# tampered after hashing\n' >>"$D_TAMPER/infra/compose/compose.onprem.yaml"

D_EXTRA="$(deny_copy deny-extra)"
printf 'server { listen 9999; }\n' >"$D_EXTRA/infra/nginx/conf.d/extra-unlisted.conf"

D_MISSING="$(deny_copy deny-missing)"
rm -f "$D_MISSING/infra/nginx/includes/oracle.conf"

D_DOTDOT="$(deny_copy deny-dotdot)"
printf 'outside the package\n' >"$PKGS/outside-dotdot.txt"
printf '%s  ../outside-dotdot.txt\n' "$(sha256sum "$PKGS/outside-dotdot.txt" | cut -d' ' -f1)" >>"$D_DOTDOT/SHA256SUMS"
sign_pkg "$D_DOTDOT" # the appended line is signed so the path rule is what refuses

D_ABS="$(deny_copy deny-abs)"
printf 'absolute path target\n' >"$PKGS/outside-abs.txt"
printf '%s  %s\n' "$(sha256sum "$PKGS/outside-abs.txt" | cut -d' ' -f1)" "$PKGS/outside-abs.txt" >>"$D_ABS/SHA256SUMS"
sign_pkg "$D_ABS" # the appended line is signed so the path rule is what refuses

D_BADLINE="$(deny_copy deny-badline)"
printf 'this line is not a checksum line\n' >>"$D_BADLINE/SHA256SUMS"
sign_pkg "$D_BADLINE" # the appended line is signed so the line rule is what refuses

relenv_variant() {
  local d
  d="$(deny_copy "$1")"
  python3 -I -c '
import sys
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path, encoding="utf-8").read().splitlines()
out = []
for line in lines:
    if old and line.startswith(old + "="):
        if new:
            out.append(new)
        continue
    out.append(line)
if not old:
    out.append(new)
open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
' "$d/release.env" "$2" "$3"
  write_sums "$d"
  printf '%s' "$d"
}

D_RE_EXTRA="$(relenv_variant deny-relenv-extra "" "EXTRA_SETTING=1")"
D_RE_LATEST="$(relenv_variant deny-relenv-latest API_IMAGE "API_IMAGE=homayar/api:latest")"
D_RE_LOCAL="$(relenv_variant deny-relenv-local CONSOLE_WEB_IMAGE "CONSOLE_WEB_IMAGE=homayar/console-web:local")"
D_RE_NOTAG="$(relenv_variant deny-relenv-notag EGRESS_PROXY_IMAGE "EGRESS_PROXY_IMAGE=homayar/egress-proxy")"
D_RE_BADSHA="$(relenv_variant deny-relenv-badsha HOMAYAR_RELEASE_SHA "HOMAYAR_RELEASE_SHA=${SHA5:0:39}")"
D_RE_MISSING="$(relenv_variant deny-relenv-missing EGRESS_PROXY_IMAGE "")"
SOURCED_PROOF="$T/release-env-was-sourced"
D_RE_SUBST="$(relenv_variant deny-relenv-subst HOMAYAR_RELEASE_VERSION "HOMAYAR_RELEASE_VERSION=\$(touch $SOURCED_PROOF)")"

D6_TAMPER="$PKGS/deny-p060-tamper"
cp -R -p "$PKG6" "$D6_TAMPER"
printf '# tampered after hashing\n' >>"$D6_TAMPER/infra/nginx/nginx.conf"

write_env() {
  cat >"$HOMEF/config/onprem.env" <<ENVEOF
APP_ENVIRONMENT=production
PUBLIC_ORIGIN=https://homayar.customer.example
CORS_ORIGINS=https://homayar.customer.example
TRUSTED_HOSTS=homayar.customer.example
BIND_ADDRESS=127.0.0.1
HTTP_PORT=8080
DEPLOYMENT_TOPOLOGY=single-host-cpu
POSTGRES_DB=homayar
POSTGRES_USER=homayar
POSTGRES_PASSWORD=${MARKER}pgpass
DATABASE_URL=postgresql+psycopg://homayar:${MARKER}pgpass@postgres:5432/homayar
REDIS_PASSWORD=${MARKER}redis
REDIS_URL=redis://:${MARKER}redis@redis:6379/0
JWT_SECRET=${MARKER}jwtXyzXyzXyzXyzXyzXyzXyzXyzXyz
TOKEN_PEPPER=${MARKER}pepperXyzXyzXyzXyzXyzXyzXyz
MINIO_ROOT_USER=${MARKER}rootuser
MINIO_ROOT_PASSWORD=${MARKER}rootpass
STORAGE_ACCESS_KEY=${MARKER}access
STORAGE_SECRET_KEY=${MARKER}secret
METRICS_BEARER_TOKEN=${MARKER}metricsXyzXyzXyzXyzXyz
NODE_EXPORTER_TEXTFILE_DIR=${TEXTFILE}
BACKUP_ROOT=${T}/backups
ENVEOF
  chmod 600 "$HOMEF/config/onprem.env"
}

fresh_world() {
  rm -rf -- "${HOMEF:?}" "${STUB:?}" "${RUNTMP:?}" "${TEXTFILE:?}"
  mkdir -p "$HOMEF/config" "$STUB/dockerroot" "$RUNTMP" "$TEXTFILE" "$T/backups"
  mkdir -p "$HOMEF/config/bundle-keys"
  cp "$PUB" "$HOMEF/config/bundle-keys/bundle-key-oracle.pem"
  write_env
  : >"$STUB/calls"
  : >"$STUB/events"
  : >"$STUB/images"
  : >"$STUB/svc"
  : >"$STUB/db"
}

add_img() { printf '%s|%s|%s|%s|%s\n' "$1" "$2" "$3" "$4" "$5" >>"$STUB/images"; }

gauge() {
  local f="$TEXTFILE/homayar_backup.prom" ts t
  rm -f "$f"
  [[ "$1" == missing ]] && return 0
  ts=$(($(date +%s) - $1))
  {
    printf '# HELP homayar_backup_last_success_timestamp_seconds Unix time of the last successful Homayar backup per target.\n'
    printf '# TYPE homayar_backup_last_success_timestamp_seconds gauge\n'
    for t in postgres objects offsite; do
      printf 'homayar_backup_last_success_timestamp_seconds{target="%s"} %s.250\n' "$t" "$ts"
    done
  } >"$f"
}

prepare_installed() {
  fresh_world
  mkdir -p "$HOMEF/releases" "$HOMEF/state" "$HOMEF/ops"
  chmod 700 "$HOMEF/state"
  cp -R -p "$PKG5" "$HOMEF/releases/$V5"
  ln -s "releases/$V5" "$HOMEF/current"
  add_img "homayar/api:$V5" "$IDA5" "$H5" "$SHA5" "$V5"
  add_img "homayar/console-web:$V5" "$IDC5" "$H5" "$SHA5" "$V5"
  add_img "homayar/egress-proxy:$V5" "$IDE5" "$H5" "$SHA5" "$V5"
  cat >"$STUB/svc" <<SVCEOF
proxy|running|healthy||proxy:oracle-third-party
console-web|running|healthy||homayar/console-web:$V5
api|running|healthy||homayar/api:$V5
worker|running|healthy||homayar/api:$V5
scheduler|running|||homayar/api:$V5
postgres|running|healthy||postgres:oracle-third-party
redis|running|healthy||redis:oracle-third-party
minio|running|healthy||minio:oracle-third-party
minio-bootstrap|exited||0|minio-bootstrap:oracle-third-party
SVCEOF
  printf '%s\n' "$H5" >"$STUB/db"
  gauge 3600
}

RUN_N=0
RC=0
LAST_OUT=""
LAST_ERR=""
RUN_MIN_DISK=1
CASE=""
FAILS=()
CASE_OUTS=()
BUNDLE_X=""
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
      MIN_DISK_GIB="$RUN_MIN_DISK" NODE_EXPORTER_TEXTFILE_DIR="$TEXTFILE"
    exec timeout 300 bash "$SUBJECT" "$@"
  ) </dev/null >"$LAST_OUT" 2>"$LAST_ERR"
  RC=$?
  RUN_MIN_DISK=1
}

begin() {
  CASE="$1"
  FAILS=()
  CASE_OUTS=()
  BUNDLE_X=""
}

ok() {
  local d="$1"
  shift
  if ! "$@"; then FAILS+=("$d"); fi
}

marker_leak() {
  local f
  LEAK=""
  for f in "${CASE_OUTS[@]}"; do
    if [[ -f "$f" ]] && grep -qaF -- "$MARKER" "$f"; then LEAK="run output $(basename "$f")"; return 0; fi
  done
  if [[ -d "$HOMEF/ops" ]] && grep -rqaF -- "$MARKER" "$HOMEF/ops"; then LEAK="a file under ops/"; return 0; fi
  if [[ -d "$HOMEF/state" ]] && grep -rqaF --include=record.env -- "$MARKER" "$HOMEF/state"; then LEAK="a state record"; return 0; fi
  if [[ -d "$RUNTMP" ]] && grep -rqaF -- "$MARKER" "$RUNTMP"; then LEAK="a file left in TMPDIR"; return 0; fi
  if [[ -n "$BUNDLE_X" && -d "$BUNDLE_X" ]] && grep -rqaF -- "$MARKER" "$BUNDLE_X"; then LEAK="the support bundle"; return 0; fi
  return 1
}

end_case() {
  local f
  if marker_leak; then FAILS+=("the secret marker from onprem.env appears in $LEAK"); fi
  if [[ ${#FAILS[@]} -eq 0 ]]; then
    NPASS=$((NPASS + 1))
    printf 'PASS %s\n' "$CASE"
  else
    NFAIL=$((NFAIL + 1))
    printf 'FAIL %s\n' "$CASE"
    for f in "${FAILS[@]}"; do printf '    - %s\n' "$f"; done
    printf '    last exit code: %s\n' "$RC"
    if [[ -f "$LAST_OUT" ]]; then
      tail -n 4 "$LAST_OUT" "$LAST_ERR" 2>/dev/null | sed "s/$MARKER/<MARKER>/g; s/^/      | /"
    fi
    if [[ -s "$STUB/events" ]]; then
      printf '    last stub events:\n'
      tail -n 6 "$STUB/events" | sed "s/$MARKER/<MARKER>/g; s/^/      > /"
    fi
  fi
}

rc_is() { [[ "$RC" == "$1" ]]; }
out_has() { grep -qE -- "$1" "$LAST_OUT" "$LAST_ERR"; }
out_hasi() { grep -qiE -- "$1" "$LAST_OUT" "$LAST_ERR"; }
out_hasF() { grep -qF -- "$1" "$LAST_OUT" "$LAST_ERR"; }
has_ev() { grep -qE -- "$1" "$STUB/events"; }
no_ev() { ! grep -qE -- "$1" "$STUB/events"; }
ev_line() { grep -nE -- "$1" "$STUB/events" | head -n 1 | cut -d: -f1; }
ev_before() {
  local a b
  a="$(ev_line "$1")"
  b="$(ev_line "$2")"
  [[ -n "$a" && -n "$b" && "$a" -lt "$b" ]]
}
ev_after() {
  local a
  a="$(ev_line "$1")"
  [[ -n "$a" ]] || return 1
  awk -v a="$a" 'NR > a' "$STUB/events" | grep -qE -- "$2"
}
current_is() { [[ -L "$HOMEF/current" && "$(readlink "$HOMEF/current")" == "$1" ]]; }
no_current() { [[ ! -e "$HOMEF/current" && ! -L "$HOMEF/current" ]]; }
absent() { [[ ! -e "$1" ]]; }
present() { [[ -e "$1" ]]; }
mode_of() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1" 2>/dev/null; }
mode_is() { [[ -e "$1" && "$(mode_of "$1")" == "$2" ]]; }
db_is() { [[ "$(cat "$STUB/db" 2>/dev/null)" == "$1" ]]; }
snap_field() { awk -F'|' -v s="$2" -v n="$3" '$1 == s { print $n; exit }' "$1" 2>/dev/null; }
svc_state() { snap_field "$STUB/svc" "$1" 2; }
svc_image() { snap_field "$STUB/svc" "$1" 5; }
running_on() { [[ "$(svc_state "$1")" == running && "$(svc_image "$1")" == "$2" ]]; }
not_running() { [[ "$(svc_state "$1")" != running ]]; }
writers_down_in() {
  local f="$1" s
  [[ -f "$f" ]] || return 1
  for s in proxy console-web api worker scheduler; do
    [[ "$(snap_field "$f" "$s" 2)" != running ]] || return 1
  done
  [[ "$(snap_field "$f" postgres 2)" == running ]]
}
snap_is() { [[ "$(snap_field "$1" "$2" 2)" == "$3" ]]; }
snap_not() { [[ -f "$1" && "$(snap_field "$1" "$2" 2)" != "$3" ]]; }
no_releases() {
  local d
  for d in "$HOMEF"/releases/*/; do [[ -d "$d" ]] && return 1; done
  return 0
}
dir_empty() { [[ -d "$1" && -z "$(ls -A "$1")" ]]; }
tar_count_is() {
  local n=0 f
  for f in "$1"/images/*.tar; do [[ -f "$f" ]] && n=$((n + 1)); done
  [[ "$n" == "$2" ]]
}
clear_events() { : >"$STUB/events"; }
started_from_final() {
  [[ -s "$STUB/started-from" ]] || return 1
  ! grep -q '/releases/\.' "$STUB/started-from" && grep -q "/releases/$1/infra/compose\$" "$STUB/started-from"
}
no_tmp_release() {
  local d
  for d in "$HOMEF"/releases/.*; do
    case "${d##*/}" in .|..) continue ;; esac
    [[ -e "$d" ]] && return 1
  done
  return 0
}
no_destructive() { [[ ! -s "$STUB/destructive" ]]; }
no_stop() { no_ev '^stop (proxy|console-web|api|worker|scheduler)$'; }
lock_free() { [[ ! -e "$HOMEF/ops/homayar-onprem.lock" ]]; }
log_file_600() {
  local f n=0
  for f in "$HOMEF"/ops/homayar-onprem-*.log; do
    [[ -f "$f" ]] || continue
    n=$((n + 1))
    [[ "$(mode_of "$f")" == 600 ]] || return 1
  done
  [[ "$n" -ge 1 ]]
}
loaded_all() { has_ev "^load homayar/api:$1\$" && has_ev "^load homayar/console-web:$1\$" && has_ev "^load homayar/egress-proxy:$1\$"; }
no_load() { no_ev '^load '; }

RID=""
STOPPED_AT=""
find_record() {
  local d n=0
  RID=""
  for d in "$HOMEF"/state/*/; do
    [[ -f "$d/record.env" ]] || continue
    n=$((n + 1))
    RID="$(basename "$d")"
  done
  [[ "$n" == 1 ]]
}
read_stopped_at() {
  STOPPED_AT="$(grep -iE '^(export[[:space:]]+)?stopped_at=' "$HOMEF/state/$RID/record.env" 2>/dev/null | tail -n 1 | sed -E 's/^[^=]*=//' | tr -d "\"'")"
  [[ -n "$STOPPED_AT" ]]
}
rid_format() { [[ "$RID" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; }
record_has() { grep -qF -- "$1" "$HOMEF/state/$RID/record.env"; }

begin "help: --help prints usage with every subcommand and exits 0"
fresh_world
run_s --help
ok "exit code is 0" rc_is 0
ok "usage names install, upgrade, rollback, support-bundle, status" \
  bash -c 'for w in install upgrade rollback support-bundle status preflight backup restore pack; do grep -qF -- "$w" "$1" "$2" || exit 1; done' _ "$LAST_OUT" "$LAST_ERR"
end_case

begin "help: an unknown command exits 2"
fresh_world
run_s frobnicate
ok "exit code is 2" rc_is 2
end_case

begin "verify: the untampered 0.5.0 package installs (allow-control for every package deny-case)"
fresh_world
run_s install --package "$PKG5"
ok "exit code is 0" rc_is 0
ok "current is the relative link releases/$V5" current_is "releases/$V5"
ok "releases/$V5/release.env exists" present "$HOMEF/releases/$V5/release.env"
ok "all three image archives were loaded" loaded_all "$V5"
ok "migrate ran and reached the 0.5.0 head" db_is "$H5"
ok "postgres ran during migrate" snap_is "$STUB/migrate-snapshot" postgres running
ok "the api was not started before migrate" snap_not "$STUB/migrate-snapshot" api running
ok "api runs homayar/api:$V5 at the end" running_on api "homayar/api:$V5"
ok "proxy runs at the end" snap_is "$STUB/svc" proxy running
ok "every service was started from releases/$V5 (bind mounts keep that path), not a temp folder" started_from_final "$V5"
ok "the signature line names the verifying key's fingerprint" out_hasF "fingerprint $FINGERPRINT"
ok "the signed requirements are printed" out_hasF "requires bash >=4.4"
ok "a log file ops/homayar-onprem-<ts>.log exists with mode 600" log_file_600
ok "the lock is released" lock_free
ok "no image or volume was removed" no_destructive
end_case

deny_install() {
  local name="$1" pkg="$2" reason="$3"
  begin "verify: $name is refused with exit 2 and nothing extracted"
  fresh_world
  run_s install --package "$pkg"
  ok "exit code is 2" rc_is 2
  ok "releases/$V5 was not created" absent "$HOMEF/releases/$V5"
  ok "current was not created" no_current
  ok "no image archive was loaded" no_load
  ok "nothing was migrated" no_ev '^migrate'
  if [[ -n "$reason" ]]; then ok "the reason names the offending item ($reason)" out_hasi "$reason"; fi
  end_case
}

deny_install "a package with a tampered file" "$D_TAMPER" 'compose\.onprem\.yaml'
deny_install "a package with an extra unlisted file" "$D_EXTRA" 'extra-unlisted\.conf'
deny_install "a package with a listed file missing" "$D_MISSING" 'oracle\.conf'
deny_install "a SHA256SUMS line with a .. path (its hash matches)" "$D_DOTDOT" ""
deny_install "a SHA256SUMS line with an absolute path (its hash matches)" "$D_ABS" ""
deny_install "a SHA256SUMS line that is not a checksum line" "$D_BADLINE" ""
deny_install "a release.env with an extra key" "$D_RE_EXTRA" 'release\.env|EXTRA_SETTING'
deny_install "a release.env with a :latest tag" "$D_RE_LATEST" 'release\.env|API_IMAGE|latest'
deny_install "a release.env with a :local tag" "$D_RE_LOCAL" 'release\.env|CONSOLE_WEB_IMAGE|local'
deny_install "a release.env image without a tag" "$D_RE_NOTAG" 'release\.env|EGRESS_PROXY_IMAGE'
deny_install "a release.env with a 39-hex SHA" "$D_RE_BADSHA" 'release\.env|HOMAYAR_RELEASE_SHA'
deny_install "a release.env with a missing key" "$D_RE_MISSING" 'release\.env|EGRESS_PROXY_IMAGE'

begin "verify: release.env is parsed as data, never sourced (a \$(...) value is refused and never runs)"
fresh_world
rm -f "$SOURCED_PROOF"
run_s install --package "$D_RE_SUBST"
ok "exit code is 2" rc_is 2
ok "the command substitution in release.env never ran" absent "$SOURCED_PROOF"
ok "releases/ has no extracted release" no_releases
end_case

begin "install: refused with exit 2 when current already exists (use upgrade)"
prepare_installed
run_s install --package "$PKG6"
ok "exit code is 2" rc_is 2
ok "the output says to use upgrade" out_hasi 'upgrade'
ok "no image archive was loaded" no_load
ok "releases/$V6 was not created" absent "$HOMEF/releases/$V6"
ok "current still points to releases/$V5" current_is "releases/$V5"
ok "no writer was stopped" no_stop
end_case

begin "install: a failed migrate exits 2 with the step name, removes no data, and a retry is not blocked"
fresh_world
: >"$STUB/fail-migrate"
run_s install --package "$PKG5"
ok "exit code is 2" rc_is 2
ok "the output names the migrate step" out_hasi 'migrat'
ok "current was not created" no_current
ok "config/onprem.env is still there" present "$HOMEF/config/onprem.env"
ok "no image or volume was removed" no_destructive
ok "no half-extracted temp folder is left under releases/" no_tmp_release
rm -f "$STUB/fail-migrate"
run_s install --package "$PKG5"
ok "a retry of the same install exits 0" rc_is 0
ok "after the retry current is releases/$V5" current_is "releases/$V5"
end_case

begin "status: prints the current version and alembic version, no secrets"
prepare_installed
run_s status
ok "exit code is 0" rc_is 0
ok "the output has the version $V5" out_hasF "$V5"
ok "the output has the alembic version $H5" out_hasF "$H5"
end_case

begin "preflight: runs compose config with the operator env file and the host preflight, exit 0 passed through"
prepare_installed
run_s preflight
ok "exit code is 0" rc_is 0
ok "the host preflight ran with --host" has_ev '^preflight .*model_preflight.* --host'
ok "compose config ran with config/onprem.env" has_ev '^config envfiles=.*config/onprem\.env'
end_case

begin "preflight: --offline is passed through to the host preflight"
prepare_installed
run_s preflight --offline
ok "exit code is 0" rc_is 0
ok "the host preflight got --offline" has_ev '^preflight .*model_preflight.* --offline'
end_case

begin "preflight: a preflight exit code 2 is passed through"
prepare_installed
printf '2\n' >"$STUB/preflight-rc"
run_s preflight
ok "exit code is 2" rc_is 2
end_case

begin "preflight: a preflight exit code 1 is passed through"
prepare_installed
printf '1\n' >"$STUB/preflight-rc"
run_s preflight
ok "exit code is 1" rc_is 1
end_case

begin "backup: execs current's infra/scripts/backup.sh with the args and the operator env file"
prepare_installed
run_s backup create --oracle-arg
ok "exit code is 0" rc_is 0
ok "backup.sh was called" present "$STUB/backup-called"
ok "backup.sh got exactly the args 'create --oracle-arg'" grep -qx 'args=create --oracle-arg' "$STUB/backup-called"
ok "the backup.sh that ran is the one of releases/$V5" grep -qE "^script=.*/releases/$V5/infra/scripts/backup\.sh$" "$STUB/backup-called"
ok "COMPOSE_ENV_FILE is config/onprem.env" grep -qE '^compose_env_file=.*/config/onprem\.env$' "$STUB/backup-called"
end_case

begin "backup: the exit code of backup.sh is passed through"
prepare_installed
printf '1\n' >"$STUB/backup-rc"
run_s backup create
ok "exit code is 1" rc_is 1
end_case

begin "restore: execs current's infra/scripts/restore-isolated.sh with the args"
prepare_installed
run_s restore --set oracle-set
ok "exit code is 0" rc_is 0
ok "restore-isolated.sh got exactly the args '--set oracle-set'" grep -qx 'args=--set oracle-set' "$STUB/restore-called"
ok "the restore-isolated.sh that ran is the one of releases/$V5" grep -qE "^script=.*/releases/$V5/infra/scripts/restore-isolated\.sh$" "$STUB/restore-called"
end_case

begin "upgrade: happy path (exit 0, current switched, record and dump written, writers stopped before dump, dump before migrate)"
prepare_installed
run_s upgrade --package "$PKG6"
ok "exit code is 0" rc_is 0
ok "current is the relative link releases/$V6" current_is "releases/$V6"
ok "the 0.5.0 release folder is kept" present "$HOMEF/releases/$V5/release.env"
ok "all three 0.6.0 image archives were loaded" loaded_all "$V6"
ok "exactly one state record exists" find_record
ok "the state id has the form YYYYMMDDTHHMMSSZ" rid_format
ok "record.env has mode 600" mode_is "$HOMEF/state/$RID/record.env" 600
ok "state/ has mode 700" mode_is "$HOMEF/state" 700
ok "record.env has the from-version $V5" record_has "$V5"
ok "record.env has the to-version $V6" record_has "$V6"
ok "record.env has the from-SHA" record_has "$SHA5"
ok "record.env has the to-SHA" record_has "$SHA6"
ok "record.env has the from api image ID" record_has "$IDA5"
ok "record.env has the alembic version before ($H5)" record_has "$H5"
ok "record.env has stopped_at" read_stopped_at
ok "db.dump exists with mode 600" mode_is "$HOMEF/state/$RID/db.dump" 600
ok "db.dump holds the pg_dump output" grep -q '^PGDMP' "$HOMEF/state/$RID/db.dump"
ok "proxy, console-web, api, worker, scheduler were stopped and postgres ran at pg_dump time" writers_down_in "$STUB/dump-snapshot"
ok "no pg_dump ran while a writer was running" no_ev '^pg_dump while writers run'
ok "pg_dump ran before migrate" ev_before '^pg_dump ok' '^migrate ok'
ok "the api was not running during migrate" snap_not "$STUB/migrate-snapshot" api running
ok "the database is at the 0.6.0 head" db_is "$H6"
ok "api runs homayar/api:$V6" running_on api "homayar/api:$V6"
ok "worker runs homayar/api:$V6" running_on worker "homayar/api:$V6"
ok "console-web runs homayar/console-web:$V6" running_on console-web "homayar/console-web:$V6"
ok "the output prints the state id" out_hasF "$RID"
ok "the output tells how to roll back" out_hasi 'rollback'
ok "the signature line names the verifying key's fingerprint" out_hasF "fingerprint $FINGERPRINT"
ok "no image or volume was removed or pruned" no_destructive
ok "the lock is released" lock_free
ok "a log file ops/homayar-onprem-<ts>.log exists with mode 600" log_file_600
end_case

begin "upgrade: a backup gauge 23 h old proceeds (allow-control for 25 h)"
prepare_installed
gauge $((23 * 3600))
run_s upgrade --package "$PKG6"
ok "exit code is 0" rc_is 0
ok "current is releases/$V6" current_is "releases/$V6"
end_case

begin "upgrade: a backup gauge 25 h old is refused with exit 2 before any change"
prepare_installed
gauge $((25 * 3600))
run_s upgrade --package "$PKG6"
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
ok "no pg_dump ran" no_ev '^pg_dump'
ok "releases/$V6 was not extracted" absent "$HOMEF/releases/$V6"
ok "no image archive was loaded" no_load
ok "current still points to releases/$V5" current_is "releases/$V5"
ok "the output tells the operator to run the backup command" out_hasi 'backup'
end_case

begin "upgrade: a missing backup gauge file is refused with exit 2"
prepare_installed
gauge missing
run_s upgrade --package "$PKG6"
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
ok "current still points to releases/$V5" current_is "releases/$V5"
end_case

begin "upgrade: --max-backup-age-hours 2 refuses a gauge 3 h old"
prepare_installed
gauge $((3 * 3600))
run_s upgrade --package "$PKG6" --max-backup-age-hours 2
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
end_case

begin "upgrade: --skip-backup-check with a reason proceeds on a stale gauge and records the reason"
prepare_installed
gauge $((25 * 3600))
run_s upgrade --package "$PKG6" --skip-backup-check oracle-skip-reason-77
ok "exit code is 0" rc_is 0
ok "current is releases/$V6" current_is "releases/$V6"
ok "exactly one state record exists" find_record
ok "record.env has the skip reason" record_has oracle-skip-reason-77
end_case

begin "upgrade: the same version with the same SHA exits 0 (already on it) and changes nothing"
prepare_installed
run_s upgrade --package "$PKG5"
ok "exit code is 0" rc_is 0
ok "the output says already" out_hasi 'already'
ok "no writer was stopped" no_stop
ok "current still points to releases/$V5" current_is "releases/$V5"
end_case

begin "upgrade: the same version with a different SHA is refused with exit 2"
prepare_installed
run_s upgrade --package "$PKG5X"
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
ok "current still points to releases/$V5" current_is "releases/$V5"
end_case

begin "upgrade: a tampered package is refused with exit 2 before any change"
prepare_installed
run_s upgrade --package "$D6_TAMPER"
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
ok "no image archive was loaded" no_load
ok "releases/$V6 was not extracted" absent "$HOMEF/releases/$V6"
ok "the reason names nginx.conf" out_hasi 'nginx\.conf'
end_case

begin "upgrade: an unhealthy stack (/readyz not 200) is refused with exit 2"
prepare_installed
: >"$STUB/readyz-down"
run_s upgrade --package "$PKG6"
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
ok "current still points to releases/$V5" current_is "releases/$V5"
end_case

begin "upgrade: less free disk than MIN_DISK_GIB is refused with exit 2"
prepare_installed
run_s --help
DISK_FLAG=()
if out_hasF "--min-disk-gib"; then DISK_FLAG=(--min-disk-gib 100000000); fi
RUN_MIN_DISK=100000000
run_s upgrade --package "$PKG6" "${DISK_FLAG[@]}"
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
end_case

begin "upgrade: a held lock is refused with exit 2 and the lock stays"
prepare_installed
mkdir -p "$HOMEF/ops/homayar-onprem.lock"
run_s upgrade --package "$PKG6"
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
ok "the other run's lock is still there" present "$HOMEF/ops/homayar-onprem.lock"
ok "current still points to releases/$V5" current_is "releases/$V5"
end_case

begin "upgrade: an unknown flag exits 2 before any change"
prepare_installed
run_s upgrade --package "$PKG6" --no-such-flag
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
end_case

begin "upgrade: pg_dump fails -> writers start again on the old release, exit 2, current unchanged"
prepare_installed
: >"$STUB/fail-dump"
run_s upgrade --package "$PKG6"
ok "exit code is 2" rc_is 2
ok "current still points to releases/$V5" current_is "releases/$V5"
ok "migrate never ran" no_ev '^migrate'
ok "the api was started again after the failed dump on homayar/api:$V5" ev_after '^pg_dump fail' "^start api homayar/api:$V5\$"
ok "api runs homayar/api:$V5" running_on api "homayar/api:$V5"
ok "worker runs homayar/api:$V5" running_on worker "homayar/api:$V5"
ok "proxy runs" snap_is "$STUB/svc" proxy running
ok "the database is unchanged" db_is "$H5"
end_case

begin "upgrade: migrate fails -> automatic rollback restores the dump, current back, exit 1"
prepare_installed
: >"$STUB/fail-migrate"
run_s upgrade --package "$PKG6"
ok "exit code is 1" rc_is 1
ok "current points back to releases/$V5" current_is "releases/$V5"
ok "the dump was restored into the live database after the failed migrate" ev_after '^migrate fail' '^pg_restore live ok'
ok "the database is back at $H5" db_is "$H5"
ok "api runs homayar/api:$V5" running_on api "homayar/api:$V5"
ok "worker runs homayar/api:$V5" running_on worker "homayar/api:$V5"
ok "console-web runs homayar/console-web:$V5" running_on console-web "homayar/console-web:$V5"
ok "no image or volume was removed" no_destructive
ok "the lock is released" lock_free
end_case

begin "upgrade: the rollback itself fails (live restore fails) -> exit 3 with manual steps, writers stay stopped"
prepare_installed
: >"$STUB/fail-migrate"
: >"$STUB/fail-live-restore"
run_s upgrade --package "$PKG6"
ok "exit code is 3" rc_is 3
ok "the output gives manual steps" out_hasi 'manual'
ok "api is not running" not_running api
ok "worker is not running" not_running worker
ok "scheduler is not running" not_running scheduler
end_case

setup_upgraded() {
  prepare_installed
  run_s upgrade --package "$PKG6"
  SETUP_OK=1
  rc_is 0 || SETUP_OK=0
  find_record || SETUP_OK=0
  read_stopped_at || SETUP_OK=0
  : >"$STUB/events"
}
setup_ok() { [[ "$SETUP_OK" == 1 ]]; }

begin "rollback: --accept-data-loss-since one second after stopped_at is refused with exit 2"
setup_upgraded
ok "setup: the upgrade finished and wrote stopped_at" setup_ok
TS_PLUS="$(python3 -I "$PY/shiftts.py" "$STOPPED_AT" 1 2>/dev/null || :)"
TS_MINUS="$(python3 -I "$PY/shiftts.py" "$STOPPED_AT" -1 2>/dev/null || :)"
ok "stopped_at is a UTC timestamp the oracle can shift" test -n "$TS_PLUS"
run_s rollback --to "$RID" --accept-data-loss-since "$TS_PLUS"
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
ok "current still points to releases/$V6" current_is "releases/$V6"
ok "the database is still at $H6" db_is "$H6"
end_case

begin "rollback: --accept-data-loss-since one second before stopped_at is refused with exit 2"
ok "setup: the upgrade finished and wrote stopped_at" setup_ok
clear_events
run_s rollback --to "$RID" --accept-data-loss-since "$TS_MINUS"
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
ok "current still points to releases/$V6" current_is "releases/$V6"
end_case

begin "rollback: an unknown state id is refused with exit 2"
ok "setup: the upgrade finished and wrote stopped_at" setup_ok
clear_events
run_s rollback --to 20000101T000000Z --accept-data-loss-since "$STOPPED_AT"
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
end_case

begin "rollback: without --accept-data-loss-since is refused with exit 2"
ok "setup: the upgrade finished and wrote stopped_at" setup_ok
clear_events
run_s rollback --to "$RID"
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
ok "current still points to releases/$V6" current_is "releases/$V6"
end_case

begin "rollback: the exact stopped_at rolls back (exit 0, dump restored, current back, old images run)"
ok "setup: the upgrade finished and wrote stopped_at" setup_ok
clear_events
run_s rollback --to "$RID" --accept-data-loss-since "$STOPPED_AT"
ok "exit code is 0" rc_is 0
ok "current points back to releases/$V5" current_is "releases/$V5"
ok "the dump was restored into the live database" has_ev '^pg_restore live ok'
ok "the database is back at $H5" db_is "$H5"
ok "writers were stopped before the restore" ev_before '^stop api$' '^pg_restore live ok'
ok "api runs homayar/api:$V5" running_on api "homayar/api:$V5"
ok "worker runs homayar/api:$V5" running_on worker "homayar/api:$V5"
ok "console-web runs homayar/console-web:$V5" running_on console-web "homayar/console-web:$V5"
ok "the lock is released" lock_free
ok "no image or volume was removed" no_destructive
end_case

begin "rollback: refused with exit 2 when the dump is missing"
setup_upgraded
ok "setup: the upgrade finished and wrote stopped_at" setup_ok
rm -f "$HOMEF/state/$RID/db.dump"
run_s rollback --to "$RID" --accept-data-loss-since "$STOPPED_AT"
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
ok "current still points to releases/$V6" current_is "releases/$V6"
end_case

begin "rollback: refused with exit 2 when the from-release folder is missing"
setup_upgraded
ok "setup: the upgrade finished and wrote stopped_at" setup_ok
rm -rf -- "${HOMEF:?}/releases/$V5"
run_s rollback --to "$RID" --accept-data-loss-since "$STOPPED_AT"
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
ok "current still points to releases/$V6" current_is "releases/$V6"
end_case

begin "rollback: refused with exit 2 when a from-image ID no longer exists"
setup_upgraded
ok "setup: the upgrade finished and wrote stopped_at" setup_ok
awk -F'|' -v r="homayar/api:$V5" '$1 != r' "$STUB/images" >"$STUB/images.tmp" && mv -f "$STUB/images.tmp" "$STUB/images"
run_s rollback --to "$RID" --accept-data-loss-since "$STOPPED_AT"
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
ok "current still points to releases/$V6" current_is "releases/$V6"
end_case

begin "rollback: refused with exit 2 when current is not the record's to-version"
setup_upgraded
ok "setup: the upgrade finished and wrote stopped_at" setup_ok
ln -sfn "releases/$V5" "$HOMEF/current"
run_s rollback --to "$RID" --accept-data-loss-since "$STOPPED_AT"
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
end_case

begin "rollback: a held lock is refused with exit 2"
setup_upgraded
ok "setup: the upgrade finished and wrote stopped_at" setup_ok
mkdir -p "$HOMEF/ops/homayar-onprem.lock"
run_s rollback --to "$RID" --accept-data-loss-since "$STOPPED_AT"
ok "exit code is 2" rc_is 2
ok "no writer was stopped" no_stop
ok "the other run's lock is still there" present "$HOMEF/ops/homayar-onprem.lock"
ok "current still points to releases/$V6" current_is "releases/$V6"
end_case

check_bundle() {
  rm -rf -- "${T:?}/bx"
  mkdir -p "$T/bx"
  python3 -I "$PY/extract.py" "$1" "$T/bx" >"$T/bx.list" || return 1
  BUNDLE_X="$T/bx"
}
member() { grep -qE "(^|/)$1\$" "$T/bx.list"; }
bundle_has() { grep -rqaF -- "$1" "$T/bx"; }
bundle_lacks() { ! grep -rqaF -- "$1" "$T/bx"; }
tmp_empty() { [[ -z "$(ls -A "$RUNTMP" 2>/dev/null)" ]]; }
json_ok() {
  local f
  f="$(grep -E "(^|/)$1\$" "$T/bx.list" | head -n 1)"
  [[ -n "$f" ]] && python3 -I -c 'import json, sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$T/bx/$f"
}

begin "support-bundle: a clean scan writes a mode-600 tar.gz with the allowlisted files and no secret, log text, hostname or GPU process"
prepare_installed
BOUT="$T/out/bundle.tar.gz"
rm -rf -- "${T:?}/out"
mkdir -p "$T/out"
printf '%s\n' "$BOUT" >"$STUB/bundle-out"
run_s support-bundle --output "$BOUT"
ok "exit code is 0" rc_is 0
ok "the output file exists" present "$BOUT"
ok "the output file has mode 600" mode_is "$BOUT" 600
if [[ -f "$BOUT" ]] && check_bundle "$BOUT"; then
  ok "the bundle has MANIFEST.json" member MANIFEST.json
  ok "the bundle has container.json" member container.json
  ok "the bundle has preflight.json" member preflight.json
  ok "the bundle has logs-summary.json" member logs-summary.json
  ok "logs-summary.json is valid JSON" json_ok logs-summary.json
  ok "MANIFEST.json lists every file with its sha256, a created-at and format version 1" python3 -I "$PY/mancheck.py" "$T/bx"
  ok "container.json is the collect output" bundle_has oracle-collect-ok
  ok "the GPU line is in the bundle (nvidia-smi present)" bundle_has "Tesla P40"
  ok "the docker version is in the bundle" bundle_has 27.3.1
  ok "no log line text is in the bundle" bundle_lacks oracle-log-line-text-canary
  ok "no e-mail from a log line is in the bundle" bundle_lacks oracle-person@example.org
  ok "no IP from a log line is in the bundle" bundle_lacks 10.77.0.9
  ok "no hostname is in the bundle" bundle_lacks oracle-host-canary
  ok "no host IP address is in the bundle" bundle_lacks 10.77.0.5
  ok "no GPU process is in the bundle" bundle_lacks oracle-gpu-process-canary
else
  FAILS+=("the output is not a readable tar.gz with safe member names")
fi
ok "the scan ran" has_ev '^scan 0$'
ok "the scan got --env-file" grep -qE -- '--env-file' "$STUB/scan-args"
ok "the output file did not exist when the scan ran" absent "$STUB/out-existed-at-scan"
ok "the temp folder was removed" tmp_empty
end_case

begin "support-bundle: a scan finding (scan exit 1) fails closed: exit 2, no output file, temp removed"
prepare_installed
BOUT="$T/out/bundle-finding.tar.gz"
rm -rf -- "${T:?}/out"
mkdir -p "$T/out"
printf '%s\n' "$BOUT" >"$STUB/bundle-out"
printf '1\n' >"$STUB/scan-rc"
run_s support-bundle --output "$BOUT"
ok "exit code is 2" rc_is 2
ok "no output file exists" absent "$BOUT"
ok "the out folder is empty" dir_empty "$T/out"
ok "the output names the finding kind" out_hasF email
ok "the temp folder was removed" tmp_empty
end_case

begin "support-bundle: a scan error (scan exit 2) fails closed: exit 2, no output file"
prepare_installed
BOUT="$T/out/bundle-error.tar.gz"
rm -rf -- "${T:?}/out"
mkdir -p "$T/out"
printf '%s\n' "$BOUT" >"$STUB/bundle-out"
printf '2\n' >"$STUB/scan-rc"
run_s support-bundle --output "$BOUT"
ok "exit code is 2" rc_is 2
ok "no output file exists" absent "$BOUT"
ok "the temp folder was removed" tmp_empty
end_case

make_src() {
  rm -rf -- "${SRC:?}"
  mkdir -p "$SRC/Homayar/backend" "$SRC/infra/offline"
  write_infra "$SRC" "$V7"
  printf 'print("app")\n' >"$SRC/Homayar/backend/app.py"
  printf '%s\n' "$SHA7" >"$SRC/.oracle-git-head"
  {
    printf '# oracle bundle.conf fixture\n'
    printf 'license api LicenseRef-Homayar-Proprietary\n'
    printf 'license console-web LicenseRef-Homayar-Proprietary\n'
    printf 'license egress-proxy LicenseRef-Homayar-Proprietary\n'
    printf 'requires bash >=4.4\n'
    printf 'requires openssl >=3.0\n'
  } >"$SRC/infra/offline/bundle.conf"
}
pack_world() {
  fresh_world
  add_img "homayar/api:$V7" "$IDA7" "$H7" "$SHA7" "$V7"
  add_img "homayar/console-web:$V7" "$IDC7" "$H7" "$SHA7" "$V7"
  add_img "homayar/egress-proxy:$V7" "$IDE7" "$H7" "$SHA7" "$V7"
  make_src
}
pack_args() {
  printf '%s\n' --source "$SRC" --output "$1" --version "$V7" \
    --api-image "homayar/api:$V7" --console-web-image "homayar/console-web:$V7" --egress-proxy-image "homayar/egress-proxy:$V7" \
    --signing-key "$KEY"
}
relenv_val() { grep -E "^$2=" "$1/release.env" | tail -n 1 | sed -E 's/^[^=]*=//' | tr -d "\"'"; }
relenv_eq() { [[ -f "$1/release.env" && "$(relenv_val "$1" "$2")" == "$3" ]]; }
relenv_keys_exact() {
  local got
  got="$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$1/release.env" | sed 's/=.*//' | LC_ALL=C sort | tr '\n' ' ')"
  [[ "$got" == "API_IMAGE CONSOLE_WEB_IMAGE EGRESS_PROXY_IMAGE HOMAYAR_RELEASE_SHA HOMAYAR_RELEASE_VERSION " ]]
}
sums_cover_all() {
  local d="$1" listed present_files
  listed="$(sed -E 's/^[0-9a-f]{64} [ *]//' "$d/SHA256SUMS" | LC_ALL=C sort)"
  present_files="$(cd "$d" && find . -type f ! -path ./SHA256SUMS ! -path ./SHA256SUMS.sig | sed 's|^\./||' | LC_ALL=C sort)"
  [[ -n "$listed" && "$listed" == "$present_files" ]]
}
sums_verify() { (cd "$1" && sha256sum -c --strict --quiet SHA256SUMS >/dev/null 2>&1); }
sums_clean_paths() { ! grep -qE '(^[0-9a-f]{64} [ *]/)|(\.\.)|( [ *]SHA256SUMS$)' "$1/SHA256SUMS"; }

PK1="$T/pk1"
begin "pack: builds a package whose SHA256SUMS covers every file and whose release.env has exactly the five keys"
pack_world
rm -rf -- "${PK1:?}"
mkdir -p "$PK1"
mapfile -t PARGS < <(pack_args "$PK1")
run_s pack "${PARGS[@]}"
ok "exit code is 0" rc_is 0
ok "SHA256SUMS exists" present "$PK1/SHA256SUMS"
ok "every SHA256SUMS line verifies" sums_verify "$PK1"
ok "SHA256SUMS lists every file of the package and nothing else" sums_cover_all "$PK1"
ok "SHA256SUMS has no absolute path, no .. and no line for itself" sums_clean_paths "$PK1"
ok "release.env has exactly the five allowed keys" relenv_keys_exact "$PK1"
ok "HOMAYAR_RELEASE_VERSION is $V7" relenv_eq "$PK1" HOMAYAR_RELEASE_VERSION "$V7"
ok "HOMAYAR_RELEASE_SHA is the checkout HEAD" relenv_eq "$PK1" HOMAYAR_RELEASE_SHA "$SHA7"
ok "API_IMAGE is homayar/api:$V7" relenv_eq "$PK1" API_IMAGE "homayar/api:$V7"
ok "three image archives under images/" tar_count_is "$PK1" 3
ok "SHA256SUMS.sig exists and is exactly 64 bytes" bash -c '[[ -f "$1/SHA256SUMS.sig" && "$(stat -c %s "$1/SHA256SUMS.sig")" == 64 ]]' _ "$PK1"
ok "SHA256SUMS.sig verifies with the signing key's public key" bash -c 'openssl pkeyutl -verify -pubin -inkey "$2" -rawin -in "$1/SHA256SUMS" -sigfile "$1/SHA256SUMS.sig" >/dev/null 2>&1' _ "$PK1" "$PUB"
ok "BUNDLE.manifest exists, is listed in SHA256SUMS and starts with the schema line" bash -c 'test -f "$1/BUNDLE.manifest" && grep -q "  BUNDLE.manifest" "$1/SHA256SUMS" && head -n 1 "$1/BUNDLE.manifest" | grep -qx "schema homayar-offline-bundle/1"' _ "$PK1"
ok "the pack output names the signing key fingerprint" out_hasF "fingerprint $FINGERPRINT"
ok "infra/compose/compose.onprem.yaml is copied from the checkout" cmp -s "$SRC/infra/compose/compose.onprem.yaml" "$PK1/infra/compose/compose.onprem.yaml"
ok "infra/nginx is copied from the checkout" cmp -s "$SRC/infra/nginx/nginx.conf" "$PK1/infra/nginx/nginx.conf"
end_case

begin "pack: the packed output installs on a fresh home (round trip)"
fresh_world
run_s install --package "$PK1"
ok "exit code is 0" rc_is 0
ok "current is the relative link releases/$V7" current_is "releases/$V7"
ok "all three 0.7.0 image archives were loaded" loaded_all "$V7"
ok "the database is at the 0.7.0 head" db_is "$H7"
end_case

begin "pack: a non-empty output folder is refused with exit 2 and left alone"
pack_world
PK3="$T/pk3"
rm -rf -- "${PK3:?}"
mkdir -p "$PK3"
printf 'keep me\n' >"$PK3/keep.txt"
mapfile -t PARGS < <(pack_args "$PK3")
run_s pack "${PARGS[@]}"
ok "exit code is 2" rc_is 2
ok "the existing file is unchanged" grep -qx 'keep me' "$PK3/keep.txt"
ok "no SHA256SUMS was written" absent "$PK3/SHA256SUMS"
end_case

begin "pack: a dirty checkout is refused with exit 2"
pack_world
: >"$SRC/.oracle-git-dirty"
PK4="$T/pk4"
rm -rf -- "${PK4:?}"
mkdir -p "$PK4"
mapfile -t PARGS < <(pack_args "$PK4")
run_s pack "${PARGS[@]}"
ok "exit code is 2" rc_is 2
ok "no SHA256SUMS was written" absent "$PK4/SHA256SUMS"
end_case

begin "pack: a missing --egress-proxy-image is refused with exit 2"
pack_world
PK5="$T/pk5"
rm -rf -- "${PK5:?}"
mkdir -p "$PK5"
run_s pack --source "$SRC" --output "$PK5" --version "$V7" --api-image "homayar/api:$V7" --console-web-image "homayar/console-web:$V7"
ok "exit code is 2" rc_is 2
ok "no SHA256SUMS was written" absent "$PK5/SHA256SUMS"
end_case

printf '%s passed, %s failed\n' "$NPASS" "$NFAIL"
[[ "$NFAIL" == 0 ]]
