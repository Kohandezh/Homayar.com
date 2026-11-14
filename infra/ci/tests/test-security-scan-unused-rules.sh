#!/usr/bin/env bash
# Behavioural tests for `infra/ci/security-scan.sh unused-rules` (HOM-359) with
# small fake Grype JSON reports. Runs with plain bash and jq; no Docker, no network.
set -Eeuo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
scanner="${here}/../security-scan.sh"
tmp="$(mktemp -d "${TMPDIR:-/tmp}/unused-rules-test.XXXXXX")"
trap 'rm -rf "${tmp}"' EXIT

passed=0
failed=0

run_case() {
  # run_case NAME EXPECTED_EXIT REQUIRED_PATTERN -- scanner args...
  local name="$1" expected="$2" pattern="$3"
  shift 3
  [[ "$1" == "--" ]] && shift
  local out rc=0
  out="$(bash "${scanner}" "$@" 2>&1)" || rc=$?
  if [[ "${rc}" -ne "${expected}" ]]; then
    printf 'FAIL %s: expected exit %s, got %s\n%s\n' "${name}" "${expected}" "${rc}" "${out}"
    failed=$((failed + 1))
    return
  fi
  if [[ -n "${pattern}" ]] && ! grep -qx -- "${pattern}" <<<"${out}"; then
    printf 'FAIL %s: no output line is exactly "%s"\n%s\n' "${name}" "${pattern}" "${out}"
    failed=$((failed + 1))
    return
  fi
  passed=$((passed + 1))
  printf 'ok   %s\n' "${name}"
}

# expect_no_line NAME LINE DIR: the scanner output must not contain LINE.
expect_no_line() {
  local out
  out="$(bash "${scanner}" unused-rules "$3" 2>&1)" || true
  if grep -qx -- "$2" <<<"${out}"; then
    printf 'FAIL %s: output has the line "%s"\n%s\n' "$1" "$2" "${out}"
    failed=$((failed + 1))
    return
  fi
  passed=$((passed + 1))
  printf 'ok   %s\n' "$1"
}

# report FILE RULES APPLIED: write a fake Grype report.
# RULES and APPLIED are space-separated lists of <vulnerability>:<package> pairs.
# RULES always gets the Grype default rules (empty vulnerability) as well.
report() {
  mkdir -p "$(dirname -- "$1")"
  jq -n --arg rules "$2" --arg applied "$3" '
    def pairs($s): $s | split(" ") | map(select(. != "") | split(":") | {id: .[0], pkg: .[1]});
    def rule: {vulnerability: .id, "include-aliases": false, reason: "", namespace: "",
      "fix-state": "", package: {name: .pkg, version: "", language: "", type: "",
      location: "", "upstream-name": ""}, "vex-status": "", "vex-justification": "",
      "match-type": ""};
    {
      matches: [],
      descriptor: {name: "grype", configuration: {ignore: (
        (pairs($rules) | map(rule)) + [
          {vulnerability: "", package: {name: "kernel-headers", type: "rpm"}},
          {vulnerability: "", package: {name: "linux(-.*)?-headers-.*", type: "deb"}},
          {vulnerability: "", package: {name: "linux-libc-dev", type: "deb"}},
          {vulnerability: "", package: {name: "linux-kbuild-.*", type: "deb"}}
        ])}}
    }
    + (if $applied == "" then {} else {ignoredMatches: (pairs($applied) | map({
        vulnerability: {id: .id},
        artifact: {name: .pkg},
        appliedIgnoreRules: [{vulnerability: .id, namespace: "", package: {name: .pkg, language: ""}}]
      }))} end)
  ' >"$1"
}

rules="GHSA-aaaa-aaaa-aaaa:alpha CVE-2026-0001:beta"

# --- All rules used -> 0 -----------------------------------------------------
d="${tmp}/all-used"
report "${d}/deps/reports/one.grype.json" "${rules}" "GHSA-aaaa-aaaa-aaaa:alpha CVE-2026-0001:beta"
run_case "all rules used -> 0" 0 "" -- unused-rules "${d}"

# --- One rule unused -> 6 and the exact UNUSED line ---------------------------
d="${tmp}/one-unused"
report "${d}/deps/reports/one.grype.json" "${rules}" "GHSA-aaaa-aaaa-aaaa:alpha"
run_case "one rule unused -> 6" 6 "UNUSED CVE-2026-0001 beta" -- unused-rules "${d}"
expect_no_line "the used rule is not listed" "UNUSED GHSA-aaaa-aaaa-aaaa alpha" "${d}"

# --- A rule used only in a second report (union across reports) -> 0 ----------
d="${tmp}/union"
report "${d}/deps/reports/one.grype.json" "${rules}" "GHSA-aaaa-aaaa-aaaa:alpha"
report "${d}/images/reports/two.grype.json" "${rules}" "CVE-2026-0001:beta"
run_case "rule used in a second report only -> 0" 0 "" -- unused-rules "${d}"

# --- A report without ignoredMatches still counts; the union decides ----------
d="${tmp}/no-ignored-matches"
report "${d}/a/one.grype.json" "${rules}" ""
report "${d}/b/two.grype.json" "${rules}" "GHSA-aaaa-aaaa-aaaa:alpha CVE-2026-0001:beta"
run_case "report without ignoredMatches -> 0" 0 "" -- unused-rules "${d}"

