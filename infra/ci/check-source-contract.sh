#!/usr/bin/env bash
# Verify that the product source checkout satisfies infra/product-source.lock.
#
# Exit codes:
#   0  contract satisfied
#   2  usage or configuration error (missing lock or product directory,
#      malformed lock, directory is not an independent Git checkout)
#   3  contract violated (pinned commit unavailable or not checked out,
#      required path not committed at the pinned commit, forbidden path
#      tracked, remote mismatch in ci mode, dirty tree in ci mode)
#
# Modes:
#   local (default)  dirty working tree and remote mismatch are warnings
#   ci               working tree must be clean and origin must match the lock
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "${script_dir}/../.." && pwd -P)"
mode="local"
lock_file="${repo_root}/infra/product-source.lock"
product_dir="${repo_root}/Homayar"

usage() {
  cat <<'USAGE'
Usage: check-source-contract.sh [--mode local|ci] [--lock FILE] [--product-dir DIR]

Checks that DIR is an independent Git checkout of the repository and commit
pinned in FILE, that every required_path is committed at that commit and that
no forbidden_path is tracked there. See the script header for exit codes.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      mode="$2"; shift 2 ;;
    --lock)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      lock_file="$2"; shift 2 ;;
    --product-dir)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      product_dir="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      printf 'ERROR: unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2 ;;
  esac
done

case "${mode}" in
  local|ci) ;;
  *) printf 'ERROR: --mode must be "local" or "ci", got "%s".\n' "${mode}" >&2; exit 2 ;;
esac

info() { printf '[contract] %s\n' "$*"; }
warn() { printf '[contract] WARNING: %s\n' "$*" >&2; }
config_error() { printf '[contract] ERROR: %s\n' "$*" >&2; exit 2; }

violations=()
violation() { violations+=("$*"); printf '[contract] VIOLATION: %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# Parse the lock file.
# ---------------------------------------------------------------------------
[[ -f "${lock_file}" ]] || config_error "lock file not found: ${lock_file}"

repository=""
ref=""
sha=""
required_paths=()
forbidden_paths=()
trim() {
  local text="$1"
  text="${text#"${text%%[![:space:]]*}"}"
  text="${text%"${text##*[![:space:]]}"}"
  printf '%s' "${text}"
}

line_number=0
while IFS= read -r raw_line || [[ -n "${raw_line}" ]]; do
  line_number=$((line_number + 1))
  line="$(trim "${raw_line%$'\r'}")"
  [[ -z "${line}" || "${line}" == \#* ]] && continue
  [[ "${line}" == *=* ]] || config_error "${lock_file}:${line_number}: expected key=value, got: ${line}"
  key="$(trim "${line%%=*}")"
  value="$(trim "${line#*=}")"
  case "${key}" in
    repository) repository="${value}" ;;
    ref) ref="${value}" ;;
    sha) sha="${value}" ;;
    required_path) required_paths+=("${value%/}") ;;
    forbidden_path) forbidden_paths+=("${value%/}") ;;
    *) config_error "${lock_file}:${line_number}: unknown key \"${key}\"" ;;
  esac
done <"${lock_file}"

