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

A free text can also quote part of a value, which the detector does not see
as a value: a summary written before redaction was on can say ``۰۹۱۲ ۳۴۵`` of
the phone ``۰۹۱۲ ۳۴۵ ۶۷۸۹``, or ``example.com`` of ``ali.rezaei@example.com``
(HOM-284).  So after the whole values are replaced, the text that is still
visible is searched for fragments of the known values.  The known values are
the ones the free pseudonymizer holds: the current revision's values and the
whole values of the free texts.

* R1, digits: a digit run is digits (Persian, Arabic-Indic, fullwidth or
  ASCII) joined directly or by one of the detector's separators; a leading
  ``+`` and brackets around an area code belong to it.  A run of at least four
  digits is a fragment when it shares four consecutive digits with a known
  phone, national ID, bank card, IBAN or postal code.  For an Iranian phone,
  a ``98``/``0098``/``+98`` prefix of the run is also compared as the trunk ``0``.
* R2, e-mail: a token is a run of ``[A-Za-z0-9._%+@-]`` with ``.``, ``-`` and
  ``_`` trimmed at its ends.  The whole token is a fragment when it contains a
  part of a known address of at least four characters that (a) contains
  ``@``, or contains ``.`` and covers at least one whole label (the local part
  split at ``.``, and the domain labels other than the last); or (b) is the
  whole local part, one label of the local part, or one whole domain label
  other than the last, with the token edge, one of ``._%+@-`` or a digit on
  each side.  So ``www.example.com``, ``ali.r``, ``ali.rezaei2``, ``rezaei``
  and ``x-example`` are fragments of ``ali.rezaei@example.com``;
  ``examples``, ``rezaeian``, ``google.com``, ``i.re``, ``ali`` and ``com``
  are not.
* R3: the whole run or token shows the placeholder of the value it belongs
  to, as ``span`` widens a cut inside a value.  When several values match, the
  value with the longest common part wins, then the detector's class priority
  (e-mail, IBAN, bank card, phone, national ID, postal code), then the lowest
  placeholder number.  A fragment never creates a placeholder, so the
  numbering stays as described above.
* R6: only the placeholders of this view are skipped as hidden.  Text shaped
  like another placeholder (``[A_0912345]``) is searched like any other text.

The known values are looked up by 4-character windows (for an address, only
the windows every R2 match holds), so only the values that share one with a
run or a token are ranked.

``restore`` with the stored text gives a placeholder back the exact stored
text it stood for, so editing an action keeps a fragment a fragment, but only
when the placeholder is kept from the shown text (not added), every occurrence
of it there stood for the same stored text (a known placeholder stored as text
counts as its own stored text), and no ``[A-Za-z0-9]`` is glued to it after
digit folding; otherwise it restores the whole value.  A whole value glued to other
characters (``[EMAIL_1]2``) is stored as written; the read path above masks it.

A transcript segment has no such fragment masking: it is redacted with whole
values only.  So ``restore_segment``, the write-back of a correction
(HOM-288), refuses what the detector would not find again as the same value:
``[PHONE_1]2`` would store a longer number, shown raw, and ``x[EMAIL_1]`` or
``a.[EMAIL_1]`` another address.  Kept or added is decided against the
segment as shown, not against its free-text rendering, on whole tokens and the
text between them.  A kept placeholder gets back its exact stored text (one
stored as text stays text), an added one the whole value.  Refused: a
placeholder the shown segment uses both as its own text and for a value, an
added one without a value, and a value next to ``[A-Za-z0-9]`` (after digit
folding) or not found again as exactly one detector replacement with its start
and end.

Residual risk: a value that occurs only in an older revision (corrected away
since) is not a known value, so its fragments in a free text stay visible.
Digits split by a separator of two or more characters are separate runs:
``۰۹۱۲ - ۳۴۵ - ۶۷۸۹`` shows ``۳۴۵``.  A whole value spelled with characters
that are not separators (``091.234.567.89``, a ZWNJ between groups) is not
detected: when no other text makes it a known value it stays visible, as before
this rule; when it is known, only its groups of four or more digits are masked
(``0912.345.6789`` shows ``[PHONE_1].345.[PHONE_1]``).  Names and numbers spelled
out in words are not seen.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher

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
from backend.speech.text_normalize import normalize_digits_extended

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

