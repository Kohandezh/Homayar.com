#!/usr/bin/env bash
# Behavioural tests for infra/ci/check-source-contract.sh using synthetic Git
# repositories. Runs with plain bash and git; no network, no real product tree.
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
  mkdir -p "$1"
  git -C "$1" init -q
  git -C "$1" checkout -q -b main 2>/dev/null || true
}

commit_all() {
  git -C "$1" add -A
  git -C "$1" commit -q -m "$2"
  git -C "$1" rev-parse HEAD
}

write_product_tree() {
  mkdir -p "$1/backend" "$1/frontend/src"
  printf '[project]\nname = "product"\n' >"$1/pyproject.toml"
  printf 'print("api")\n' >"$1/backend/main.py"
  printf 'export const app = 1\n' >"$1/frontend/src/app.ts"
  printf '.env\ndata.db\n' >"$1/.gitignore"
}

write_lock() {
  # write_lock FILE [IMPORTED_SHA]
  {
    printf '# test lock\nproduct_dir=Homayar\n'
    if [[ -n "${2:-}" ]]; then
      printf 'imported_from=https://github.com/Example/Product.git\nimported_ref=main\nimported_sha=%s\n' "$2"
    fi
    printf 'required_path=pyproject.toml\nrequired_path=backend/main.py\nrequired_path=frontend/src\n'
    printf 'forbidden_path=.env\nforbidden_path=data.db\n'
  } >"$1"
}

# --- Reference repository that satisfies the contract ----------------------
good="${tmp}/good"
mkrepo "${good}"
printf 'site\n' >"${good}/README.md"
write_product_tree "${good}/Homayar"
commit_all "${good}" "initial" >/dev/null
good_lock="${tmp}/good.lock"
write_lock "${good_lock}"

# --- Configuration errors (exit 2) -----------------------------------------
run_case "missing lock file" 2 "lock file not found" -- --lock "${tmp}/nope.lock" --repo "${good}"

printf 'product_dir=Homayar\nimported_sha=notahash\nrequired_path=a\n' >"${tmp}/bad-sha.lock"
run_case "malformed imported_sha" 2 "40-character" -- --lock "${tmp}/bad-sha.lock" --repo "${good}"

printf 'product_dir=Homayar\nrequired_path=../escape\n' >"${tmp}/bad-path.lock"
run_case "path traversal in lock" 2 "invalid path" -- --lock "${tmp}/bad-path.lock" --repo "${good}"

printf 'product_dir=/abs\nrequired_path=a\n' >"${tmp}/abs-dir.lock"
run_case "absolute product_dir" 2 "invalid path" -- --lock "${tmp}/abs-dir.lock" --repo "${good}"

printf 'required_path=a\n' >"${tmp}/no-dir.lock"
run_case "missing product_dir" 2 "no product_dir" -- --lock "${tmp}/no-dir.lock" --repo "${good}"

printf 'product_dir=Homayar\nrequired_path=a\nsurprise=1\n' >"${tmp}/bad-key.lock"
run_case "unknown lock key" 2 "unknown key" -- --lock "${tmp}/bad-key.lock" --repo "${good}"

printf 'product_dir=Homayar\nimported_from=git@github.com:Example/Product.git\nrequired_path=a\n' >"${tmp}/ssh.lock"
run_case "non-https imported_from" 2 "must be an https://github.com" -- --lock "${tmp}/ssh.lock" --repo "${good}"

run_case "unknown mode" 2 '--mode must be' -- --mode staging --lock "${good_lock}" --repo "${good}"

mkdir -p "${tmp}/plain"
run_case "repository is not git" 2 "not a Git checkout" -- --lock "${good_lock}" --repo "${tmp}/plain"

run_case "repository is not the top level" 2 "not the top level" -- --lock "${good_lock}" --repo "${good}/Homayar"

run_case "revision does not resolve" 2 "does not resolve" -- --lock "${good_lock}" --repo "${good}" --rev no-such-branch

# --- Contract violations (exit 3) ------------------------------------------
untracked="${tmp}/untracked"
mkrepo "${untracked}"
printf 'site\n' >"${untracked}/README.md"
printf '/Homayar/\n' >"${untracked}/.gitignore"
commit_all "${untracked}" "site only" >/dev/null
write_product_tree "${untracked}/Homayar"
run_case "product directory not tracked" 3 "is not tracked" -- --lock "${good_lock}" --repo "${untracked}"

gitlink="${tmp}/gitlink"
mkrepo "${gitlink}"
printf 'site\n' >"${gitlink}/README.md"
commit_all "${gitlink}" "site" >/dev/null
git -C "${gitlink}" update-index --add --cacheinfo "160000,$(git -C "${good}" rev-parse HEAD),Homayar"
git -C "${gitlink}" commit -q -m "gitlink"
run_case "product directory is a gitlink" 3 "is a gitlink" -- --lock "${good_lock}" --repo "${gitlink}"

submodule="${tmp}/submodule"
cp -R "${good}" "${submodule}"
printf '[submodule "Homayar"]\n\tpath = Homayar\n\turl = https://github.com/Example/Product.git\n' >"${submodule}/.gitmodules"
commit_all "${submodule}" "declare submodule" >/dev/null
run_case ".gitmodules declares the product directory" 3 "declares a submodule" -- --lock "${good_lock}" --repo "${submodule}"

