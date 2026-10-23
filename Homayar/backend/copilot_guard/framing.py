"""Framing of untrusted text before it reaches a model (HOM-97).

Every untrusted text (the caller's question, a transcript span, a search query) reaches a
model only inside one quoted, delimited and labelled block of exactly three lines.  The
body is a JSON string literal with every control, format and separator character escaped,
so the text can never start a new line and never close its own block.  Pure: the same
input always gives the same output.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

# The version of the guard rules of the README.  It is part of every fence.
GUARD_VERSION = "copilot-guard/1"

LABEL_PATTERN = r"^[a-z0-9][a-z0-9_.:-]{0,63}$"
# A span id from HOM-95.  The block label is ``span:<span_id>``, so it always fits
# LABEL_PATTERN.
SPAN_ID_PATTERN = r"^[a-z0-9][a-z0-9_.-]{0,57}$"
FENCE_LENGTH = 16

_LABEL = re.compile(LABEL_PATTERN)
_SPAN_ID = re.compile(SPAN_ID_PATTERN)
_ESCAPED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
# Persian writing needs the zero-width non-joiner and joiner, so they stay raw.
_RAW_JOINERS = frozenset({"\u200c", "\u200d"})


class SourceKind(StrEnum):
    CALLER_QUESTION = "caller_question"
    TRANSCRIPT = "transcript"
    SEARCH_QUERY = "search_query"


@dataclass(frozen=True)
class UntrustedText:
    """Text that a person or a transcript wrote.  It is data, never an instruction.
    ``label`` is trusted: this package or HOM-95 writes it, never the text."""

    kind: SourceKind
    label: str
    text: str

    def __post_init__(self) -> None:
        _check_untrusted(self)


def _check_untrusted(item: UntrustedText) -> None:
    if not isinstance(item.kind, SourceKind):
        raise ValueError("kind must be a SourceKind")
    if not isinstance(item.label, str) or not _LABEL.fullmatch(item.label):
        raise ValueError(f"label must match {LABEL_PATTERN}")
    if not isinstance(item.text, str):
        raise ValueError("text must be a str")


def _escape(char: str) -> str:
    """A lowercase ``\\uXXXX`` escape; a JSON surrogate pair above U+FFFF."""
    code = ord(char)
    if code <= 0xFFFF:
        return f"\\u{code:04x}"
    code -= 0x10000
    return f"\\u{0xD800 + (code >> 10):04x}\\u{0xDC00 + (code & 0x3FF):04x}"


def _body(text: str) -> str:
    """A JSON string literal of ``text``.  ``"`` and ``\\`` keep their short escapes.
    Every ``Cc``, ``Cf``, ``Zl`` and ``Zp`` character except ZWNJ and ZWJ is a
    ``\\uXXXX`` escape, the short ones (``\\n``, ``\\t``, ...) too.  Every other character
    is raw."""
    parts = ['"']
    for char in text:
        if char == '"':
            parts.append('\\"')
        elif char == "\\":
            parts.append("\\\\")
        elif char not in _RAW_JOINERS and unicodedata.category(char) in _ESCAPED_CATEGORIES:
            parts.append(_escape(char))
        else:
            parts.append(char)
    parts.append('"')
    return "".join(parts)


def _fence(item: UntrustedText) -> str:
    seed = "\x00".join((GUARD_VERSION, item.kind.value, item.label, item.text))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:FENCE_LENGTH]


def frame_untrusted(item: UntrustedText) -> str:
    """The three lines of one framed block, joined by ``"\\n"``, with no trailing newline:

    ``<<<UNTRUSTED kind=<kind> label=<label> fence=<fence>>>>``, the JSON body, and
    ``<<<END fence=<fence>>>>``.  The fence is the first 16 hex characters of the sha256
    of the version, kind, label and text.  ``ValueError`` for a bad item, and for a text
    with a lone surrogate (it has no UTF-8 form, so it has no fence).
    """
    if not isinstance(item, UntrustedText):
        raise ValueError("item must be an UntrustedText")
    _check_untrusted(item)
    if any(unicodedata.category(char) == "Cs" for char in item.text):
        raise ValueError("text holds a lone surrogate")
    fence = _fence(item)
    return "\n".join(
        (
            f"<<<UNTRUSTED kind={item.kind.value} label={item.label} fence={fence}>>>",
            _body(item.text),
            f"<<<END fence={fence}>>>",
        )
    )


@dataclass(frozen=True)
class EvidenceSpan:
    """One authorized span from HOM-95.  ``span_id`` is trusted (HOM-95 writes it).
    ``text`` is untrusted transcript text."""

    span_id: str
    conversation_id: str
    text: str

    def __post_init__(self) -> None:
        _check_span(self)


def _check_span(span: EvidenceSpan) -> None:
    if not isinstance(span.span_id, str) or not _SPAN_ID.fullmatch(span.span_id):
        raise ValueError(f"span_id must match {SPAN_ID_PATTERN}")
    if not isinstance(span.conversation_id, str):
        raise ValueError("conversation_id must be a str")
    if not isinstance(span.text, str):
        raise ValueError("text must be a str")


def check_spans(evidence: object) -> tuple[EvidenceSpan, ...]:
    """The spans in the given order.  ``ValueError`` unless ``evidence`` is a sequence of
    ``EvidenceSpan`` with distinct span ids."""
    if not isinstance(evidence, Sequence) or isinstance(evidence, str | bytes):
        raise ValueError("evidence must be a sequence of EvidenceSpan")
    spans = tuple(evidence)
    for span in spans:
        if not isinstance(span, EvidenceSpan):
            raise ValueError("evidence must be a sequence of EvidenceSpan")
        _check_span(span)
    ids = [span.span_id for span in spans]
    if len(set(ids)) != len(ids):
        raise ValueError("two spans have the same span_id")
    return spans