# Fragments of known values in free text (module docstring, R1-R3).  The
# separators and the class priority are the detector's (``text_pii._SEP``,
# ``text_pii._PRIORITY``); a test keeps them equal.
_SEPARATORS = " \u00a0\\-\u2010\u2011\u2012\u2013"  # space, NBSP, hyphen, dashes
_DIGIT_RUN_RE = re.compile(rf"(?:\+[{_SEPARATORS}]?)?\(?[0-9](?:\)?[{_SEPARATORS}]?\(?[0-9])*")
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9._%+@-]+")
_MIN_FRAGMENT = 4
_COUNTRY_PREFIXES = ("0098", "98")
_DIGIT_CLASSES = frozenset(
    {
        PiiClass.PHONE,
        PiiClass.IR_NATIONAL_ID,
        PiiClass.BANK_CARD,
        PiiClass.IBAN,
        PiiClass.IR_POSTAL_CODE,
    }
)
_CLASS_ORDER = (
    PiiClass.EMAIL,
    PiiClass.IBAN,
    PiiClass.BANK_CARD,
    PiiClass.PHONE,
    PiiClass.IR_NATIONAL_ID,
    PiiClass.IR_POSTAL_CODE,
)


class ViewDenied(Exception):
    """The caller asked for the original under an enforced policy without the right."""

    code = "original_access_denied"


class PlaceholderRefused(Exception):
    """A corrected segment would store a placeholder as text, or a value the
    redacted view would then show raw or as another value (HOM-288)."""

    code = "invalid_placeholder"


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


@dataclass(frozen=True)
class _Known:
    """A known value as the fragment rule compares it."""

    pii_class: PiiClass
    placeholder: str
    text: str  # the value digits, or the lower-case e-mail address

    @property
    def iranian_phone(self) -> bool:
        # Canonical Iranian numbers start with the trunk 0; international ones with 00.
        if self.pii_class is not PiiClass.PHONE:
            return False
        return self.text.startswith("0") and not self.text.startswith("00")

    def rank(self, common: int) -> tuple[int, int, int]:
        """R3: the longest common part, then the class priority, then the lowest number."""
        number = int(self.placeholder[self.placeholder.rindex("_") + 1 : -1])
        return (-common, _CLASS_ORDER.index(self.pii_class), number)


def _common_part(a: str, b: str) -> int:
    return SequenceMatcher(None, a, b, autojunk=False).find_longest_match().size


def _collapsed(text: str) -> tuple[str, list[int]]:
    """``" ".join(text.split())``, as a correction stores a segment, and the
    offset there of each character of ``text``."""
    out: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            offsets.append(len(out))
            continue
        if out and text[index - 1].isspace():
            out.append(" ")
        offsets.append(len(out))
        out.append(char)
    return "".join(out), offsets


# Aligning n pieces costs about n**3 steps when they repeat: 200 take some
# 0.03 s, 800 take seconds (HOM-288, R10).
_MAX_ALIGNED_PIECES = 200


def _pieces(text: str) -> list[tuple[int, str]]:
    """``text`` as placeholder tokens and the text between them, each with its
    offset; the tokens are at the odd indexes."""
    pieces: list[tuple[int, str]] = []
    cursor = 0
    for match in _PLACEHOLDER_RE.finditer(text):
        pieces += [(cursor, text[cursor : match.start()]), (match.start(), match.group())]
        cursor = match.end()
    pieces.append((cursor, text[cursor:]))
    return pieces