[[ -n "${repository}" ]] || config_error "lock file has no repository= entry"
[[ "${repository}" == https://github.com/*/* ]] || config_error "lock file repository= must be an https://github.com/<owner>/<repo> URL"
[[ -n "${ref}" ]] || config_error "lock file has no ref= entry"
[[ "${sha}" =~ ^[0-9a-f]{40}$ ]] || config_error "lock file sha= must be a 40-character lowercase hex commit id"
[[ ${#required_paths[@]} -gt 0 ]] || config_error "lock file declares no required_path entries"
for path in "${required_paths[@]}" ${forbidden_paths[@]+"${forbidden_paths[@]}"}; do
  case "${path}" in
    ""|/*|*/../*|../*|*/..|..) config_error "invalid path in lock file: \"${path}\"" ;;
  esac
done

# ---------------------------------------------------------------------------
# Locate the product checkout.
# ---------------------------------------------------------------------------
[[ -d "${product_dir}" ]] || config_error "product source directory missing: ${product_dir}. Clone ${repository} at ${sha} into it (CI uses .github/actions/product-source)."
product_dir="$(cd -- "${product_dir}" && pwd -P)"

git -C "${product_dir}" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || config_error "${product_dir} is not a Git checkout"
toplevel="$(git -C "${product_dir}" rev-parse --show-toplevel)"
toplevel="$(cd -- "${toplevel}" && pwd -P)"
[[ "${toplevel}" == "${product_dir}" ]] \
  || config_error "${product_dir} is inside another Git checkout (${toplevel}); the product source must be its own checkout"

normalize_remote() {
  # Portable lowercase (bash 3.2 on macOS has no ${var,,}).
  local url
  url="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  url="${url%/}"
  url="${url%.git}"
  url="${url#git@github.com:}"
  url="${url#https://github.com/}"
  url="${url#http://github.com/}"
  url="${url#ssh://git@github.com/}"
  printf '%s' "${url}"
}

origin_url="$(git -C "${product_dir}" remote get-url origin 2>/dev/null || true)"
if [[ -z "${origin_url}" ]]; then
  if [[ "${mode}" == "ci" ]]; then
    violation "product checkout has no origin remote; expected ${repository}"
  else
    warn "product checkout has no origin remote; expected ${repository}"
  fi
elif [[ "$(normalize_remote "${origin_url}")" != "$(normalize_remote "${repository}")" ]]; then
  if [[ "${mode}" == "ci" ]]; then
    violation "origin remote does not match the lock: expected ${repository}"
  else
    warn "origin remote does not match the lock: expected ${repository}"
  fi
fi

# ---------------------------------------------------------------------------
# Pinned commit checks.
# ---------------------------------------------------------------------------
if ! git -C "${product_dir}" cat-file -e "${sha}^{commit}" 2>/dev/null; then
  violation "pinned commit ${sha} is not present in ${product_dir}; fetch ${repository} (ref ${ref})"
  head_sha="$(git -C "${product_dir}" rev-parse HEAD 2>/dev/null || printf 'unknown')"
  info "repository=${repository} ref=${ref} pinned=${sha} head=${head_sha} mode=${mode}"
  printf '[contract] RESULT: violated (%d finding(s))\n' "${#violations[@]}" >&2
  exit 3
fi

head_sha="$(git -C "${product_dir}" rev-parse HEAD)"
if [[ "${head_sha}" != "${sha}" ]]; then
  violation "HEAD ${head_sha} does not match pinned commit ${sha}; check out the pinned commit or update the lock through a reviewed change"
fi

# Ref consistency: the pinned commit must be reachable from the declared ref so
# that "ref=" cannot silently describe a branch that never contained "sha=".
ref_sha=""
for candidate in "refs/remotes/origin/${ref}" "refs/heads/${ref}" "refs/tags/${ref}"; do
  if ref_sha="$(git -C "${product_dir}" rev-parse --verify --quiet "${candidate}^{commit}" 2>/dev/null)"; then
    break
  fi
  ref_sha=""
done
if [[ -n "${ref_sha}" ]]; then
  if ! git -C "${product_dir}" merge-base --is-ancestor "${sha}" "${ref_sha}" 2>/dev/null; then
    violation "pinned commit ${sha} is not reachable from ref \"${ref}\" (${ref_sha}); update ref= and sha= together"
  fi
elif [[ "${mode}" == "ci" ]]; then
  violation "ref \"${ref}\" is not resolvable in ${product_dir} (expected refs/remotes/origin/${ref}); fetch the published ref before verifying so the pin can be checked against it"
else
  warn "ref \"${ref}\" is not resolvable locally; pin/ref consistency not verified (a locally committed pin is not available to remote CI until the ref is published)"
fi

for path in "${required_paths[@]}"; do
  if git -C "${product_dir}" cat-file -e "${sha}:${path}" 2>/dev/null; then
    continue
  fi
  if [[ -e "${product_dir}/${path}" ]]; then
    violation "required path \"${path}\" exists on disk but is not committed at ${sha} (uncommitted overlay); commit and pin it before relying on a clean checkout"
  else
    violation "required path \"${path}\" is missing from ${sha} and from the working tree"
  fi
done

for path in ${forbidden_paths[@]+"${forbidden_paths[@]}"}; do
  if git -C "${product_dir}" cat-file -e "${sha}:${path}" 2>/dev/null; then
    violation "forbidden path \"${path}\" is tracked at ${sha}; runtime data, environments and caches must stay out of the product source"
  elif [[ -e "${product_dir}/${path}" ]] && ! git -C "${product_dir}" check-ignore -q -- "${path}" 2>/dev/null; then
    warn "forbidden path \"${path}\" exists in the working tree and no repository ignore rule matches it (tool-generated nested .gitignore files do not count); add an explicit rule to the product .gitignore"
  fi
done

dirty_count="$(git -C "${product_dir}" status --porcelain --untracked-files=normal | grep -c '' || true)"
if [[ "${dirty_count}" -gt 0 ]]; then
  if [[ "${mode}" == "ci" ]]; then
    violation "product checkout has ${dirty_count} modified or untracked entries; ci mode requires a clean checkout"
  else
    warn "product checkout has ${dirty_count} modified or untracked entries (local mode); the pinned commit alone must satisfy every required path"
  fi
fi

info "repository=${repository} ref=${ref} pinned=${sha} head=${head_sha} dirty_entries=${dirty_count} mode=${mode}"
if [[ ${#violations[@]} -gt 0 ]]; then
  printf '[contract] RESULT: violated (%d finding(s))\n' "${#violations[@]}" >&2
  exit 3
fi
info "RESULT: satisfied"