# --- Grype default rules (empty vulnerability) never count as unused ----------
d="${tmp}/defaults"
report "${d}/one.grype.json" "${rules}" "GHSA-aaaa-aaaa-aaaa:alpha CVE-2026-0001:beta"
run_case "Grype default rules are not own rules -> 0" 0 "" -- unused-rules "${d}"
d="${tmp}/defaults-unused"
report "${d}/one.grype.json" "${rules}" "GHSA-aaaa-aaaa-aaaa:alpha"
expect_no_line "Grype default rule is never listed" "UNUSED  kernel-headers" "${d}"

# --- A rule applied only in canary.grype.json is unused -> 6 ------------------
d="${tmp}/canary"
report "${d}/deps/reports/one.grype.json" "${rules}" "GHSA-aaaa-aaaa-aaaa:alpha"
report "${d}/canary/reports/canary.grype.json" "${rules}" "CVE-2026-0001:beta"
run_case "rule applied only in canary.grype.json -> 6" 6 "UNUSED CVE-2026-0001 beta" -- unused-rules "${d}"

# --- Same ID, other package in the applied entry -> 6 -------------------------
d="${tmp}/other-package"
report "${d}/one.grype.json" "${rules}" "GHSA-aaaa-aaaa-aaaa:alpha CVE-2026-0001:gamma"
run_case "same ID but other package -> 6" 6 "UNUSED CVE-2026-0001 beta" -- unused-rules "${d}"

# --- Unused lines are sorted --------------------------------------------------
d="${tmp}/sorted"
report "${d}/one.grype.json" "GHSA-zzzz-zzzz-zzzz:zeta CVE-2026-0001:beta GHSA-aaaa-aaaa-aaaa:alpha" ""
out="$(bash "${scanner}" unused-rules "${d}" 2>&1)" || true
got="$(grep '^UNUSED ' <<<"${out}" || true)"
want="$(printf 'UNUSED CVE-2026-0001 beta\nUNUSED GHSA-aaaa-aaaa-aaaa alpha\nUNUSED GHSA-zzzz-zzzz-zzzz zeta')"
if [[ "${got}" == "${want}" ]]; then
  passed=$((passed + 1))
  printf 'ok   UNUSED lines are sorted\n'
else
  printf 'FAIL UNUSED lines are sorted: got\n%s\n' "${got}"
  failed=$((failed + 1))
fi

# --- No reports -> 4 ----------------------------------------------------------
d="${tmp}/empty"
mkdir -p "${d}"
run_case "empty reports dir -> 4" 4 "" -- unused-rules "${d}"

# --- Only a canary report -> 4 ------------------------------------------------
d="${tmp}/only-canary"
report "${d}/canary.grype.json" "${rules}" "GHSA-aaaa-aaaa-aaaa:alpha CVE-2026-0001:beta"
run_case "only canary.grype.json -> 4" 4 "" -- unused-rules "${d}"

# --- Invalid JSON -> 4 --------------------------------------------------------
d="${tmp}/invalid"
report "${d}/one.grype.json" "${rules}" "GHSA-aaaa-aaaa-aaaa:alpha CVE-2026-0001:beta"
printf '{"matches": [' >"${d}/two.grype.json"
run_case "invalid JSON -> 4" 4 "" -- unused-rules "${d}"

# --- Missing ignore list or matches array -> 4 ---------------------------------
d="${tmp}/no-ignore"
mkdir -p "${d}"
printf '{"matches": [], "descriptor": {"configuration": {}}}\n' >"${d}/one.grype.json"
run_case "no descriptor.configuration.ignore -> 4" 4 "" -- unused-rules "${d}"
d="${tmp}/no-matches"
mkdir -p "${d}"
printf '{"descriptor": {"configuration": {"ignore": []}}}\n' >"${d}/one.grype.json"
run_case "no matches array -> 4" 4 "" -- unused-rules "${d}"

# --- Reports with different own-rule sets -> 4 --------------------------------
d="${tmp}/different-sets"
report "${d}/one.grype.json" "${rules}" "GHSA-aaaa-aaaa-aaaa:alpha CVE-2026-0001:beta"
report "${d}/two.grype.json" "GHSA-aaaa-aaaa-aaaa:alpha" "GHSA-aaaa-aaaa-aaaa:alpha"
run_case "different own-rule sets -> 4" 4 "" -- unused-rules "${d}"

# --- Same own-rule set in a different order -> 0 -------------------------------
d="${tmp}/same-set-other-order"
report "${d}/one.grype.json" "${rules}" "GHSA-aaaa-aaaa-aaaa:alpha"
report "${d}/two.grype.json" "CVE-2026-0001:beta GHSA-aaaa-aaaa-aaaa:alpha" "CVE-2026-0001:beta"
run_case "same own-rule set in another order -> 0" 0 "" -- unused-rules "${d}"

# --- Bad arguments -> 2 -------------------------------------------------------
run_case "no directory argument -> 2" 2 "" -- unused-rules
run_case "two arguments -> 2" 2 "" -- unused-rules "${tmp}/all-used" extra
run_case "not a directory -> 2" 2 "" -- unused-rules "${tmp}/does-not-exist"
run_case "a file, not a directory -> 2" 2 "" -- unused-rules "${tmp}/all-used/deps/reports/one.grype.json"

printf '\n%s passed, %s failed\n' "${passed}" "${failed}"
[[ "${failed}" -eq 0 ]]