def _kept_tokens(shown: str, value: str) -> dict[int, int]:
    """The placeholders of ``value`` kept from ``shown``: offset in ``value`` to
    offset in ``shown``.

    Both texts are compared as whole pieces (a token, or the text between two
    tokens), never character by character.  The equal pieces at both ends are
    kept; the changed middle is aligned with ``SequenceMatcher`` only when it
    has at most ``_MAX_ALIGNED_PIECES`` pieces on each side, else its tokens
    count as added.
    """
    old, new = _pieces(shown), _pieces(value)
    pairs: list[tuple[int, int]] = []
    head = 0
    while head < min(len(old), len(new)) and old[head][1] == new[head][1]:
        pairs.append((head, head))
        head += 1
    tail = 0
    while tail < min(len(old), len(new)) - head and old[-1 - tail][1] == new[-1 - tail][1]:
        tail += 1
        pairs.append((len(old) - tail, len(new) - tail))
    middle_old = [text for _, text in old[head : len(old) - tail]]
    middle_new = [text for _, text in new[head : len(new) - tail]]
    if max(len(middle_old), len(middle_new)) <= _MAX_ALIGNED_PIECES:
        matcher = SequenceMatcher(None, middle_old, middle_new, autojunk=False)
        for block in matcher.get_matching_blocks():
            pairs += [(head + block.a + k, head + block.b + k) for k in range(block.size)]
    return {new[j][0]: old[i][0] for i, j in pairs if j % 2}


def _windows(text: str) -> Iterator[str]:
    """Every run of ``_MIN_FRAGMENT`` characters of ``text``, left to right."""
    return (text[i : i + _MIN_FRAGMENT] for i in range(len(text) - _MIN_FRAGMENT + 1))


@dataclass
class _KnownValues:
    """The known values of a view, looked up by their 4-character windows, so
    only values that share a window with a run or a token are ranked (P1)."""

    size: int  # the number of pseudonymizer tokens it was built from
    placeholders: frozenset[str]
    digits: dict[str, list[_Known]]  # a 4-digit window -> the digit values holding it
    addresses: dict[str, list[_Known]]  # an R2 key window (_email_keys) -> the addresses

    @classmethod
    def of(cls, tokens: Mapping[tuple[PiiClass, str], str]) -> _KnownValues:
        known = cls(len(tokens), frozenset(tokens.values()), {}, {})
        for (pii_class, value), placeholder in tokens.items():
            if pii_class is PiiClass.EMAIL:
                address = _Known(pii_class, placeholder, value.lower())
                for window in _email_keys(address.text):
                    known.addresses.setdefault(window, []).append(address)
            elif pii_class in _DIGIT_CLASSES:
                digits = _Known(pii_class, placeholder, re.sub(r"[^0-9]", "", value))
                for window in set(_windows(digits.text)):
                    known.digits.setdefault(window, []).append(digits)
        return known


def _visible_parts(
    text: str, whole: Sequence[Replacement], placeholders: frozenset[str]
) -> Iterator[tuple[int, int]]:
    """The parts of a free text the detector left as they are, without the
    placeholders of this view the text already holds (R6: text shaped like
    another placeholder is searched like any other text)."""
    hidden = sorted(
        [(item.start, item.end) for item in whole]
        + [
            match.span()
            for match in _PLACEHOLDER_RE.finditer(text)
            if match.group() in placeholders
        ]
    )
    cursor = 0
    for start, end in hidden:
        if cursor < start:
            yield cursor, start
        cursor = max(cursor, end)
    if cursor < len(text):
        yield cursor, len(text)


def _digit_matches(run: str, known: _KnownValues) -> list[tuple[int, _Known]]:
    digits = re.sub(r"[^0-9]", "", run)
    if len(digits) < _MIN_FRAGMENT:
        return []
    forms = [digits]
    prefix = next((p for p in _COUNTRY_PREFIXES if digits.startswith(p)), None)
    if prefix is not None:
        forms.append("0" + digits[len(prefix) :])
    candidates: dict[_Known, list[str]] = {}  # each value with the forms it is compared to
    for index, form in enumerate(forms):
        for window in _windows(form):
            for value in known.digits.get(window, ()):
                if index == 0 or value.iranian_phone:
                    candidates[value] = forms if value.iranian_phone else forms[:1]
    if not candidates:
        return []
    size, best = _shared_digits(candidates)
    return [(size, value) for value in best]


def _shared_digits(candidates: Mapping[_Known, list[str]]) -> tuple[int, list[_Known]]:
    """R3: the values that share the longest digit run with the run, and its
    length.  Each candidate already shares ``_MIN_FRAGMENT`` digits; the length
    grows by one digit while some value still shares it."""
    size, best = _MIN_FRAGMENT, list(candidates)
    while True:
        longer = [
            value
            for value in best
            if any(
                form[start : start + size + 1] in value.text
                for form in candidates[value]
                for start in range(len(form) - size)
            )
        ]
        if not longer:
            return size, best
        size, best = size + 1, longer


