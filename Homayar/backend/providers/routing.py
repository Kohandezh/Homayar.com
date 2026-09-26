"""Privacy and eligibility filtering of stage candidates (WP08-T03, ADR 0028).

Routing order (blueprint): eligibility first — the workspace AI policy
(endpoint kind, region, manifest allowlist) and the licence — then capability
(language); only candidates that pass both reach ranking by health, quality,
latency and cost (WP08-T05).  A candidate filtered out here is never called,
never used as a fallback and never reused from a checkpoint in that attempt.

This module decides; the processor enforces the decision at the execution
boundary, immediately before each stage calls its adapter.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from backend.privacy.text_pii import PiiClass
from backend.providers.policy import StagePolicy, TextStagePolicy
from backend.providers.registry import EndpointKind, ManifestStatus, Placement, ResolvedManifest

STAGE_DISABLED = "stage_disabled"
PROVIDER_NOT_PERMITTED = "provider_not_permitted"
REGION_NOT_PERMITTED = "region_not_permitted"
MODEL_NOT_PERMITTED = "model_not_permitted"
LICENSE_NOT_PERMITTED = "license_not_permitted"
LANGUAGE_NOT_SUPPORTED = "language_not_supported"
POLICY_CHANGED = "policy_changed"
FALLBACK_NOT_APPROVED = "fallback_not_approved"
FALLBACK_NOT_EQUIVALENT = "fallback_not_equivalent"

AUDIO_STAGES = frozenset({"asr", "diarization"})
_STAGE_LABEL = {
    "asr": "speech recognition",
    "diarization": "diarization",
    "intelligence": "analysis",
}
_KIND_LABEL = {
    EndpointKind.IN_PROCESS: "in-process",
    EndpointKind.PRIVATE: "private-endpoint",
    EndpointKind.CLOUD: "cloud",
}


@dataclass(frozen=True)
class Decision:
    stage: str
    allowed: bool
    reason: str | None
    message: str | None
    # What the stage hands to its endpoint: ``none`` (in-process), ``audio``,
    # ``original_text`` or ``redacted_text``.
    disclosure: str

    def entry(self) -> dict[str, object]:
        return {
            "verdict": "allowed" if self.allowed else "denied",
            "reason": self.reason,
            "disclosure": self.disclosure,
        }


def _disclosure(stage: str, placement: Placement, *, text_redacted: bool) -> str:
    if not placement.networked:
        return "none"
    if stage in AUDIO_STAGES:
        return "audio"
    return "redacted_text" if text_redacted else "original_text"


def supports_language(resolved: ResolvedManifest, language: str | None) -> bool:
    """Explicit job languages must be declared by the manifest (``*`` = any;
    ``fa`` covers ``fa-IR``).  ``auto`` and unregistered adapters are not
    checked: capability is then unknown, not refused."""
    manifest = resolved.manifest
    if manifest is None or not language or language == "auto":
        return True
    declared = set(manifest.languages)
    return "*" in declared or language in declared or language.split("-")[0] in declared


def evaluate(
    stage: str,
    resolved: ResolvedManifest,
    policy: StagePolicy,
    *,
    language: str | None,
    text_redacted: bool = False,
) -> Decision:
    placement = resolved.placement
    disclosure = _disclosure(stage, placement, text_redacted=text_redacted)
    label = _STAGE_LABEL.get(stage, stage)

    def deny(reason: str, message: str) -> Decision:
        return Decision(stage, False, reason, message, disclosure)

    if not policy.endpoint_kinds:
        return deny(STAGE_DISABLED, f"The workspace AI policy disables {label}")
    if placement.endpoint_kind not in policy.endpoint_kinds:
        return deny(
            PROVIDER_NOT_PERMITTED,
            f"The workspace AI policy does not allow {_KIND_LABEL[placement.endpoint_kind]} "
            f"{label}",
        )
    if placement.networked and (
        placement.region not in policy.regions
        if placement.endpoint_kind is EndpointKind.CLOUD
        else bool(policy.regions) and placement.region not in policy.regions
    ):
        return deny(
            REGION_NOT_PERMITTED,
            f"The workspace AI policy does not allow {label} in this processing region",
        )
    if policy.manifests and resolved.manifest_id not in policy.manifests:
        return deny(
            MODEL_NOT_PERMITTED, f"The workspace AI policy does not allow this {label} model"
        )
    if resolved.manifest is not None and resolved.manifest.license_policy == "rejected":
        return deny(LICENSE_NOT_PERMITTED, f"The {label} model's licence is rejected")
    if not supports_language(resolved, language):
        return deny(LANGUAGE_NOT_SUPPORTED, f"The {label} model does not support this language")
    return Decision(stage, True, None, None, disclosure)


def equivalent(primary: ResolvedManifest, fallback: ResolvedManifest) -> bool:
    """A fallback serves the same contract: same structured-output mode and,
    when the primary is qualified, qualified too.  Unregistered adapters
    (development/test) are only equivalent to each other."""
    if primary.manifest is None or fallback.manifest is None:
        return primary.manifest is None and fallback.manifest is None
    if fallback.manifest.structured_output != primary.manifest.structured_output:
        return False
    if primary.manifest.status is ManifestStatus.QUALIFIED:
        return fallback.manifest.status is ManifestStatus.QUALIFIED
    return True


@dataclass(frozen=True)
class Selection:
    candidates: tuple[ResolvedManifest, ...]
    decisions: tuple[Decision, ...]
    # Eligible candidates in call order (best first); empty = none eligible.
    order: tuple[int, ...]

    @property
    def chosen_index(self) -> int | None:
        return self.order[0] if self.order else None

    @property
    def chosen(self) -> ResolvedManifest | None:
        return None if self.chosen_index is None else self.candidates[self.chosen_index]

    @property
    def decision(self) -> Decision:
        """The chosen candidate's decision, or the first refusal."""
        return self.decisions[0 if self.chosen_index is None else self.chosen_index]


