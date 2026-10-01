"""Language capability registry (qualification-driven, not marketing claims).

Every ASR qualification status starts as ``PLANNED``.  A status only moves
through :func:`record_qualification`, which requires a
:class:`backend.speech.benchmark.QualificationRecord` whose gate verdict is
``PASS``; there is no code path that marks a language or dialect qualified
without measured data.  Dialect entries are *tags* for slicing benchmarks and
routing, not statements that the dialect is supported.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from backend.speech.benchmark import GateVerdict, QualificationRecord
from backend.speech.text_normalize import normalize_az, normalize_en, normalize_fa


class QualificationStatus(str, enum.Enum):
    PLANNED = "planned"  # no evidence
    IN_EVALUATION = "in_evaluation"  # data exists, gate not passed
    QUALIFIED = "qualified"  # gate PASS on a recorded benchmark


class DateSupport(str, enum.Enum):
    NONE = "none"
    BASIC = "basic"  # numeric dates only
    NATURAL = "natural"  # spoken relative expressions (dates.py)


@dataclass(frozen=True)
class Dialect:
    tag: str
    label: str
    status: QualificationStatus = QualificationStatus.PLANNED


@dataclass(frozen=True)
class LanguageCapability:
    code: str
    name: str
    scripts: tuple[str, ...]
    normalizer: Callable[..., str]
    date_support: DateSupport
    entity_support: bool
    asr_status: QualificationStatus = QualificationStatus.PLANNED
    dialects: tuple[Dialect, ...] = field(default=())
    evidence: tuple[QualificationRecord, ...] = field(default=())


def _default_registry() -> dict[str, LanguageCapability]:
    return {
        "fa": LanguageCapability(
            "fa", "Persian", ("Arab",), normalize_fa, DateSupport.NATURAL, True,
            dialects=(
                Dialect("fa-IR-x-tehrani", "Tehrani (colloquial standard)"),
                Dialect("fa-IR-x-isfahani", "Isfahani"),
                Dialect("fa-IR-x-mashhadi", "Mashhadi"),
                Dialect("fa-IR-x-shirazi", "Shirazi"),
                Dialect("fa-IR-x-yazdi", "Yazdi"),
                Dialect("fa-IR-x-south", "Southern (Bushehri/Bandari)"),
                Dialect("fa-AF", "Dari (Afghanistan)"),
                Dialect("tg", "Tajik (Cyrillic)"),
            ),
        ),
        "en": LanguageCapability("en", "English", ("Latn",), normalize_en, DateSupport.BASIC, True,
                                 dialects=(Dialect("en-x-l2-fa", "English, Persian L1 accent"),)),
        "az": LanguageCapability(
            "az", "Azerbaijani", ("Latn", "Arab"), normalize_az, DateSupport.NONE, False,
            dialects=(Dialect("az-Latn-AZ", "North Azerbaijani (Latin)"),
                      Dialect("az-Arab-IR", "South Azerbaijani / Iranian Turkic (Arabic script)")),
        ),
    }  # fmt: skip


_REGISTRY = _default_registry()


def get_language(code: str) -> LanguageCapability:
    key = code.lower().replace("_", "-")
    if key in _REGISTRY:
        return _REGISTRY[key]
    return _REGISTRY[key.split("-", 1)[0]]


def list_languages() -> tuple[LanguageCapability, ...]:
    return tuple(_REGISTRY[k] for k in sorted(_REGISTRY))


def reset_registry() -> None:
    _REGISTRY.clear()
    _REGISTRY.update(_default_registry())


def record_qualification(
    code: str, record: QualificationRecord, verdict: GateVerdict, dialect_tag: str | None = None
) -> LanguageCapability:
    """Attach evidence.  QUALIFIED only on a PASS verdict; any other verdict
    with data is IN_EVALUATION; a language never regresses silently from
    QUALIFIED because of one slice — callers re-evaluate explicitly."""
    lang = get_language(code)
    new_status = (
        QualificationStatus.QUALIFIED
        if verdict is GateVerdict.PASS
        else QualificationStatus.IN_EVALUATION
    )
    if dialect_tag is None:
        status = (
            new_status if lang.asr_status is not QualificationStatus.QUALIFIED else lang.asr_status
        )
        updated = replace(lang, asr_status=status, evidence=lang.evidence + (record,))
    else:
        if dialect_tag not in {d.tag for d in lang.dialects}:
            raise KeyError(f"Unknown dialect tag {dialect_tag!r} for {lang.code}")
        dialects = tuple(
            replace(d, status=new_status) if d.tag == dialect_tag else d for d in lang.dialects
        )
        updated = replace(lang, dialects=dialects, evidence=lang.evidence + (record,))
    _REGISTRY[lang.code] = updated
    return updated