def _bounded(token: str, start: int, end: int) -> bool:
    """R2 (b): each side of the part is the token edge, ``._%+@-``, or a digit."""
    sides = (token[start - 1] if start else "", token[end] if end < len(token) else "")
    return all(side == "" or side in "._%+@-0123456789" for side in sides)


def _labels(address: str) -> list[tuple[int, int]]:
    """Spans of the labels R2 (a) counts: the local part split at ``.``, and
    the domain labels before the last one."""
    local, _, domain = address.rpartition("@")
    spans: list[tuple[int, int]] = []
    start = 0
    for label in [*local.split("."), *domain.split(".")[:-1]]:
        spans.append((start, start + len(label)))
        start += len(label) + 1  # the "." (or the "@" after the local part)
    return [(start, end) for start, end in spans if start < end]


def _email_keys(address: str) -> set[str]:
    """Windows of ``address`` of which every R2 match holds at least one: a
    window with the ``@``, the first four characters of a label, or, for a
    label shorter than four, each window around it.  ``.com`` alone is none."""
    keys = {window for window in _windows(address) if "@" in window}
    for start, end in _labels(address):
        if end - start >= _MIN_FRAGMENT:
            keys.add(address[start : start + _MIN_FRAGMENT])
            continue
        for first in range(max(0, end - _MIN_FRAGMENT), start + 1):
            if first + _MIN_FRAGMENT <= len(address):
                keys.add(address[first : first + _MIN_FRAGMENT])
    return keys


def _is_email_fragment(token: str, address: str) -> bool:
    """R2: ``token`` holds a part of ``address`` of at least four characters."""
    labels = _labels(address)
    for i in range(len(token)):
        for j in range(len(address)):
            if token[i] != address[j] or (i and j and token[i - 1] == address[j - 1]):
                continue  # a common part that starts further left holds this one
            size = 1
            while (
                i + size < len(token)
                and j + size < len(address)
                and token[i + size] == address[j + size]
            ):
                size += 1
            part = address[j : j + size]
            if size >= _MIN_FRAGMENT and (
                "@" in part
                or ("." in part and any(j <= start and end <= j + size for start, end in labels))
            ):
                return True  # case (a)
    local, _, domain = address.rpartition("@")
    for part in {local, *local.split("."), *domain.split(".")[:-1]}:
        if len(part) < _MIN_FRAGMENT:
            continue
        start = token.find(part)
        while start >= 0:
            if _bounded(token, start, start + len(part)):
                return True  # case (b)
            start = token.find(part, start + 1)
    return False


def _email_matches(token: str, known: _KnownValues) -> list[tuple[int, _Known]]:
    token = token.lower()
    candidates: dict[_Known, None] = {}  # an ordered set
    for window in _windows(token):
        for address in known.addresses.get(window, ()):
            candidates[address] = None
    return [
        (_common_part(token, address.text), address)
        for address in candidates
        if _is_email_fragment(token, address.text)
    ]


