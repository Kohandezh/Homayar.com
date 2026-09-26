# ADR 0031 — Egress denial for AI providers

Date: 2026-09-25 · Status: accepted · Package: WP08-T06

## Context

F21 requires that, with cloud credentials present and the local provider
unavailable, a local-only tenant emits zero unauthorized outbound requests.
ADR 0028 decides and enforces routing at each stage boundary; a defect or a
new code path could still hand data to an HTTP client directly. The blueprint
asks for denial at both the application and the network layer.

## Decision

1. **Application layer: a permit per approved call.** `backend.providers.egress`
   keeps one context-local permit. The processor grants it only around a
   policy-approved call to a networked adapter, for exactly that call's
   declared endpoint (scheme, host, port and path); the OpenAI-compatible
   adapter calls `egress.check` before every request. Without a permit, or
   with a permit for another endpoint, the request is refused before any
   connection with `egress_not_permitted` (permanent). Permits do not outlive
   the call. Tests that exercise adapters directly must grant a permit
   explicitly, standing in for the routing decision.
2. **Endpoint policy (SSRF).** Manifests and adapters refuse endpoints that
   are never acceptable: cloud-metadata hosts and addresses
   (`169.254.169.254`, `169.254.170.2`, `100.100.100.200`, `fd00:ec2::254`,
   `metadata.google.internal`, …), link-local, unspecified, multicast and
   reserved addresses, credentials, queries or fragments in the URL. A
   `cloud` endpoint must be HTTPS on a public host (not localhost or a
   private/loopback address). A `private` endpoint may be an internal host or
   address: that is how an on-premise deployment approves its own endpoints.
3. **F21 behaviour.** When the configured local endpoint fails, the attempt
   retries the same endpoint; ZAI/GapGPT keys are never used while
   `LOCAL_AI_BASE_URL` is set, and a configured cloud fallback is reached only
   if the owner's policy allows cloud in its region **and** approves fallback
   (ADR 0030). Verified end to end through the real provider factory with an
   in-memory transport that records every host.
4. **Network layer: deployment configuration.** The
   application guard is not a substitute for network denial. The operator
   preflight now reports `egress`: every networked endpoint the configured
   adapters may reach (stage, primary/fallback, manifest, kind, region,
   scheme, host, port) — the allowlist source for the egress proxy or
   firewall. Required deployment changes are listed in the Session A handoff:
   workers and API on an internal network without a default route; outbound
   AI traffic only through an allowlisting egress proxy built from that
   report; the air-gapped profile with no external route at all.

## Network layer (Session C integration, 2026-09-25)

5. **On-premise topology.** In `compose.onprem.yaml` no application container
   has a route out: nginx alone is on the published `edge` network and reaches
   the web services and the API over the internal `ingress` network; the API,
   worker and scheduler use the internal `backend` network for PostgreSQL,
   Redis and MinIO; the API and worker reach `egress-proxy` over the internal
   `egress` network, and only `egress-proxy` is on `outbound`, the one network
   with a route out. The scheduler previously had no network list and joined
   the default network, which has a route out.
6. **Egress proxy.** A deny-by-default Squid (`infra/docker/egress-proxy.Dockerfile`,
   profile `egress`) forwards CONNECT and plain HTTP only to the exact
   `host:port` lines of `infra/egress/allowlist.txt`; loopback, link-local
   (metadata) and unlisted destinations and its manager pages are refused; no
   port is published. The application containers use it through
   `HTTP(S)_PROXY` (`EGRESS_PROXY_URL`) with `NO_PROXY` for the internal
   services. The air-gapped default is no proxy and no `EGRESS_PROXY_URL`: no
   outbound path at all.
7. **Allowlist source and air-gapped gate.** `model_preflight
   --egress-allowlist` prints the `host:port` lines of the configured AI
   endpoints; `--require-no-cloud` exits 3 when one is a `cloud` endpoint. An
   endpoint without a manifest is treated as `cloud` (unknown region), so a
   local model server counts as private only through an operator manifest.
   CI checks both outcomes in the API image.
8. **Evidence and its limits.** `infra/egress/tests/test-egress-network.sh`
   probes from inside the `api`, `worker`, `scheduler`, `public-web` and
   `console-web` containers as Compose attaches them, with disposable HTTP
   servers: no direct connection (names, addresses, public address,
   metadata); through the proxy only the listed destination, also through the
   application's own HTTP client. That is a single-host Docker test, not a
   production validation. The cloud profile keeps PostgreSQL, Redis and
   storage external, so its network control must be the platform firewall
   (not verified here). SMTP is not HTTP and needs an internal mail relay in
   the isolated on-premise topology (not provided).

## Not covered

- Outbound traffic that is not an AI provider call (e-mail, SMS, OAuth, object
  storage) is governed by the network layer only.
- Resolution-time checks (a public name resolving to a private address) belong
  to the egress proxy.
- The legacy `llm_router`/Copilot modules are not mounted in the v1 app or the
  worker and do not go through this guard; removing them is recommended.
