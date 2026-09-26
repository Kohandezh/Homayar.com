#!/usr/bin/env bash
# Verify that the product source tracked in this repository satisfies
# infra/product-source.lock (ADR 0033: the product lives under Homayar/ as
# ordinary files, never as a gitlink or submodule).
#
# Exit codes:
#   0  contract satisfied
#   2  usage or configuration error (missing lock, malformed lock, repository
#      is not a Git checkout, revision does not resolve)
#   3  contract violated (product directory untracked or a gitlink/submodule,
#      required path not committed at the revision, forbidden path tracked,
#      dirty product directory or nested checkout in ci mode)
#
# Modes:
#   local (default)  a dirty product directory and a nested pre-import clone
#                    (Homayar/.git) are warnings
#   ci               the product directory must be clean and contain no nested
#                    Git checkout
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "${script_dir}/../.." && pwd -P)"
mode="local"
lock_file="${repo_root}/infra/product-source.lock"
rev="HEAD"

usage() {
  cat <<'USAGE'
Usage: check-source-contract.sh [--mode local|ci] [--lock FILE] [--repo DIR] [--rev REV]

Checks that the product directory declared in FILE is tracked in the Git
repository DIR (default: this repository) as ordinary files at REV (default:
HEAD), that every required_path is committed there and that no forbidden_path
is tracked. See the script header for exit codes.
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
    --repo)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      repo_root="$2"; shift 2 ;;
    --rev)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      rev="$2"; shift 2 ;;
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

product_dir=""
imported_from=""
imported_ref=""
imported_sha=""
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
    product_dir) product_dir="${value%/}" ;;
    imported_from) imported_from="${value}" ;;
    imported_ref) imported_ref="${value}" ;;
    imported_sha) imported_sha="${value}" ;;
    required_path) required_paths+=("${value%/}") ;;
    forbidden_path) forbidden_paths+=("${value%/}") ;;
    *) config_error "${lock_file}:${line_number}: unknown key \"${key}\"" ;;
  esac
done <"${lock_file}"

