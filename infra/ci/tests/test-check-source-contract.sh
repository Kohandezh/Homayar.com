#!/usr/bin/env bash
# Behavioural tests for infra/ci/check-source-contract.sh using synthetic Git
# repositories. Runs with plain bash and git; no network, no real product clone.
set -Eeuo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
checker="${here}/../check-source-contract.sh"
tmp="$(mktemp -d "${TMPDIR:-/tmp}/source-contract-test.XXXXXX")"
trap 'rm -rf "${tmp}"' EXIT

export HOME="${tmp}/home"
mkdir -p "${HOME}"
export GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.invalid
export GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.invalid

passed=0
failed=0

run_case() {
  # run_case NAME EXPECTED_EXIT REQUIRED_PATTERN -- checker args...
  local name="$1" expected="$2" pattern="$3"
  shift 3
  [[ "$1" == "--" ]] && shift
  local out rc=0
  out="$(bash "${checker}" "$@" 2>&1)" || rc=$?
  if [[ "${rc}" -ne "${expected}" ]]; then
    printf 'FAIL %s: expected exit %s, got %s\n%s\n' "${name}" "${expected}" "${rc}" "${out}"
    failed=$((failed + 1))
    return
  fi
  if [[ -n "${pattern}" ]] && ! grep -q -- "${pattern}" <<<"${out}"; then
    printf 'FAIL %s: output does not contain "%s"\n%s\n' "${name}" "${pattern}" "${out}"
    failed=$((failed + 1))
    return
  fi
  passed=$((passed + 1))
  printf 'ok   %s\n' "${name}"
}

mkrepo() {
  local dir="$1" remote="${2:-https://github.com/Example/Product.git}"
  mkdir -p "${dir}"
  git -C "${dir}" init -q
  git -C "${dir}" checkout -q -b main 2>/dev/null || true
  git -C "${dir}" remote add origin "${remote}"
}

commit_all() {
  git -C "$1" add -A
  git -C "$1" commit -q -m "$2"
  git -C "$1" rev-parse HEAD
}

write_good_tree() {
  mkdir -p "$1/backend" "$1/frontend/src"
  printf '[project]\nname = "product"\n' >"$1/pyproject.toml"
  printf 'print("api")\n' >"$1/backend/main.py"
  printf 'export const app = 1\n' >"$1/frontend/src/app.ts"
  printf '.env\ndata.db\n' >"$1/.gitignore"
}

write_lock() {
  cat >"$1" <<LOCK
# test lock
repository=https://github.com/Example/Product.git
ref=main
sha=$2
required_path=pyproject.toml
required_path=backend/main.py
required_path=frontend/src
forbidden_path=.env
forbidden_path=data.db
LOCK
}

# --- Reference repository that satisfies the contract ----------------------
good="${tmp}/good"
mkrepo "${good}"
write_good_tree "${good}"
good_sha="$(commit_all "${good}" "initial")"
good_lock="${tmp}/good.lock"
write_lock "${good_lock}" "${good_sha}"

# --- Configuration errors (exit 2) -----------------------------------------
run_case "missing lock file" 2 "lock file not found" -- --lock "${tmp}/nope.lock" --product-dir "${good}"

printf 'repository=https://github.com/Example/Product.git\nref=main\nsha=notahash\nrequired_path=a\n' >"${tmp}/bad-sha.lock"
run_case "malformed sha" 2 "40-character" -- --lock "${tmp}/bad-sha.lock" --product-dir "${good}"

printf 'repository=https://github.com/Example/Product.git\nref=main\nsha=%s\nrequired_path=../escape\n' "${good_sha}" >"${tmp}/bad-path.lock"
run_case "path traversal in lock" 2 "invalid path" -- --lock "${tmp}/bad-path.lock" --product-dir "${good}"

printf 'repository=https://github.com/Example/Product.git\nref=main\nsha=%s\nsurprise=1\n' "${good_sha}" >"${tmp}/bad-key.lock"
run_case "unknown lock key" 2 "unknown key" -- --lock "${tmp}/bad-key.lock" --product-dir "${good}"

run_case "unknown mode" 2 '--mode must be' -- --mode staging --lock "${good_lock}" --product-dir "${good}"

run_case "product dir missing" 2 "product source directory missing" -- --lock "${good_lock}" --product-dir "${tmp}/absent"

mkdir -p "${tmp}/plain"
run_case "product dir is not git" 2 "not a Git checkout" -- --lock "${good_lock}" --product-dir "${tmp}/plain"

outer="${tmp}/outer"
mkrepo "${outer}" "https://github.com/Example/Website.git"
mkdir -p "${outer}/Homayar"
printf 'x\n' >"${outer}/Homayar/file"
run_case "product dir nested in another checkout" 2 "inside another Git checkout" -- --lock "${good_lock}" --product-dir "${outer}/Homayar"