Ranker = Callable[[Sequence[ResolvedManifest]], Sequence[ResolvedManifest]]


def select(
    stage: str,
    candidates: Sequence[ResolvedManifest],
    policy: StagePolicy,
    *,
    language: str | None,
    text_redacted: bool | Callable[[ResolvedManifest], bool] = False,
    rank: Ranker | None = None,
    fallbacks_from: int | None = None,
) -> Selection:
    """Filter by policy and capability first; rank only the survivors.

    ``rank`` (health/quality/latency/cost, WP08-T05) never sees a candidate
    the policy refused.  Without one, the configured order is kept.
    Candidates from index ``fallbacks_from`` on are fallbacks of the first:
    eligible only when the policy approves fallback for the stage and they
    serve an equivalent contract.
    """
    if not candidates:
        raise ValueError("a stage needs at least one candidate")
    pool = tuple(candidates)
    decisions = [
        evaluate(
            stage,
            candidate,
            policy,
            language=language,
            text_redacted=(text_redacted(candidate) if callable(text_redacted) else text_redacted),
        )
        for candidate in pool
    ]
    for index in range(fallbacks_from or len(pool), len(pool)):
        decision = decisions[index]
        if not decision.allowed:
            continue
        if not policy.fallback:
            decisions[index] = Decision(
                stage,
                False,
                FALLBACK_NOT_APPROVED,
                "The workspace AI policy does not approve a fallback for this stage",
                decision.disclosure,
            )
        elif not equivalent(pool[0], pool[index]):
            decisions[index] = Decision(
                stage,
                False,
                FALLBACK_NOT_EQUIVALENT,
                "The fallback model does not serve the same contract",
                decision.disclosure,
            )
    eligible = [c for c, d in zip(pool, decisions, strict=True) if d.allowed]
    if not eligible:
        return Selection(pool, tuple(decisions), ())
    ranked = list(rank(eligible)) if rank is not None else eligible
    if sorted(map(id, ranked)) != sorted(map(id, eligible)):
        raise ValueError("ranking may only reorder eligible candidates")
    order = tuple(next(i for i, c in enumerate(pool) if c is chosen) for chosen in ranked)
    return Selection(pool, tuple(decisions), order)


def analysis_text_classes(
    workspace_classes: Sequence[str], placement: Placement, policy: TextStagePolicy
) -> tuple[str, ...]:
    """The PII classes redacted from the analysis input.

    The workspace redaction policy (WP07-T06) applies to every endpoint; a
    cloud endpoint under ``cloud_text: redacted`` additionally receives the
    transcript redacted with every supported class even when the workspace
    shows originals.  In-process and private endpoints are unchanged.
    """
    if workspace_classes:
        return tuple(workspace_classes)
    if placement.endpoint_kind is EndpointKind.CLOUD and policy.cloud_text == "redacted":
        return tuple(sorted(c.value for c in PiiClass))
    return ()
