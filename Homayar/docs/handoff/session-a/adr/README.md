# Session A ADR drafts: superseded

The files in this directory, `0026` to `0031`, are **superseded drafts**. Session A
wrote them for WP08, and the integration pass then copied them into the website
ADR set. They are kept only as a record of that hand-off. Do not cite, edit or
extend them.

The authoritative decisions are the accepted ADRs in
`docs/architecture/decisions/` at the repository root:

| Draft here | Authoritative ADR |
|---|---|
| `0026-model-manifests-and-preflight.md` | `docs/architecture/decisions/0026-model-manifests-and-preflight.md` |
| `0027-ai-policies-and-run-snapshots.md` | `docs/architecture/decisions/0027-ai-policies-and-run-snapshots.md` |
| `0028-privacy-eligibility-routing.md` | `docs/architecture/decisions/0028-privacy-eligibility-routing.md` |
| `0029-adapter-contract-usage-and-padyar-boundary.md` | `docs/architecture/decisions/0029-adapter-contract-usage-and-padyar-boundary.md` |
| `0030-provider-resilience-and-same-policy-fallback.md` | `docs/architecture/decisions/0030-provider-resilience-and-same-policy-fallback.md` |
| `0031-egress-denial.md` | `docs/architecture/decisions/0031-egress-denial.md` |

The drafts and the accepted ADRs differ. Each draft still reads "Status: accepted
(draft for the website ADR set)" and carries a note about the copy step. The
accepted `0031` also adds the network-layer decisions from the Session C
integration (on-premise topology and the egress proxy). If the two ever
disagree, the ADR under `docs/architecture/decisions/` wins.
