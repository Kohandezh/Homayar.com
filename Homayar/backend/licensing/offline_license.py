"""The signed offline licence of HOM-109: verification, states and the gate error.

Trust follows ADR 0070 (signed offline bundles): Ed25519 over the exact file
bytes, verifying keys are the ``*.pem`` files of a host directory, any trusted
key verifies (rotation), and a key fingerprint is the first 16 hex characters
of the sha256 of the DER public key. The verification outcome is cached per
process against the ``(st_mtime_ns, st_size, st_ino)`` of every input file; the
state is recomputed from ``now`` on every evaluation, never cached (ADR 0073).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from backend.services.capability_registry import CAPABILITY_MODULES
from backend.services.logging_service import logger

LICENSE_SCHEMA = "homayar-offline-license/1"
LICENSE_MAX_BYTES = 65536
SIGNATURE_BYTES = 64
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
ZULU_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
ZULU_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
NOT_YET_VALID_SLACK = timedelta(days=1)
EXPIRES_SOON_DAYS = 30
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
PROCESSING_STATES = frozenset({"valid", "grace"})
RESTRICTED_STATES = frozenset({"expired", "invalid", "missing"})
REGISTRY_KEYS = frozenset(module.key for module in CAPABILITY_MODULES)
LICENSE_FIELDS = frozenset(
    {
        "schema",
        "license_id",
        "customer_id",
        "customer_name",
        "issued_at",
        "expires_at",
        "grace_days",
        "modules",
        "limits",
    }
)
RESTRICTED_MESSAGE = "The offline licence does not allow new processing"


class LicenseRestrictedError(Exception):
    """A restricted licence state refuses new processing (SPEC 2.5).

    Not a ``QuotaExceededError``: callers map it to 403 ``license_restricted``.
    """

    code = "license_restricted"

    def __init__(self, state: str, reason: str | None):
        self.state = state
        self.reason = reason
        super().__init__(f"{RESTRICTED_MESSAGE} ({state})")


@dataclass(frozen=True)
class VerifiedLicense:
    """The signed, parsed payload of a licence file, before any time decision."""

    license_id: str
    customer_id: str
    customer_name: str
    issued_at: datetime
    expires_at: datetime
    grace_days: int
    # Derived when parsed, so a date at the edge of the datetime range is
    # malformed once instead of overflowing on every evaluation.
    not_before: datetime
    grace_until: datetime
    modules: tuple[str, ...]
    unknown_modules: tuple[str, ...]
    limits: dict[str, int | None]
    unknown_limits: tuple[str, ...]
    key_fingerprint: str


@dataclass(frozen=True)
class LicenseStatus:
    enforced: bool
    state: str
    reason: str | None
    processing_allowed: bool
    license_id: str | None = None
    customer_id: str | None = None
    customer_name: str | None = None
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    grace_until: datetime | None = None
    modules: tuple[str, ...] = ()
    unknown_modules: tuple[str, ...] = ()
    limits: dict[str, int | None] = field(default_factory=dict)
    unknown_limits: tuple[str, ...] = ()
    key_fingerprint: str | None = None
    days_left: int | None = None
    warnings: tuple[str, ...] = ()
    # Why verification failed, for the state log line only: fixed text or a
    # trust-dir file name, never file content or key material.
    detail: str | None = None
    # The class name of an unexpected error inside the evaluation (Amendment 3).
    error_type: str | None = None

    @property
    def restricted(self) -> bool:
        return self.state in RESTRICTED_STATES

    def cap_for(self, metric: str) -> int | None:
        """The licence cap of one metric; ``None`` adds no condition."""
        if self.state not in PROCESSING_STATES:
            return None
        return self.limits.get(metric)


NOT_ENFORCED = LicenseStatus(
    enforced=False, state="not_enforced", reason=None, processing_allowed=True
)


class _Refusal(Exception):
    """A verification refusal: the SPEC 2.3 state, reason and a log detail."""

    def __init__(self, state: str, reason: str, detail: str | None = None):
        self.state = state
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


def _malformed(detail: str) -> _Refusal:
    return _Refusal("invalid", "malformed", detail)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _value in pairs:
        if key in seen:
            raise _malformed("a JSON object repeats a key")
        seen.add(key)
    return dict(pairs)


# --- reading the files ----------------------------------------------------------------


def _stat_key(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size, stat.st_ino)


def _pem_paths(trust_dir: str) -> list[Path] | None:
    """The sorted ``*.pem`` entries of the trust dir; ``None`` when it cannot be listed."""
    if not trust_dir:
        return None
    try:
        return sorted(
            (entry for entry in Path(trust_dir).iterdir() if entry.name.endswith(".pem")),
            key=lambda entry: entry.name,
        )
    except OSError:
        return None


def _load_trust_keys(pem_paths: list[Path] | None) -> tuple[Ed25519PublicKey, ...]:
    if not pem_paths:
        raise _Refusal("invalid", "no_trusted_keys", "no *.pem file in the trust dir")
    keys: list[Ed25519PublicKey] = []
    for path in pem_paths:
        detail = f"the trust key file {path.name!r} is not an Ed25519 public key"
        try:
            key = serialization.load_pem_public_key(path.read_bytes())
        except Exception:
            raise _Refusal("invalid", "bad_trust_key", detail) from None
        if not isinstance(key, Ed25519PublicKey):
            raise _Refusal("invalid", "bad_trust_key", detail)
        keys.append(key)
    return tuple(keys)


def _read_bounded(path: Path, limit: int) -> bytes:
    """At most ``limit + 1`` bytes, so an oversized file is never read whole."""
    try:
        with path.open("rb") as handle:
            return handle.read(limit + 1)
    except OSError:
        raise _Refusal("missing", "unreadable") from None


def _fingerprint(key: Ed25519PublicKey) -> str:
    der = key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()[:16]


# --- parsing --------------------------------------------------------------------------


def _parse_moment(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not ZULU_PATTERN.fullmatch(value):
        raise _malformed(f"{name} is not YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime.strptime(value, ZULU_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        raise _malformed(f"{name} is not a real date") from None


def _is_count(value: Any) -> bool:
    return type(value) is int and value >= 0


def _parse(payload: bytes, fingerprint: str) -> VerifiedLicense:
    try:
        doc = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise _malformed("the licence is not UTF-8 JSON") from None
    if not isinstance(doc, dict) or set(doc) != LICENSE_FIELDS:
        raise _malformed("an unknown or missing top-level key")
    if doc["schema"] != LICENSE_SCHEMA:
        raise _malformed(f"the schema is not {LICENSE_SCHEMA}")
    for name in ("license_id", "customer_id"):
        if not isinstance(doc[name], str) or not ID_PATTERN.fullmatch(doc[name]):
            raise _malformed(f"{name} is malformed")
    customer_name = doc["customer_name"]
    if not isinstance(customer_name, str):
        raise _malformed("customer_name is not a string")
    customer_name = customer_name.strip()
    if not 1 <= len(customer_name) <= 200 or any(
        unicodedata.category(char) == "Cc" for char in customer_name
    ):
        raise _malformed("customer_name is malformed")
    issued_at = _parse_moment(doc["issued_at"], "issued_at")
    expires_at = _parse_moment(doc["expires_at"], "expires_at")
    if expires_at <= issued_at:
        raise _malformed("expires_at is not after issued_at")
    grace_days = doc["grace_days"]
    if not _is_count(grace_days) or grace_days > 90:
        raise _malformed("grace_days is not an integer from 0 to 90")
    try:
        not_before = issued_at - NOT_YET_VALID_SLACK
        grace_until = expires_at + timedelta(days=grace_days)
    except OverflowError:
        raise _malformed("issued_at or expires_at is at the edge of the date range") from None
    modules = doc["modules"]
    if (
        not isinstance(modules, list)
        or not all(isinstance(item, str) for item in modules)
        or len(set(modules)) != len(modules)
    ):
        raise _malformed("modules is not a list of unique strings")
    limits_doc = doc["limits"]
    if not isinstance(limits_doc, dict):
        raise _malformed("limits is not an object")
    if not all(value is None or _is_count(value) for value in limits_doc.values()):
        raise _malformed("a limit is not null or an integer >= 0")
    # Imported here: tenancy_service pulls in the ORM, and this module is
    # imported by entitlement_service (which tenancy's imports may reach).
    from backend.services.tenancy_service import METERED_METRICS

    return VerifiedLicense(
        license_id=doc["license_id"],
        customer_id=doc["customer_id"],
        customer_name=customer_name,
        issued_at=issued_at,
        expires_at=expires_at,
        grace_days=grace_days,
        not_before=not_before,
        grace_until=grace_until,
        modules=tuple(sorted(key for key in modules if key in REGISTRY_KEYS)),
        unknown_modules=tuple(sorted(key for key in modules if key not in REGISTRY_KEYS)),
        limits={metric: value for metric, value in limits_doc.items() if metric in METERED_METRICS},
        unknown_limits=tuple(sorted(key for key in limits_doc if key not in METERED_METRICS)),
        key_fingerprint=fingerprint,
    )


def _verify(license_file: Path, pem_paths: list[Path] | None) -> VerifiedLicense:
    """SPEC 2.3 in order: missing, trust keys, size, signature, then the JSON."""
    signature_file = Path(f"{license_file}.sig")
    if _stat_key(license_file) is None or _stat_key(signature_file) is None:
        raise _Refusal("missing", "file_missing")
    trust_keys = _load_trust_keys(pem_paths)
    payload = _read_bounded(license_file, LICENSE_MAX_BYTES)
    if len(payload) > LICENSE_MAX_BYTES:
        raise _Refusal("invalid", "too_large", f"the licence is over {LICENSE_MAX_BYTES} bytes")
    signature = _read_bounded(signature_file, SIGNATURE_BYTES)
    if len(signature) != SIGNATURE_BYTES:
        raise _Refusal("invalid", "bad_signature", "the signature is not 64 bytes")
    for key in trust_keys:
        try:
            key.verify(signature, payload)
        except InvalidSignature:
            continue
        return _parse(payload, _fingerprint(key))
    raise _Refusal("invalid", "bad_signature", "no trusted key verifies the licence")


# --- the per-process cache -----------------------------------------------------------

_CACHE_LOCK = threading.Lock()
_CACHE: tuple[tuple[Any, ...], VerifiedLicense | _Refusal] | None = None
_LOG_LOCK = threading.Lock()
_LAST_LOGGED: tuple[str, str | None, str | None] | None = None
_ERROR_LOGGED = False


def clear_license_cache() -> None:
    """Tests swap licence files inside one process; production never needs this."""
    global _CACHE, _LAST_LOGGED, _ERROR_LOGGED
    with _CACHE_LOCK:
        _CACHE = None
    with _LOG_LOCK:
        _LAST_LOGGED = None
        _ERROR_LOGGED = False


def _verified_license(settings_like: Any) -> VerifiedLicense | _Refusal:
    """The verification outcome, from the cache while no input file changed.

    A refusal is returned, not raised: re-raising one cached exception object
    would grow its traceback on every request."""
    global _CACHE
    license_file = Path(settings_like.offline_license_file)
    trust_dir = settings_like.offline_license_trust_dir or ""
    pem_paths = _pem_paths(trust_dir)
    key = (
        str(license_file),
        _stat_key(license_file),
        _stat_key(Path(f"{license_file}.sig")),
        trust_dir,
        None if pem_paths is None else tuple((p.name, _stat_key(p)) for p in pem_paths),
    )
    with _CACHE_LOCK:
        cached = _CACHE
    outcome: VerifiedLicense | _Refusal
    if cached is not None and cached[0] == key:
        outcome = cached[1]
    else:
        try:
            outcome = _verify(license_file, pem_paths)
        except _Refusal as refusal:
            refusal.__traceback__ = None
            outcome = refusal
        # A missing or unreadable file is not cached: a fixed file mode
        # changes no field of the cache key.
        if not (isinstance(outcome, _Refusal) and outcome.state == "missing"):
            with _CACHE_LOCK:
                _CACHE = (key, outcome)
    return outcome


# --- the decision ---------------------------------------------------------------------


def _whole_days(until: datetime, moment: datetime) -> int:
    return max(0, (until - moment).days)


def evaluate(settings_like: Any, now: datetime | None = None) -> LicenseStatus:
    """The licence state at ``now`` (UTC wall clock by default). No logging.

    Never raises (Amendment 3): an unexpected error is ``invalid``/``malformed``,
    so the module guard keeps reads working and processing stays refused.
    """
    try:
        if not getattr(settings_like, "offline_license_file", ""):
            return NOT_ENFORCED
        return _decide(settings_like, now or datetime.now(UTC))
    except Exception as exc:
        return LicenseStatus(
            enforced=True,
            state="invalid",
            reason="malformed",
            processing_allowed=False,
            warnings=("license_invalid",),
            error_type=type(exc).__name__,
        )


def _decide(settings_like: Any, moment: datetime) -> LicenseStatus:
    verified = _verified_license(settings_like)
    if isinstance(verified, _Refusal):
        return LicenseStatus(
            enforced=True,
            state=verified.state,
            reason=verified.reason,
            processing_allowed=False,
            warnings=(f"license_{verified.state}",),
            detail=verified.detail,
        )
    if moment < verified.not_before:
        return LicenseStatus(
            enforced=True,
            state="invalid",
            reason="not_yet_valid",
            processing_allowed=False,
            warnings=("license_invalid",),
            detail="the host clock is before issued_at minus one day",
        )
    grace_until = verified.grace_until
    if moment < verified.expires_at:
        state = "valid"
        days_left = _whole_days(verified.expires_at, moment)
        warnings = ("license_expires_soon",) if days_left <= EXPIRES_SOON_DAYS else ()
    elif moment < grace_until:
        state = "grace"
        days_left = _whole_days(grace_until, moment)
        warnings = ("license_in_grace",)
    else:
        state = "expired"
        days_left = 0
        warnings = ("license_expired",)
    return LicenseStatus(
        enforced=True,
        state=state,
        reason=None,
        processing_allowed=state in PROCESSING_STATES,
        license_id=verified.license_id,
        customer_id=verified.customer_id,
        customer_name=verified.customer_name,
        issued_at=verified.issued_at,
        expires_at=verified.expires_at,
        grace_until=grace_until,
        modules=verified.modules,
        unknown_modules=verified.unknown_modules,
        limits=dict(verified.limits),
        unknown_limits=verified.unknown_limits,
        key_fingerprint=verified.key_fingerprint,
        days_left=days_left,
        warnings=warnings,
    )


def _log_state(status: LicenseStatus) -> None:
    global _LAST_LOGGED
    seen = (status.state, status.reason, status.license_id)
    with _LOG_LOCK:
        if _LAST_LOGGED == seen:
            return
        _LAST_LOGGED = seen
    logger.warning(
        "OFFLINE_LICENSE_STATE state=%s reason=%s license_id=%s%s",
        status.state,
        status.reason or "-",
        status.license_id or "-",
        f" detail={status.detail!r}" if status.detail else "",
    )


def _log_error_once(error_type: str) -> None:
    global _ERROR_LOGGED
    with _LOG_LOCK:
        if _ERROR_LOGGED:
            return
        _ERROR_LOGGED = True
    logger.warning("OFFLINE_LICENSE_EVALUATION_FAILED error_type=%s", error_type)


def current_license_status(settings_like: Any, now: datetime | None = None) -> LicenseStatus:
    """The runtime entry point: ``evaluate`` plus one log line per state change,
    and one line per process for an unexpected evaluation error (class name only)."""
    status = evaluate(settings_like, now)
    if status.error_type is not None:
        _log_error_once(status.error_type)
    if status.enforced:
        _log_state(status)
    return status


# --- the operator views -------------------------------------------------------------


def format_zulu(moment: datetime | None) -> str | None:
    """``YYYY-MM-DDTHH:MM:SSZ``; ``isoformat`` pads a year below 1000, ``%Y`` may not."""
    if moment is None:
        return None
    return moment.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def status_line(status: LicenseStatus) -> str:
    """The one-line operator view of SPEC 2.9; never the customer name."""
    if not status.enforced:
        return "license: state=not_enforced"
    limits = ",".join(
        f"{metric}={'unlimited' if value is None else value}"
        for metric, value in sorted(status.limits.items())
    )
    return (
        f"license: state={status.state}"
        f" reason={status.reason or '-'}"
        f" id={status.license_id or '-'}"
        f" customer={status.customer_id or '-'}"
        f" expires={format_zulu(status.expires_at) or '-'}"
        f" grace_until={format_zulu(status.grace_until) or '-'}"
        f" days_left={'-' if status.days_left is None else status.days_left}"
        f" modules={','.join(status.modules) or '-'}"
        f" limits={limits or '-'}"
        f" key={status.key_fingerprint or '-'}"
    )