overlay="${tmp}/overlay"
cp -R "${good}" "${overlay}"
git -C "${overlay}" rm -q Homayar/pyproject.toml
git -C "${overlay}" commit -q -m "without pyproject"
printf '[project]\n' >"${overlay}/Homayar/pyproject.toml"
run_case "required path only on disk" 3 "exists on disk but is not committed" -- --lock "${good_lock}" --repo "${overlay}"

absent="${tmp}/absent-path"
cp -R "${good}" "${absent}"
git -C "${absent}" rm -q -r Homayar/frontend
git -C "${absent}" commit -q -m "without frontend"
run_case "required path missing everywhere" 3 "is missing from" -- --lock "${good_lock}" --repo "${absent}"

tracked="${tmp}/tracked-secret"
cp -R "${good}" "${tracked}"
printf 'SECRET_PLACEHOLDER=1\n' >"${tracked}/Homayar/.env"
git -C "${tracked}" add -f Homayar/.env
git -C "${tracked}" commit -q -m "tracks env"
run_case "forbidden path tracked" 3 'forbidden path "Homayar/.env" is tracked' -- --lock "${good_lock}" --repo "${tracked}"

dirty="${tmp}/dirty"
cp -R "${good}" "${dirty}"
printf 'scratch\n' >"${dirty}/Homayar/notes.txt"
run_case "ci mode rejects a dirty product directory" 3 "ci mode requires a clean checkout" -- --mode ci --lock "${good_lock}" --repo "${dirty}"

nested="${tmp}/nested"
cp -R "${good}" "${nested}"
git -C "${nested}/Homayar" init -q
run_case "ci mode rejects a nested checkout" 3 'Homayar/.git" exists; ci mode' -- --mode ci --lock "${good_lock}" --repo "${nested}"

old_rev="$(git -C "${absent}" rev-parse HEAD~1)"
run_case "--rev checks an older commit" 0 "RESULT: satisfied" -- --mode ci --lock "${good_lock}" --repo "${absent}" --rev "${old_rev}"

# --- Satisfied with warnings (exit 0) --------------------------------------
run_case "local mode warns on a dirty product directory" 0 "WARNING: product directory has 1 modified or untracked" -- --mode local --lock "${good_lock}" --repo "${dirty}"
run_case "local mode warns on a nested checkout" 0 "WARNING: \"Homayar/.git\" exists" -- --mode local --lock "${good_lock}" --repo "${nested}"

unignored="${tmp}/unignored"
cp -R "${good}" "${unignored}"
git -C "${unignored}" rm -q Homayar/.gitignore
git -C "${unignored}" commit -q -m "drop product gitignore"
printf 'db\n' >"${unignored}/Homayar/data.db"
run_case "local mode warns on an unignored forbidden path" 0 "no ignore rule matches" -- --mode local --lock "${good_lock}" --repo "${unignored}"

printf '  product_dir = Homayar/ \r\n\t\nrequired_path=  pyproject.toml\n' >"${tmp}/spaced.lock"
run_case "whitespace and CRLF tolerated in lock" 0 "RESULT: satisfied" -- --mode ci --lock "${tmp}/spaced.lock" --repo "${good}"

# --- Provenance of the import (informational) --------------------------------
product="${tmp}/product"
mkrepo "${product}"
write_product_tree "${product}"
product_sha="$(commit_all "${product}" "product")"
imported="${tmp}/imported"
mkrepo "${imported}"
printf 'site\n' >"${imported}/README.md"
commit_all "${imported}" "site" >/dev/null
git -C "${imported}" fetch -q "${product}" main
git -C "${imported}" read-tree --prefix=Homayar/ "${product_sha}^{tree}"
git -C "${imported}" checkout -q -- Homayar
git -C "${imported}" commit -q -m "import product"
write_lock "${tmp}/imported.lock" "${product_sha}"
run_case "provenance: identical to the import" 0 "identical to the imported commit" -- --mode ci --lock "${tmp}/imported.lock" --repo "${imported}"

printf 'print("changed")\n' >"${imported}/Homayar/backend/main.py"
commit_all "${imported}" "change after import" >/dev/null
run_case "provenance: changed since the import" 0 "changed since the imported commit" -- --mode ci --lock "${tmp}/imported.lock" --repo "${imported}"

write_lock "${tmp}/foreign.lock" "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
run_case "provenance: import commit not available" 0 "commit not available locally" -- --mode ci --lock "${tmp}/foreign.lock" --repo "${good}"

# --- Satisfied (exit 0) -----------------------------------------------------
run_case "ci mode satisfied" 0 "RESULT: satisfied" -- --mode ci --lock "${good_lock}" --repo "${good}"
run_case "local mode satisfied" 0 "RESULT: satisfied" -- --mode local --lock "${good_lock}" --repo "${good}"

printf '\n%d passed, %d failed\n' "${passed}" "${failed}"
[[ "${failed}" -eq 0 ]]