[[ -n "${product_dir}" ]] || config_error "lock file has no product_dir= entry"
[[ ${#required_paths[@]} -gt 0 ]] || config_error "lock file declares no required_path entries"
for path in "${product_dir}" "${required_paths[@]}" ${forbidden_paths[@]+"${forbidden_paths[@]}"}; do
  case "${path}" in
    ""|/*|*/../*|../*|*/..|..|.|./*) config_error "invalid path in lock file: \"${path}\"" ;;
  esac
done
if [[ -n "${imported_from}" && "${imported_from}" != https://github.com/*/* ]]; then
  config_error "lock file imported_from= must be an https://github.com/<owner>/<repo> URL"
fi
if [[ -n "${imported_sha}" && ! "${imported_sha}" =~ ^[0-9a-f]{40}$ ]]; then
  config_error "lock file imported_sha= must be a 40-character lowercase hex commit id"
fi

# ---------------------------------------------------------------------------
# Locate the repository and the revision.
# ---------------------------------------------------------------------------
[[ -d "${repo_root}" ]] || config_error "repository directory missing: ${repo_root}"
repo_root="$(cd -- "${repo_root}" && pwd -P)"
git -C "${repo_root}" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || config_error "${repo_root} is not a Git checkout"
toplevel="$(git -C "${repo_root}" rev-parse --show-toplevel)"
toplevel="$(cd -- "${toplevel}" && pwd -P)"
[[ "${toplevel}" == "${repo_root}" ]] \
  || config_error "${repo_root} is not the top level of its Git checkout (${toplevel})"
rev_sha="$(git -C "${repo_root}" rev-parse --verify --quiet "${rev}^{commit}" 2>/dev/null)" \
  || config_error "revision \"${rev}\" does not resolve to a commit in ${repo_root}"

# ---------------------------------------------------------------------------
# The product directory must be an ordinary tracked tree.
# ---------------------------------------------------------------------------
entry="$(git -C "${repo_root}" ls-tree "${rev_sha}" -- "${product_dir}" | head -n 1)"
entry_mode="${entry%% *}"
if [[ -z "${entry}" ]]; then
  violation "product directory \"${product_dir}\" is not tracked at ${rev_sha}; the product source must be committed in this repository (ADR 0033)"
elif [[ "${entry_mode}" == "160000" ]]; then
  violation "product directory \"${product_dir}\" is a gitlink at ${rev_sha}; it must be committed as ordinary files, never as a submodule"
elif [[ "${entry_mode}" != "040000" ]]; then
  violation "product directory \"${product_dir}\" is not a directory at ${rev_sha} (mode ${entry_mode})"
fi
if git -C "${repo_root}" cat-file -e "${rev_sha}:.gitmodules" 2>/dev/null \
  && git -C "${repo_root}" config --blob "${rev_sha}:.gitmodules" --get-regexp '^submodule\..*\.path$' 2>/dev/null \
    | awk '{print $2}' | grep -qxF -- "${product_dir}"; then
  violation ".gitmodules at ${rev_sha} declares a submodule at \"${product_dir}\"; remove it"
fi

for path in "${required_paths[@]}"; do
  if git -C "${repo_root}" cat-file -e "${rev_sha}:${product_dir}/${path}" 2>/dev/null; then
    continue
  fi
  if [[ -e "${repo_root}/${product_dir}/${path}" ]]; then
    violation "required path \"${product_dir}/${path}\" exists on disk but is not committed at ${rev_sha}; commit it before relying on a clean checkout"
  else
    violation "required path \"${product_dir}/${path}\" is missing from ${rev_sha} and from the working tree"
  fi
done

for path in ${forbidden_paths[@]+"${forbidden_paths[@]}"}; do
  if git -C "${repo_root}" cat-file -e "${rev_sha}:${product_dir}/${path}" 2>/dev/null; then
    violation "forbidden path \"${product_dir}/${path}\" is tracked at ${rev_sha}; runtime data, environments and caches must stay out of the repository"
  elif [[ -e "${repo_root}/${product_dir}/${path}" ]] \
    && ! git -C "${repo_root}" check-ignore -q -- "${product_dir}/${path}" 2>/dev/null; then
    warn "forbidden path \"${product_dir}/${path}\" exists in the working tree and no ignore rule matches it; add an explicit rule to ${product_dir}/.gitignore"
  fi
done

# A pre-import clone left in place (Homayar/.git) is harmless to this
# repository, which never descends into it, but Git commands run inside the
# directory then address that clone instead of this repository.
if [[ -e "${repo_root}/${product_dir}/.git" ]]; then
  if [[ "${mode}" == "ci" ]]; then
    violation "\"${product_dir}/.git\" exists; ci mode requires the product source from this repository's checkout only"
  else
    warn "\"${product_dir}/.git\" exists (a separate clone in the product directory); Git commands run inside ${product_dir}/ address that clone, not this repository"
  fi
fi

dirty_count="$(git -C "${repo_root}" status --porcelain --untracked-files=normal -- "${product_dir}" | grep -c '' || true)"
if [[ "${dirty_count}" -gt 0 ]]; then
  if [[ "${mode}" == "ci" ]]; then
    violation "product directory has ${dirty_count} modified or untracked entries; ci mode requires a clean checkout"
  else
    warn "product directory has ${dirty_count} modified or untracked entries (local mode); the commit alone must satisfy every required path"
  fi
fi

# Provenance of the import (informational, never a violation).
provenance="not recorded"
if [[ -n "${imported_sha}" ]]; then
  if import_tree="$(git -C "${repo_root}" rev-parse --verify --quiet "${imported_sha}^{tree}" 2>/dev/null)"; then
    product_tree="$(git -C "${repo_root}" rev-parse --verify --quiet "${rev_sha}:${product_dir}" 2>/dev/null || true)"
    if [[ "${product_tree}" == "${import_tree}" ]]; then
      provenance="identical to the imported commit ${imported_sha}"
    else
      provenance="changed since the imported commit ${imported_sha}"
    fi
  else
    provenance="imported from ${imported_from:-unknown} ${imported_ref:+(${imported_ref}) }at ${imported_sha} (commit not available locally)"
  fi
fi

info "product_dir=${product_dir} rev=${rev_sha} dirty_entries=${dirty_count} mode=${mode}"
info "provenance: ${provenance}"
if [[ ${#violations[@]} -gt 0 ]]; then
  printf '[contract] RESULT: violated (%d finding(s))\n' "${#violations[@]}" >&2
  exit 3
fi
info "RESULT: satisfied"
