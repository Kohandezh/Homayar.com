#!/usr/bin/env bash
# Container-boundary egress test for the on-prem Compose topology
# (WP08-T06 network layer, ADR 0031).
#
# Starts the real egress-proxy service of infra/compose/compose.onprem.yaml in
# a throw-away project, two disposable HTTP servers on its outbound network
# (one allowlisted, one not), and runs probes from inside the api, worker,
# scheduler, public-web and console-web service containers as Compose
# attaches them. It proves, from the process boundary:
#   - no application container reaches anything directly (names, IPs,
#     public addresses, cloud metadata);
#   - through the proxy only the exact allowlisted host:port is forwarded,
#     for CONNECT and plain HTTP, and the application's HTTP client uses it;
#   - the proxy refuses unlisted hosts/ports, metadata and its manager page,
#     and publishes no host port.
# Needs Docker and the images named by API_IMAGE, PUBLIC_WEB_IMAGE,
# CONSOLE_WEB_IMAGE (built beforehand). Contacts no real provider.
set -Eeuo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
compose_file="${repo}/infra/compose/compose.onprem.yaml"
project="${EGRESS_TEST_PROJECT:-homayar-egress-test}"
work="$(mktemp -d)"
allowlist="${work}/allowlist.txt"
printf '%s\n' "# test allowlist" "egress-test-allowed:8080" > "${allowlist}"

export PUBLIC_ORIGIN=http://localhost:8080 CORS_ORIGINS=http://localhost:8080 TRUSTED_HOSTS=localhost
export DATABASE_URL=postgresql+psycopg://t:t@postgres:5432/t REDIS_URL=redis://:t@redis:6379/0
export JWT_SECRET=egress-test-only-jwt-secret-000000000000 TOKEN_PEPPER=egress-test-only-token-pepper-00000000000
export STORAGE_ACCESS_KEY=t STORAGE_SECRET_KEY=t POSTGRES_DB=t POSTGRES_USER=t POSTGRES_PASSWORD=t
export REDIS_PASSWORD=t MINIO_ROOT_USER=t MINIO_ROOT_PASSWORD=t
export EGRESS_PROXY_URL=http://egress-proxy:3128 EGRESS_ALLOWLIST_PATH="${allowlist}"
: "${API_IMAGE:?set API_IMAGE to a built API image}"
: "${PUBLIC_WEB_IMAGE:?set PUBLIC_WEB_IMAGE to a built public-web image}"
: "${CONSOLE_WEB_IMAGE:?set CONSOLE_WEB_IMAGE to a built console-web image}"
export API_IMAGE PUBLIC_WEB_IMAGE CONSOLE_WEB_IMAGE

compose=(docker compose --project-name "${project}" --file "${compose_file}" --profile onprem --profile egress)
failures=0

cleanup() {
  docker rm --force egress-test-allowed egress-test-denied >/dev/null 2>&1 || true
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "${work}"
}
trap cleanup EXIT

expect() {  # name actual expected-regex
  if [[ "$2" =~ ^($3)$ ]]; then
    printf 'PASS %-48s %s\n' "$1" "$2"
  else
    printf 'FAIL %-48s %s (expected %s)\n' "$1" "$2" "$3"
    failures=$((failures + 1))
  fi
}

field() { python3 -c 'import json,sys; print(json.loads(sys.argv[1])[sys.argv[2]])' "$1" "$2"; }

"${compose[@]}" build egress-proxy >/dev/null
"${compose[@]}" up --detach --wait --wait-timeout 60 egress-proxy >/dev/null
proxy_container="$("${compose[@]}" ps --quiet egress-proxy)"
expect "egress-proxy publishes no host port" "$(docker port "${proxy_container}" | wc -l | tr -d ' ')" "0"

outbound="${project}_outbound"
for name in egress-test-allowed egress-test-denied; do
  docker run --detach --name "${name}" --network "${outbound}" --read-only \
    "${API_IMAGE}" python -m http.server 8080 --bind 0.0.0.0 >/dev/null
done
allowed_ip="$(docker inspect --format "{{(index .NetworkSettings.Networks \"${outbound}\").IPAddress}}" egress-test-allowed)"
for _ in $(seq 1 20); do
  docker exec egress-test-allowed python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/', timeout=1)" >/dev/null 2>&1 && break
  sleep 0.5
done

probe_volume="${repo}/infra/egress/tests/probe.py:/probe/probe.py:ro"
direct_fail='no_dns|timeout|refused:.*'

for service in worker api; do
  result="$("${compose[@]}" run --rm --no-deps --volume "${probe_volume}" --env ALLOWED_IP="${allowed_ip}" \
    --entrypoint python "${service}" /probe/probe.py)"
  for probe in direct_allowed_name direct_allowed_ip direct_public_ip direct_metadata; do
    expect "${service}: ${probe}" "$(field "${result}" "${probe}")" "${direct_fail}"
  done
  expect "${service}: proxy_get_allowed" "$(field "${result}" proxy_get_allowed)" "200"
  expect "${service}: proxy_connect_allowed" "$(field "${result}" proxy_connect_allowed)" "tunnel_200"
  expect "${service}: proxy_connect_denied_host" "$(field "${result}" proxy_connect_denied_host)" "connect_403"
  expect "${service}: proxy_connect_allowed_host_other_port" "$(field "${result}" proxy_connect_allowed_host_other_port)" "connect_403"
  expect "${service}: proxy_get_denied" "$(field "${result}" proxy_get_denied)" "403"
  expect "${service}: proxy_metadata" "$(field "${result}" proxy_metadata)" "connect_403"
  expect "${service}: proxy_manager" "$(field "${result}" proxy_manager)" "403"
  expect "${service}: app_client_allowed (httpx via HTTP_PROXY)" "$(field "${result}" app_client_allowed)" "200"
  expect "${service}: app_client_denied (httpx via HTTP_PROXY)" "$(field "${result}" app_client_denied)" "403"
done

result="$("${compose[@]}" run --rm --no-deps --volume "${probe_volume}" --env ALLOWED_IP="${allowed_ip}" \
  --entrypoint python scheduler /probe/probe.py --direct-only)"
for probe in direct_allowed_name direct_allowed_ip direct_public_ip direct_metadata; do
  expect "scheduler: ${probe}" "$(field "${result}" "${probe}")" "${direct_fail}"
done

web="$("${compose[@]}" run --rm --no-deps --entrypoint node public-web -e \
  "const s=require('net').connect({host:'1.1.1.1',port:443,timeout:4000});s.on('connect',()=>{console.log('connected');process.exit(0)});s.on('timeout',()=>{console.log('timeout');process.exit(0)});s.on('error',(e)=>{console.log('refused:'+e.code);process.exit(0)});")"
expect "public-web: direct_public_ip" "${web}" "${direct_fail}|refused:[A-Z]+"
console="$("${compose[@]}" run --rm --no-deps --entrypoint sh console-web -c \
  'if wget -q -T 4 -O /dev/null http://1.1.1.1/ 2>/dev/null; then echo connected; else echo refused:wget; fi')"
expect "console-web: direct_public_ip" "${console}" "refused:wget"

if (( failures )); then
  echo "egress network test: ${failures} failure(s)"
  exit 1
fi
echo "egress network test: all probes passed"