# --- Contract violations (exit 3) ------------------------------------------
write_lock "${tmp}/unreachable.lock" "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
run_case "pinned commit not present" 3 "is not present" -- --lock "${tmp}/unreachable.lock" --product-dir "${good}"

ahead="${tmp}/ahead"
cp -R "${good}" "${ahead}"
printf 'more\n' >"${ahead}/backend/extra.py"
commit_all "${ahead}" "second" >/dev/null
run_case "HEAD ahead of pinned commit" 3 "does not match pinned commit" -- --lock "${good_lock}" --product-dir "${ahead}"

overlay="${tmp}/overlay"
mkrepo "${overlay}"
write_good_tree "${overlay}"
rm "${overlay}/pyproject.toml"
overlay_sha="$(commit_all "${overlay}" "without pyproject")"
printf '[project]\n' >"${overlay}/pyproject.toml"
write_lock "${tmp}/overlay.lock" "${overlay_sha}"
run_case "required path only in uncommitted overlay" 3 "uncommitted overlay" -- --lock "${tmp}/overlay.lock" --product-dir "${overlay}"

absent="${tmp}/absent-path"
mkrepo "${absent}"
write_good_tree "${absent}"
rm -r "${absent}/frontend"
absent_sha="$(commit_all "${absent}" "without frontend")"
write_lock "${tmp}/absent.lock" "${absent_sha}"
run_case "required path missing everywhere" 3 "missing from" -- --lock "${tmp}/absent.lock" --product-dir "${absent}"

tracked="${tmp}/tracked-secret"
mkrepo "${tracked}"
write_good_tree "${tracked}"
rm "${tracked}/.gitignore"
printf 'SECRET_PLACEHOLDER=1\n' >"${tracked}/.env"
tracked_sha="$(commit_all "${tracked}" "tracks env")"
write_lock "${tmp}/tracked.lock" "${tracked_sha}"
run_case "forbidden path tracked" 3 'forbidden path ".env" is tracked' -- --lock "${tmp}/tracked.lock" --product-dir "${tracked}"

dirty="${tmp}/dirty"
cp -R "${good}" "${dirty}"
printf 'scratch\n' >"${dirty}/notes.txt"
run_case "ci mode rejects dirty tree" 3 "ci mode requires a clean checkout" -- --mode ci --lock "${good_lock}" --product-dir "${dirty}"

wrong_remote="${tmp}/wrong-remote"
cp -R "${good}" "${wrong_remote}"
git -C "${wrong_remote}" remote set-url origin https://github.com/Other/Fork.git
run_case "ci mode rejects remote mismatch" 3 "origin remote does not match" -- --mode ci --lock "${good_lock}" --product-dir "${wrong_remote}"

# --- Satisfied with warnings (exit 0) --------------------------------------
run_case "local mode warns on dirty tree" 0 "WARNING: product checkout has 1 modified or untracked" -- --mode local --lock "${good_lock}" --product-dir "${dirty}"
run_case "local mode warns on remote mismatch" 0 "WARNING: origin remote does not match" -- --mode local --lock "${good_lock}" --product-dir "${wrong_remote}"

unignored="${tmp}/unignored"
cp -R "${good}" "${unignored}"
git -C "${unignored}" rm -q --cached .gitignore
rm "${unignored}/.gitignore"
commit_all "${unignored}" "drop gitignore" >/dev/null
unignored_sha="$(git -C "${unignored}" rev-parse HEAD)"
printf 'db\n' >"${unignored}/data.db"
write_lock "${tmp}/unignored.lock" "${unignored_sha}"
run_case "local mode warns on unignored forbidden path" 0 "no repository ignore rule matches" -- --mode local --lock "${tmp}/unignored.lock" --product-dir "${unignored}"

printf 'repository=git@github.com:Example/Product.git\nref=main\nsha=%s\nrequired_path=pyproject.toml\n' "${good_sha}" >"${tmp}/ssh-remote.lock"
run_case "non-https repository in lock" 2 "must be an https://github.com" -- --lock "${tmp}/ssh-remote.lock" --product-dir "${good}"

printf '  repository = https://github.com/Example/Product.git \r\n\t\nref=main\nsha = %s\nrequired_path=  pyproject.toml\n' "${good_sha}" >"${tmp}/spaced.lock"
run_case "whitespace and CRLF tolerated in lock" 0 "RESULT: satisfied" -- --mode ci --lock "${tmp}/spaced.lock" --product-dir "${good}"

# --- Satisfied (exit 0) -----------------------------------------------------
run_case "ci mode satisfied" 0 "RESULT: satisfied" -- --mode ci --lock "${good_lock}" --product-dir "${good}"
run_case "local mode satisfied" 0 "RESULT: satisfied" -- --mode local --lock "${good_lock}" --product-dir "${good}"
run_case "remote url normalisation (.git suffix, case)" 0 "RESULT: satisfied" -- --mode ci --lock "${good_lock}" --product-dir "${good}"

printf '\n%d passed, %d failed\n' "${passed}" "${failed}"
[[ "${failed}" -eq 0 ]]