def _fragments(text: str, whole: Sequence[Replacement], known: _KnownValues) -> list[Replacement]:
    """Replacements for the fragments of known values in ``text`` (R1-R3, R6).

    ``whole`` are the detector's replacements in ``text``.  Offsets refer to
    ``text``.
    """
    if not known.placeholders:
        return []
    folded = normalize_digits_extended(text)  # one character each: offsets are kept
    found: list[tuple[int, int, int, _Known]] = []
    for part_start, part_end in _visible_parts(text, whole, known.placeholders):
        for match in _DIGIT_RUN_RE.finditer(folded, part_start, part_end):
            start, end = match.span()
            first = start + next(i for i, char in enumerate(match.group()) if char.isdigit())
            if "(" in folded[start:first] and ")" not in folded[first:end]:
                start = first  # an opening bracket no area code closes stays visible
            found.extend(
                (start, end, common, value)
                for common, value in _digit_matches(folded[start:end], known)
            )
        for match in _LATIN_TOKEN_RE.finditer(folded, part_start, part_end):
            start, end = match.span()
            while start < end and folded[start] in "._-":
                start += 1
            while end > start and folded[end - 1] in "._-":
                end -= 1
            found.extend(
                (start, end, common, value)
                for common, value in _email_matches(folded[start:end], known)
            )
    # Overlapping fragments (a run inside a token) become one replacement, so
    # no character of either stays visible.
    groups: list[tuple[int, int, list[tuple[int, _Known]]]] = []
    for start, end, common, known in sorted(found, key=lambda item: (item[0], item[1])):
        if groups and start < groups[-1][1]:
            group_start, group_end, matches = groups.pop()
            groups.append((group_start, max(group_end, end), [*matches, (common, known)]))
        else:
            groups.append((start, end, [(common, known)]))
    replacements: list[Replacement] = []
    for start, end, matches in groups:
        _, best = min(matches, key=lambda match: match[1].rank(match[0]))
        replacements.append(
            Replacement(start, end, best.pii_class, best.placeholder, text[start:end])
        )
    return replacements


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
        self._known: _KnownValues | None = None
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
                    owner = point.get("owner")  # a structured decision (HOM-61)
                    if isinstance(owner, dict) and isinstance(owner.get("value"), str):
                        values.append(owner["value"])
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

    def _redact_free(self, value: str) -> RedactedText:
        assert self._free is not None
        text, replacements = redact(value, self.policy.classes, pseudonymizer=self._free)
        for replacement in replacements:
            self._free_values.setdefault(replacement.placeholder, replacement.value)
        return RedactedText(text, tuple(replacements))

    def _render_free(self, value: str) -> RedactedText:
        """A free text in this view: whole values as the detector finds them,
        then each fragment of a known value as that value's placeholder."""
        free = self._free_pseudonymizer()
        whole = self._redact_free(value)
        # The tokens only grow, so their number tells when the index is stale.
        if self._known is None or self._known.size != len(free.tokens):
            self._known = _KnownValues.of(free.tokens)
        fragments = _fragments(value, whole.replacements, self._known)
        if not fragments:
            return whole
        replacements = sorted([*whole.replacements, *fragments], key=lambda item: item.start)
        parts: list[str] = []
        cursor = 0
        for replacement in replacements:
            parts += [value[cursor : replacement.start], replacement.placeholder]
            cursor = replacement.end
        parts.append(value[cursor:])
        return RedactedText("".join(parts), tuple(replacements))

    def text(self, value: str | None) -> str | None:
        if value is None or not self.redacted:
            return value
        return self._render_free(value).text

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
        previous: str | None = None,
    ) -> str:
        """Put originals back for placeholders this view showed.

        A person editing a redacted text returns placeholders; without this
        step a correction would overwrite the original value with the token.
        With ``previous``, the stored free text the person edited, a
        placeholder gets back the exact stored text it stood for there (a
        fragment stays a fragment) only when it is kept from the shown text
        (not added), unambiguous (every occurrence there, including one stored
        as text, stood for the same stored text), and not glued to
        ``[A-Za-z0-9]`` in ``value`` after digit folding.  Other
        known placeholders get the whole value (``_known_values``); unknown
        ones stay literal.
        """
        if not self.redacted or "[" not in value:
            return value
        known = self._known_values(revision, segment_id)
        kept = self._kept_placeholders(value, previous) if previous else {}

        def original(match: re.Match[str]) -> str:
            if match.start() in kept:
                return kept[match.start()]
            return known.get(match.group(), match.group())

        return _PLACEHOLDER_RE.sub(original, value)

    def _known_values(
        self, revision: TranscriptRevision | None, segment_id: str | None
    ) -> dict[str, str]:
        """The whole value of each placeholder: the segment's win, then the
        free texts', then the revision's."""
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
        return known

    def restore_segment(self, value: str, *, revision: TranscriptRevision, segment_id: str) -> str:
        """The text a person corrected in one segment of ``revision``, as it is
        stored (whitespace collapsed like ``correct_revision``), HOM-288.

        A placeholder the edit kept from the shown segment (``_kept_tokens``)
        gets back the exact stored text it stood for there, so each spelling of
        a value stays as it was; one stored as text stays that text.  An added
        or moved placeholder gets the whole value (``_known_values``).  Raises
        ``PlaceholderRefused`` for a token that the shown segment uses both as
        its own text and for a value, an added token without a value, and a
        value put back next to ``[A-Za-z0-9]`` (after digit folding) or not
        found again as exactly one detector replacement with its start and
        end: the redacted view would show it raw, or as another value.
        """
        matches = list(_PLACEHOLDER_RE.finditer(value)) if self.redacted else []
        if not matches:
            return value
        redaction = self.revision(revision)
        assert redaction is not None
        shown = redaction.segments[segment_id]
        # What each placeholder of the shown segment stood for, and whether
        # that is a value (not the token stored as text).
        starts = {shown.offset(item.start, end=False): item for item in shown.replacements}
        at: dict[int, tuple[str, bool]] = {}
        kinds: dict[str, set[bool]] = {}
        for match in _PLACEHOLDER_RE.finditer(shown.text):
            item = starts.get(match.start())
            at[match.start()] = (match.group(), False) if item is None else (item.value, True)
            kinds.setdefault(match.group(), set()).add(item is not None)
        known = self._known_values(revision, segment_id)
        for match in matches:  # refused before any alignment work
            if len(kinds.get(match.group(), ())) > 1:
                raise PlaceholderRefused()  # text and a value: a guess may restore the wrong one
            if match.group() not in kinds and match.group() not in known:
                raise PlaceholderRefused()  # unknown: it would be stored as text
        kept = _kept_tokens(shown.text, value)
        parts: list[str] = []
        values: list[tuple[int, int]] = []
        size = cursor = 0
        for match in matches:
            if match.start() in kept:
                restored, is_value = at[kept[match.start()]]
            elif match.group() in known:
                restored, is_value = known[match.group()], True
            else:
                raise PlaceholderRefused()  # an added copy of a token stored as text
            parts.append(value[cursor : match.start()])
            size += len(parts[-1])
            if is_value:
                values.append((size, size + len(restored)))
            parts.append(restored)
            size += len(restored)
            cursor = match.end()
        parts.append(value[cursor:])
        stored, offsets = _collapsed("".join(parts))
        folded = normalize_digits_extended(stored)
        found = {(item.start, item.end) for item in redact(stored, self.policy.classes)[1]}
        for start, end in values:
            start, end = offsets[start], offsets[end - 1] + 1
            sides = folded[start - 1 : start] + folded[end : end + 1]
            if any(char.isascii() and char.isalnum() for char in sides):
                raise PlaceholderRefused()  # glued: another value, or none
            if (start, end) not in found:
                raise PlaceholderRefused()  # not found again as this value, whole
        return stored

    def _kept_placeholders(self, value: str, previous: str) -> dict[int, str]:
        """The placeholders of ``value`` that get back their stored text from
        ``previous`` (SPEC 11.2), by position, with that text."""
        shown = self._render_free(previous)
        stored: dict[str, set[str]] = {}
        for item in shown.replacements:
            stored.setdefault(item.placeholder, set()).add(item.value)
        assert self._known is not None
        for match in _PLACEHOLDER_RE.finditer(previous):
            if match.group() in self._known.placeholders:
                stored.setdefault(match.group(), set()).add(match.group())  # stored as text
        by_offset = {shown.offset(item.start, end=False): item for item in shown.replacements}
        kept: dict[int, str] = {}
        # Placeholders in a block the edit left unchanged are kept; the others
        # were added (or moved) by the person.
        matcher = SequenceMatcher(None, shown.text, value, autojunk=False)
        for block in matcher.get_matching_blocks():
            for match in _PLACEHOLDER_RE.finditer(value, block.b, block.b + block.size):
                item = by_offset.get(block.a + match.start() - block.b)
                if item is None or item.placeholder != match.group():
                    continue
                if len(stored[item.placeholder]) != 1:
                    continue  # ambiguous
                sides = normalize_digits_extended(
                    value[match.start() - 1 : match.start()] + value[match.end() : match.end() + 1]
                )
                if any(char.isascii() and char.isalnum() for char in sides):
                    continue  # glued: the stored text would join the letter or digit
                kept[match.start()] = item.value
        return kept

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
