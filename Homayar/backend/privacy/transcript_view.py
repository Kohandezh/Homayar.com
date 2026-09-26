"""Which transcript version a caller receives, and the redacted texts of one
meeting (WP07-T06, ADR 0024).

``choose_view`` decides between the original transcript and the redacted
version from the workspace policy (ADR 0023) and the caller's right to see
originals: under an enforced policy everybody gets the redacted version by
default, and the original only with ``pii:original_access`` on an explicit
request.  Share links and providers never get the original under an enforced
policy (``enforced_view``).

``MeetingView`` renders every text of one meeting for that view.  Redaction is
deterministic (detector version + classes), so the texts computed here equal
the persisted redacted version of the same policy; the persisted row supplies
the version id and the restricted mappings.  Placeholders are numbered per
revision in segment order; free texts (summary, action items) continue the
numbering of the current revision, so ``[PHONE_1]`` means the same value in
the transcript and in the summary.  Offsets of evidence spans are mapped into
the redacted text and widened so that a span never shows part of a value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.saas_models import (
    ActionItem,
    Meeting,
    MeetingSummary,
    TranscriptRedaction,
    TranscriptRevision,
    TranscriptSegment,
    Workspace,
)
from backend.privacy.redaction_service import find_redaction, policy_digest, redact_revision
from backend.privacy.text_pii import (
    DETECTOR_VERSION,
    SUPPORTED_CLASSES,
    PiiClass,
    Pseudonymizer,
    Replacement,
    parse_classes,
    redact,
)
from backend.services.transcript_service import current_revision, segments_of

VIEW_ORIGINAL = "original"
VIEW_REDACTED = "redacted"
VIEWS = (VIEW_ORIGINAL, VIEW_REDACTED)

REDACTION_NOTICE = (
    "Text redaction of supported identifiers only (e-mail, phone, national ID, bank card, "
    "IBAN, postal code next to a keyword); names, addresses and other details are not "
    "detected. This is not anonymisation and audio is not masked."
)
ORIGINAL_NOTICE = "Original transcript: identifiers are not redacted."

_PLACEHOLDER_RE = re.compile(r"\[[A-Z_]+_[0-9]+\]")


class ViewDenied(Exception):
    """The caller asked for the original under an enforced policy without the right."""

    code = "original_access_denied"


@dataclass(frozen=True)
class ViewPolicy:
    view: str
    enforced: bool  # the workspace redaction policy is on
    classes: tuple[PiiClass, ...]
    digest: str | None  # policy digest of a redacted view

    @property
    def redacted(self) -> bool:
        return self.view == VIEW_REDACTED


def _workspace_classes(workspace: Workspace) -> tuple[PiiClass, ...]:
    classes = parse_classes(workspace.pii_redaction_classes or [])
    return tuple(classes or sorted(SUPPORTED_CLASSES, key=lambda c: c.value))


def choose_view(
    workspace: Workspace, *, requested: str | None, may_read_original: bool
) -> ViewPolicy:
    enforced = bool(workspace.pii_redaction_enabled)
    classes = _workspace_classes(workspace)
    wants_original = requested == VIEW_ORIGINAL or (requested is None and not enforced)
    if wants_original:
        if enforced and not may_read_original:
            raise ViewDenied()
        return ViewPolicy(VIEW_ORIGINAL, enforced, classes, None)
    return ViewPolicy(VIEW_REDACTED, enforced, classes, policy_digest(classes))


def enforced_view(workspace: Workspace) -> ViewPolicy:
    """The view for callers that may never see originals (shares, providers)."""
    return choose_view(workspace, requested=None, may_read_original=False)


@dataclass(frozen=True)
class RedactedText:
    text: str
    replacements: tuple[Replacement, ...]

    def offset(self, original: int, *, end: bool) -> int:
        """Map an offset of the original text into this text; an offset inside
        a replaced value moves to the edge of its placeholder (never inside)."""
        shift = 0
        for replacement in self.replacements:
            if original <= replacement.start:
                break
            if original < replacement.end:
                start = replacement.start + shift
                return start + len(replacement.placeholder) if end else start
            shift += len(replacement.placeholder) - (replacement.end - replacement.start)
        return original + shift


@dataclass
class RevisionRedaction:
    revision_id: str
    segments: dict[str, RedactedText]
    pseudonymizer: Pseudonymizer
    record: TranscriptRedaction | None

    @property
    def entity_count(self) -> int:
        return sum(len(item.replacements) for item in self.segments.values())


def _copy(pseudonymizer: Pseudonymizer) -> Pseudonymizer:
    clone = Pseudonymizer()
    clone.tokens = dict(pseudonymizer.tokens)
    clone.counters = dict(pseudonymizer.counters)
    return clone


class MeetingView:
    """The texts of one meeting as one caller may see them.

    ``persist`` allows creating the persisted redacted version of a revision
    that has none yet (tenant-bound sessions only: a share session cannot
    write); ``created`` tells the caller to commit.
    """

    def __init__(
        self,
        db: Session,
        *,
        meeting: Meeting,
        policy: ViewPolicy,
        settings: Settings,
        now: datetime,
        persist: bool,
        actor_user_id: str | None = None,
    ) -> None:
        self.db = db
        self.meeting = meeting
        self.policy = policy
        self._settings = settings
        self._now = now
        self._persist = persist
        self._actor_user_id = actor_user_id
        self._revisions: dict[str, RevisionRedaction] = {}
        self._free: Pseudonymizer | None = None
        self._free_values: dict[str, str] = {}
        self.created = False

    @property
    def redacted(self) -> bool:
        return self.policy.redacted

    # --- transcript ---------------------------------------------------------

    def revision(self, revision: TranscriptRevision) -> RevisionRedaction | None:
        if not self.redacted:
            return None
        cached = self._revisions.get(revision.id)
        if cached is not None:
            return cached
        pseudonymizer = Pseudonymizer()
        segments: dict[str, RedactedText] = {}
        for segment in segments_of(self.db, revision):
            text, replacements = redact(
                segment.text, self.policy.classes, pseudonymizer=pseudonymizer
            )
            segments[segment.id] = RedactedText(text, tuple(replacements))
        record = None
        if self._persist:
            assert self.policy.digest is not None
            record = find_redaction(
                self.db,
                workspace_id=revision.workspace_id,
                revision_id=revision.id,
                digest=self.policy.digest,
            )
            if record is None:
                record = redact_revision(
                    self.db,
                    revision=revision,
                    classes=self.policy.classes,
                    settings=self._settings,
                    now=self._now,
                    actor_user_id=self._actor_user_id,
                )
                self.created = True
        result = RevisionRedaction(revision.id, segments, pseudonymizer, record)
        self._revisions[revision.id] = result
        return result

    def segment_text(self, segment: TranscriptSegment, revision: TranscriptRevision) -> str:
        redaction = self.revision(revision)
        if redaction is None:
            return segment.text
        return redaction.segments[segment.id].text

    def span(
        self, segment: TranscriptSegment, revision: TranscriptRevision, start: int, end: int
    ) -> tuple[str, int, int]:
        """Text and offsets of a citation in this view."""
        redaction = self.revision(revision)
        if redaction is None:
            return segment.text[start:end], start, end
        item = redaction.segments[segment.id]
        mapped_start = item.offset(start, end=False)
        mapped_end = item.offset(end, end=True)
        return item.text[mapped_start:mapped_end], mapped_start, mapped_end

    # --- free text (summary, action items) ------------------------------------

    def _free_pseudonymizer(self) -> Pseudonymizer:
        """Numbering continues the current revision, then runs over the summary
        and the action items in one canonical order, so a value keeps its
        placeholder whichever endpoint renders it."""
        if self._free is None:
            current = current_revision(
                self.db, workspace_id=self.meeting.workspace_id, meeting_id=self.meeting.id
            )
            seeded = self.revision(current) if current is not None else None
            self._free = _copy(seeded.pseudonymizer) if seeded is not None else Pseudonymizer()
            for value in self._canonical_free_texts():
                self._redact_free(value)
        return self._free

    def _canonical_free_texts(self) -> list[str]:
        values: list[str] = []
        summary = self.db.scalar(
            select(MeetingSummary).where(
                MeetingSummary.workspace_id == self.meeting.workspace_id,
                MeetingSummary.meeting_id == self.meeting.id,
            )
        )
        if summary is not None:
            values.append(summary.overview)
            for point in [*(summary.key_points or []), *(summary.decisions or [])]:
                if isinstance(point, dict):
                    values.extend(
                        str(point[key]) for key in ("category", "description") if point.get(key)
                    )
            values.extend(str(topic) for topic in summary.topics or [])
        actions = self.db.scalars(
            select(ActionItem)
            .where(
                ActionItem.workspace_id == self.meeting.workspace_id,
                ActionItem.meeting_id == self.meeting.id,
            )
            .order_by(ActionItem.created_at.asc(), ActionItem.id.asc())
        )
        for action in actions:
            values.append(action.description)
            if action.assignee_text:
                values.append(action.assignee_text)
        return values

    def _redact_free(self, value: str) -> str:
        assert self._free is not None
        text, replacements = redact(value, self.policy.classes, pseudonymizer=self._free)
        for replacement in replacements:
            self._free_values.setdefault(replacement.placeholder, replacement.value)
        return text

    def text(self, value: str | None) -> str | None:
        if value is None or not self.redacted:
            return value
        self._free_pseudonymizer()
        return self._redact_free(value)

    def point(self, value: object) -> object:
        """A summary key point / decision with its texts in this view."""
        if not self.redacted or not isinstance(value, dict):
            return value
        return {
            key: self.text(item)
            if key in ("category", "description") and isinstance(item, str)
            else item
            for key, item in value.items()
        }

    def texts(self, values: list[str]) -> list[str]:
        return [self.text(value) or "" for value in values]

    # --- placeholders written back by a person -------------------------------

    def restore(
        self,
        value: str,
        *,
        revision: TranscriptRevision | None = None,
        segment_id: str | None = None,
    ) -> str:
        """Put originals back for placeholders this view showed.

        A person editing a redacted text returns placeholders; without this
        step a correction would overwrite the original value with the token.
        Known placeholders of the segment win, then of the revision, then of
        the free texts; unknown ones stay literal.
        """
        if not self.redacted or "[" not in value:
            return value
        self._free_pseudonymizer()
        known: dict[str, str] = dict(self._free_values)
        redaction = self.revision(revision) if revision is not None else None
        if redaction is not None:
            for item in redaction.segments.values():
                for replacement in item.replacements:
                    known.setdefault(replacement.placeholder, replacement.value)
            if segment_id is not None and segment_id in redaction.segments:
                for replacement in redaction.segments[segment_id].replacements:
                    known[replacement.placeholder] = replacement.value
        return _PLACEHOLDER_RE.sub(lambda match: known.get(match.group(), match.group()), value)

    # --- provenance -----------------------------------------------------------

    def provenance(self, revision: TranscriptRevision | None) -> dict[str, object]:
        """What an export or a share view was rendered from (never a value)."""
        data: dict[str, object] = {
            "view": self.policy.view,
            "policy_enforced": self.policy.enforced,
        }
        if not self.redacted:
            data["notice"] = ORIGINAL_NOTICE if self.policy.enforced else None
            return data
        redaction = self.revision(revision) if revision is not None else None
        data.update(
            detector_version=DETECTOR_VERSION,
            classes=[c.value for c in self.policy.classes],
            policy_digest=self.policy.digest,
            redaction_id=redaction.record.id if redaction and redaction.record else None,
            entity_count=redaction.entity_count if redaction is not None else 0,
            notice=REDACTION_NOTICE,
        )
        return data
