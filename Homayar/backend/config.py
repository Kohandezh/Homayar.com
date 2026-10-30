from __future__ import annotations

import ipaddress
import logging
import os
import re
import stat
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import Field, PlainSerializer, SerializationInfo, ValidationError, model_validator
from pydantic.fields import FieldInfo
from pydantic_core import PydanticCustomError
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    SettingsError,
)

if TYPE_CHECKING:
    from backend.speech.chunking import ChunkSettings

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Secret-bearing settings (HOM-32). Each can instead be read from the file named by
# `<NAME>_FILE` (secret-manager mounts, Docker/Kubernetes secrets), and none of them
# appears in `repr(settings)`, `str(settings)` or a settings validation error.
SECRET_FIELDS: tuple[str, ...] = (
    "database_url",
    "redis_url",
    "jwt_secret",
    "token_pepper",
    "google_client_secret",
    "asanak_api_key",
    "storage_access_key",
    "storage_secret_key",
    "pii_mapping_keys",
    "llm_fallback_api_key",
    "zai_api_key",
    "gapgpt_api_key",
    "smtp_password",
    "neo4j_password",
    "sentry_dsn",
    "metrics_bearer_token",
)

# Serialized form of a non-empty secret setting (HOM-160).
SECRET_REDACTED = "**********"  # noqa: S105 - redaction marker, not a credential
# model_dump/model_dump_json context key that returns secret settings in clear, for code
# that must rebuild Settings from a dump (tests); nothing else should pass it.
REVEAL_SECRETS_CONTEXT = "reveal_secrets"


def _serialize_secret(value: str, info: SerializationInfo) -> str:
    context = info.context or {}
    if context.get(REVEAL_SECRETS_CONTEXT) is True or not value:
        return value
    return SECRET_REDACTED


# A secret-bearing setting: a plain `str` for every consumer, left out of repr/str, and
# redacted by model_dump()/model_dump_json() unless REVEAL_SECRETS_CONTEXT is passed.
SecretSetting = Annotated[
    str,
    Field(repr=False),
    PlainSerializer(_serialize_secret, return_type=str, when_used="always"),
]

# O_NONBLOCK: a path swapped for a FIFO after the pre-open check cannot block the open.
_SECRET_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
# Secrets are short: a larger file is refused after reading at most one byte more than this.
SECRET_FILE_MAX_BYTES = 64 * 1024
# Kernel file systems expose process environments, memory and devices, never secret files.
_KERNEL_FILE_SYSTEMS = (Path("/proc"), Path("/sys"))
_REDACTED = "[redacted]"


class SecretReferenceError(SettingsError):
    """A `<NAME>_FILE` reference is ambiguous, empty or unreadable.

    The message names variables and paths only, never a value.
    """


def _pre_open_problem(path: str) -> str | None:
    """Why `path` must not be opened: a kernel file system, missing, or not a regular file."""
    try:
        resolved = Path(os.path.realpath(path))
        if any(resolved == root or root in resolved.parents for root in _KERNEL_FILE_SYSTEMS):
            return "resolves under /proc or /sys (kernel files are refused)"
        mode = os.stat(path).st_mode
    except FileNotFoundError:
        return "does not exist"
    except PermissionError:
        return "is not readable (permission denied)"
    except OSError as exc:
        return f"cannot be inspected ({exc.strerror or type(exc).__name__})"
    except ValueError:  # e.g. an embedded NUL character
        return "is not a valid path"
    return None if stat.S_ISREG(mode) else "is not a regular file"


def _read_at_most(descriptor: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        chunk = os.read(descriptor, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_secret_file(variable: str, path: str) -> str:
    """Return the UTF-8 text of the regular file at `path` minus one trailing newline.

    Exactly one trailing `\\n` or `\\r\\n` is removed; a lone `\\r`, other whitespace and a
    byte-order mark are content. A path under /proc or /sys (after symlinks) or one that is
    not a regular file is refused before it is opened (and re-checked on the open file), and
    a file larger than SECRET_FILE_MAX_BYTES is refused after a bounded read. A failure
    raises SecretReferenceError naming `variable` and `path`. It is raised outside every
    `except` block, so no chained exception is attached (a UnicodeDecodeError, for one,
    carries the file's bytes).
    """
    problem = _pre_open_problem(path)
    data = b""
    if problem is None:
        try:
            descriptor = os.open(path, _SECRET_FILE_FLAGS)
        except FileNotFoundError:
            problem = "does not exist"
        except PermissionError:
            problem = "is not readable (permission denied)"
        except OSError as exc:
            problem = f"cannot be opened ({exc.strerror or type(exc).__name__})"
        else:
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    problem = "is not a regular file"
                else:
                    data = _read_at_most(descriptor, SECRET_FILE_MAX_BYTES + 1)
                    if len(data) > SECRET_FILE_MAX_BYTES:
                        problem = f"is larger than 64 KiB ({SECRET_FILE_MAX_BYTES} bytes)"
                        data = b""
            except OSError as exc:
                problem = f"cannot be read ({exc.strerror or type(exc).__name__})"
            finally:
                os.close(descriptor)
    text: str | None = None
    if problem is None:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            problem = "is not valid UTF-8 text"
    if problem is not None or text is None:
        raise SecretReferenceError(f"{variable} points to {path!r}, which {problem}")
    if text.endswith("\r\n"):
        return text[:-2]
    if text.endswith("\n"):
        return text[:-1]
    return text


class _SecretFileReferences(PydanticBaseSettingsSource):
    """Values of the `<NAME>_FILE` references of the secret fields found in one place.

    `variables` is the environment or .env source whose variables are searched, so a
    reference follows the same letter-case and prefix rules as the plain `<NAME>`. `environ`
    (process environment only) is scanned for a reference spelled in more than one case.
    A value and a reference for the same field in the same place are rejected, never ranked.
    """

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        variables: EnvSettingsSource,
        where: str,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(settings_cls)
        self._variables = variables
        self._where = where
        self._environ = environ

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False  # __call__ resolves every reference at once

    def __call__(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field_name in SECRET_FIELDS:
            value = self._resolve(field_name)
            if value is not None:
                values[field_name] = value
        return values

    def _key(self, name: str) -> str:
        return name if self._variables.case_sensitive else name.lower()

    def _spellings(self, key: str) -> list[str]:
        return sorted(name for name in self._environ or () if self._key(name) == key)

    def _describe(self, key: str, canonical: str) -> str:
        """The canonical variable name, plus the spellings used when they differ from it."""
        others = [name for name in self._spellings(key) if name != canonical]
        return f"{canonical} (set as {', '.join(others)})" if others else canonical

    def _resolve(self, field_name: str) -> str | None:
        plain = f"{self._variables.env_prefix}{field_name}"
        plain_key, reference_key = self._key(plain), self._key(f"{plain}_file")
        variables = self._variables.env_vars
        path = variables.get(reference_key)
        if path is None:
            return None
        reference = self._describe(reference_key, f"{plain.upper()}_FILE")
        if len(self._spellings(reference_key)) > 1:
            raise SecretReferenceError(
                f"{reference} is set more than once in the {self._where} with different "
                "letter case; set it once"
            )
        if variables.get(plain_key) is not None:
            raise SecretReferenceError(
                f"{self._describe(plain_key, plain.upper())} and {reference} are both set in "
                f"the {self._where}; set only one of them"
            )
        if not path:
            raise SecretReferenceError(
                f"{reference} is set but empty in the {self._where}; point it to a secret "
                "file or unset it"
            )
        return read_secret_file(reference, path)


def _redacted_validation_error(error: ValidationError) -> ValidationError:
    """The same error types, locations and messages without the offending input values."""
    return ValidationError.from_exception_data(
        error.title,
        [
            {
                "type": PydanticCustomError(line["type"], line["msg"]),
                "loc": line["loc"],
                "input": _REDACTED,
            }
            for line in error.errors(include_url=False, include_context=False, include_input=False)
        ],
        hide_input=True,
    )


def _is_https_url(url: str) -> bool:
    """True for an absolute `https://host...` URL (the scheme in any letter case)."""
    if url[:8].lower() != "https://":
        return False
    try:
        return bool(urlsplit(url).hostname)
    except ValueError:  # e.g. an unterminated IPv6 literal
        return False


# Internal-hop transport policy (HOM-161, docs/security/transport-policy.md).
_POSTGRES_TLS_MODES = frozenset({"require", "verify-ca", "verify-full"})
# A Compose service name: one DNS label starting with a letter, so that an all-digit or
# hexadecimal label (which resolvers read as an IPv4 address) never counts as one.
_SERVICE_NAME = re.compile(r"[A-Za-z](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?")


def _is_same_host(host: str) -> bool:
    """True for a server reached without crossing to another machine (HOM-161).

    That is a loopback name or address, a unix socket path, or a single-label name such as
    a Compose service (``postgres``, ``redis``, ``minio``, ``neo4j``) on this host's internal
    network. A dotted name or any other address is treated as another machine.
    """
    if host.startswith("/"):
        return True
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return bool(_SERVICE_NAME.fullmatch(host))


def _query_hosts(values: list[str]) -> list[str]:
    """Host names from libpq ``host``/``hostaddr`` query values (comma lists, ``host:port``)."""
    hosts = []
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if item.startswith("["):
                item = item[1 : item.find("]")] if "]" in item else item
            elif not item.startswith("/") and item.count(":") == 1:
                item = item.split(":", 1)[0]
            hosts.append(item)
    return hosts


# A hop's transport: "tls", "plaintext" (with the hosts it reaches) or "invalid".
_Transport = tuple[Literal["tls", "plaintext", "invalid"], list[str]]


def _postgres_transport(url: str) -> _Transport:
    """TLS when every ``sslmode`` is require/verify-ca/verify-full (and at least one is set).

    Parsed with SQLAlchemy's ``make_url``, the parser the engine uses, so this rule and the
    connection never read the URL differently (``urlsplit`` disagrees on ``#`` and ``@``).
    """
    from sqlalchemy.engine import make_url

    try:
        parsed = make_url(url)
    except Exception:  # any parse failure; its message may quote part of the URL
        return "invalid", []
    if parsed.database and ("=" in parsed.database or "://" in parsed.database):
        # libpq can expand a database name that is itself a connection string.
        return "invalid", []
    query = {
        key: (value,) if isinstance(value, str) else tuple(value)
        for key, value in parsed.query.items()
    }
    modes = query.get("sslmode", ())
    if modes and all(mode in _POSTGRES_TLS_MODES for mode in modes):
        return "tls", []
    hosts = [parsed.host] if parsed.host else []
    hosts += _query_hosts([*query.get("host", ()), *query.get("hostaddr", ())])
    if "service" in query:
        hosts.append("")  # pg_service.conf names the server: it cannot be checked here
    return "plaintext", hosts


def _redis_transport(url: str) -> _Transport:
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        return "invalid", []
    scheme = parts.scheme.lower()
    if scheme == "rediss":
        return "tls", []
    if scheme == "redis":
        # Celery's Redis result backend lets query parameters override the URL's host.
        overrides = [
            value
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key in ("host", "hostname")
        ]
        return "plaintext", ([hostname] if hostname else []) + overrides
    if scheme == "unix" and parts.path:
        return "plaintext", [parts.path]
    return "invalid", []


def _neo4j_transport(uri: str) -> _Transport:
    """``bolt+s``/``neo4j+s`` verify the server certificate; ``+ssc`` does not and is refused."""
    try:
        parts = urlsplit(uri)
        hostname = parts.hostname
    except ValueError:
        return "invalid", []
    scheme = parts.scheme.lower()
    if scheme in ("bolt+s", "neo4j+s"):
        return "tls", []
    if scheme in ("bolt", "neo4j"):
        return "plaintext", [hostname] if hostname else []
    return "invalid", []


def _storage_transport(endpoint: str) -> _Transport:
    if not endpoint or _is_https_url(endpoint):
        return "tls", []
    if endpoint[:7].lower() != "http://":
        return "invalid", []
    try:
        hostname = urlsplit(endpoint).hostname
    except ValueError:
        return "invalid", []
    return "plaintext", [hostname] if hostname else []


def _ambient_s3_endpoints() -> list[tuple[str, str]]:
    """Where botocore would take an S3 endpoint from outside Homayar's settings.

    Returns (source name, value) pairs: the ``AWS_ENDPOINT_URL_S3`` and
    ``AWS_ENDPOINT_URL`` variables and the active shared-config profile's
    ``endpoint_url`` (directly or in its ``services`` section). The S3 client
    ignores all of them (``ignore_configured_endpoint_urls``); production still
    refuses a non-https one so a deployment never relies on that alone. A
    shared config that cannot be read is reported with an empty value.
    """
    found = [
        (name, os.environ[name])
        for name in ("AWS_ENDPOINT_URL_S3", "AWS_ENDPOINT_URL")
        if os.environ.get(name)
    ]
    try:
        import botocore.session

        session = botocore.session.Session()
        profile = session.get_scoped_config()
        services = session.full_config.get("services", {})
    except Exception:  # unreadable file or unknown profile: botocore would fail too
        return [*found, ("the shared AWS config (endpoint_url unreadable)", "")]
    if profile.get("endpoint_url"):
        found.append(("endpoint_url in the shared AWS config profile", profile["endpoint_url"]))
    service = services.get(profile.get("services", ""), {}).get("s3", {})
    if isinstance(service, dict) and service.get("endpoint_url"):
        found.append(
            ("s3 endpoint_url in the shared AWS config services section", service["endpoint_url"])
        )
    return found


class Settings(BaseSettings):
    """Validated runtime configuration for cloud and on-premise deployments."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    # APP
    environment: Literal["development", "test", "staging", "production"] = "development"
    deployment_mode: Literal["cloud", "onprem"] = "cloud"
    app_name: str = "Homayar"
    api_prefix: str = "/api/v1"
    frontend_url: str = "http://localhost:5173/app"
    public_url: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    log_level: str = "INFO"
    auto_create_schema: bool = False

    # DATABASE / REDIS
    database_url: SecretSetting = "sqlite:///./homayar-dev.db"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    # PostgreSQL role switched to with SET ROLE on every pooled connection
    # checkout (WP03-T05). Leave empty when DATABASE_URL already logs in as a
    # non-superuser member of homayar_app; set to "homayar_app" when the login
    # role is the migration owner (single-role compose/dev/CI databases).
    database_app_role: str = ""
    # Production requires TLS on every internal hop (HOM-161, docs/security/transport-policy.md).
    # On-premise only, and only for a server on the same host (loopback, a unix socket or a
    # single-label Compose service name), these per-hop opt-outs allow plaintext; every one
    # in use is logged at startup by hop name. Cloud production cannot opt out.
    database_allow_insecure_transport: bool = False
    redis_url: SecretSetting = "redis://localhost:6379/0"
    redis_allow_insecure_transport: bool = False

    # AUTH / SECURITY
    jwt_secret: SecretSetting = ""
    token_pepper: SecretSetting = ""
    jwt_issuer: str = "homayar"
    jwt_audience: str = "homayar-api"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    secure_cookies: bool = False
    cookie_domain: str | None = None
    trusted_hosts: str = "localhost,127.0.0.1,testserver"
    csrf_header_name: str = "X-CSRF-Token"
    rate_limit_fail_open: bool = False

    # AUTH FEATURES
    enable_google_auth: bool = False
    google_client_id: str = ""
    google_client_secret: SecretSetting = ""
    google_redirect_uri: str = "http://localhost:8001/api/v1/auth/google/callback"
    enable_phone_auth: bool = False
    asanak_base_url: str = "https://panel.asanak.com/webservice/v1rest/sendsms"
    asanak_api_key: SecretSetting = ""
    asanak_sender: str = ""
    otp_ttl_seconds: int = 180
    otp_resend_cooldown_seconds: int = 60
    otp_max_attempts: int = 5
    enable_demo_account: bool = False
    enable_free_demo: bool = True
    demo_quota_seconds: int = 1800

    # STORAGE / UPLOAD
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_root: str = str(PROJECT_ROOT / "data" / "objects")
    storage_bucket: str = "homayar-private"
    storage_endpoint_url: str = ""
    # On-premise only: production may reach an http:// endpoint on the same host (the
    # bundled MinIO on the internal compose network, HOM-32/HOM-161). Cloud cannot opt out.
    storage_allow_insecure_endpoint: bool = False
    storage_region: str = "us-east-1"
    storage_access_key: SecretSetting = ""
    storage_secret_key: SecretSetting = ""
    storage_presign_ttl_seconds: int = 300
    # API keys (WP03-T07, ADR 0013): per-key request rate, live keys per
    # workspace and the longest expiry an owner/admin may request.
    api_key_rate_limit_per_hour: int = 600
    api_key_max_active_per_workspace: int = 20
    api_key_max_ttl_days: int = 365
    upload_dir: str = str(PROJECT_ROOT / "data" / "legacy-uploads")
    upload_quarantine_dir: str = str(PROJECT_ROOT / "data" / "quarantine")
    max_upload_size_mb: int = 250
    max_audio_duration_seconds: int = 14_400
    allowed_extensions: str = ".wav,.mp3,.ogg,.m4a,.aac,.flac,.mp4,.webm"
    ffprobe_path: str = "ffprobe"
    ffprobe_timeout_seconds: float = 10.0
    ffmpeg_path: str = "ffmpeg"
    ffmpeg_timeout_seconds: float = 20.0
    # Seconds of audio the bounded ffmpeg decode verifies on upload (0 disables).
    media_decode_probe_seconds: float = 30.0
    # Compressed audio claiming fewer bytes per second is refused as implausible.
    media_min_bytes_per_second: int = 64
    # Per-channel signal analysis on ingest (HOM-37, backend.media.channels): a
    # signal-level heuristic over the first N seconds, never a refusal.
    # Seconds analysed (0 disables) and the wall-time bound of its decode.
    media_channel_analysis_seconds: float = 30.0
    media_channel_analysis_timeout_seconds: float = 10.0
    # A 20 ms frame at or above this level counts as active signal.
    media_channel_active_threshold_dbfs: float = -50.0
    # A channel whose active share is at most this is silent.
    media_channel_silent_max_active_share: float = 0.01
    # Two non-silent channels with |correlation| at least this are duplicates.
    media_channel_duplicate_min_correlation: float = 0.99
    # ...at some lag within +- this many milliseconds (0: aligned samples only).
    media_channel_duplicate_max_lag_ms: float = 5.0
    # Channel-preserving derivative and signal activity in the job (HOM-38,
    # backend.media.derivatives): W1 and W2 (ADR 0042) are fixed by HOM-313 and
    # HOM-314; off by default until the owner turns it on. The wall-time bound
    # of its ffmpeg decode, and the size cap of one derivative in MiB (owner
    # ruling R3; the storage cap max_upload_size_mb still applies).
    media_derivatives_enabled: bool = False
    media_derivative_timeout_seconds: float = 300.0
    media_derivative_max_size_mb: int = 200
    upload_rate_limit_per_hour: int = 30
    retention_days_default: int = 90

    # JOBS / PROVIDERS
    tasks_eager: bool = False
    worker_concurrency_per_workspace: int = 2
    job_retry_delay_seconds: int = 30
    # Retry budget and deadlines (WP06-T05, backend.jobs.failures): attempts
    # per replay window, exponential back-off between attempts (base doubling
    # up to the maximum), one overall deadline per job measured from its
    # creation (or replay), and a bounded number of operator replays.
    job_max_attempts: int = 3
    job_retry_max_delay_seconds: int = 600
    job_deadline_seconds: int = 86_400
    job_max_replays: int = 3
    # Usage ledger reconciliation (WP06-T06, backend.jobs.usage_reconciliation):
    # periodic sweep that releases orphaned reservations (older than the grace
    # period, no live job, or a deleted meeting) and compares entitlement
    # counters with the reservation rows. Counter drift is reported and
    # audited; it is corrected only when repair is enabled explicitly.
    usage_reconciliation_interval_seconds: int = 900
    usage_reconciliation_grace_seconds: int = 600
    usage_reconciliation_batch_size: int = 200
    usage_reconciliation_repair_counters: bool = False
    # Stage checkpoints and attempt-scoped artifacts (WP06-T03,
    # backend.jobs.checkpoints): reuse of committed stage results on retry,
    # the artifact size bound, and the cleanup of abandoned artifacts (objects
    # of never-published or invalidated checkpoints older than the grace
    # period; published artifacts of live meetings are never touched).
    job_checkpoint_reuse_enabled: bool = True
    job_artifact_max_bytes: int = 16 * 1024 * 1024
    job_artifact_grace_seconds: int = 3600
    job_artifact_sweep_interval_seconds: int = 900
    job_artifact_sweep_batch_size: int = 200
    # Media reconciliation (WP05-T04, backend.jobs.media_reconciliation):
    # periodic sweep that retries pending deletions, reports or removes
    # orphan objects (objects under a tenant prefix without a live row)
    # after the grace period, and flags live rows whose bytes are missing.
    # Orphan removal is report-only by default; enable it explicitly.
    media_reconciliation_interval_seconds: int = 900
    media_reconciliation_grace_seconds: int = 3600
    media_reconciliation_batch_size: int = 200
    media_reconciliation_remove_orphans: bool = False
    # Retention purge (WP07-T03, backend.jobs.retention_purge, ADR 0021):
    # tombstones expired conversations of FINITE/IMMEDIATE workspaces and
    # drives every tombstone's durable purge. A failed attempt is retried
    # after base * 2^(attempt-1) seconds (capped at the max delay); after
    # max_attempts the purge is FAILED and alerts until an operator requeues
    # it. A legal hold defers the purge without spending an attempt.
    retention_purge_interval_seconds: int = 900
    retention_purge_batch_size: int = 100
    retention_purge_max_attempts: int = 8
    retention_purge_retry_base_seconds: int = 300
    retention_purge_retry_max_seconds: int = 21600
    retention_purge_hold_recheck_seconds: int = 3600
    # Tombstone journal (WP07-T04, ADR 0022): append-only JSON lines of every
    # tombstone (ids and opaque object keys, no content) kept OUTSIDE the
    # database and object-store backups, so a restore can reapply deletions
    # made after the backup. Empty disables journaling (development only;
    # deployments mount a separately replicated volume here).
    tombstone_journal_dir: str = ""
    # Restricted PII mappings (WP07-T05, ADR 0023): "key_id:base64(32 bytes)"
    # entries, comma separated, from the secret manager; the active id
    # encrypts new mappings, the others still decrypt older ones (rotation).
    # Empty: redaction still works, but originals are not retained.
    pii_mapping_keys: SecretSetting = ""
    pii_mapping_active_key_id: str = ""
    # Execution leases (WP06-T04): one policy object (backend.jobs.leases)
    # derives every timing decision from these; no other module keeps its own.
    job_lease_seconds: int = 120  # a RUNNING attempt is abandoned when not renewed
    job_lease_renew_interval_seconds: int = 30  # heartbeat period (< lease / 2)
    job_stage_timeout_seconds: int = 1800  # one provider call (materialize/ASR/...)
    job_attempt_timeout_seconds: int = 7200  # whole attempt; renewals stop after it
    job_recovery_sweep_interval_seconds: int = 60  # expired-lease sweeper period
    job_recovery_batch_size: int = 100
    # Transactional outbox (WP06-T02): publication retries and the sweeper.
    dispatch_max_attempts: int = 8
    dispatch_retry_base_seconds: int = 5
    dispatch_retry_max_seconds: int = 600
    dispatch_sweep_interval_seconds: int = 30
    dispatch_sweep_batch_size: int = 100
    # Analysis recompute after a transcript correction (HOM-63, ADR 0037,
    # backend.jobs.recompute): a correction dispatches a recompute of the
    # intelligence outputs on the corrected revision; the sweeper dispatches
    # stale meetings again until a revision has this many failed recomputes.
    # After that only a person can ask for another attempt.  Off turns off
    # every automatic recompute (the correction and the sweeper); only the
    # manual route recomputes then.
    recompute_on_correction: bool = True
    recompute_max_attempts: int = 3
    recompute_sweep_interval_seconds: int = 300
    # Manual recompute requests per workspace and minute (429 over it).
    recompute_manual_rate_limit_per_minute: int = 10
    # HOM-312: the trend judgment cache of each API worker (ADR 0043).  At most this many
    # entries of both kinds together (judgments of analysis runs and coverage verdicts of
    # meetings: about 2 per conversation; measured about 1,444 bytes per judgment and 270 per
    # verdict, so 100,000 hold about 50,000 conversations, about 86 MB; the 4-week window at
    # 100k conversations is about 66,000).  Larger servers raise it.  Requests evict the least
    # recently used first.  0 keeps nothing (every trend request judges again).
    trend_judgment_cache_max_entries: int = 100_000
    # Warm that cache in a background thread at startup (most recently active workspaces
    # first, only in free room: it never evicts and stops when the cache is full; it yields to
    # requests).  Off: the first requests are cold.
    trend_judgment_warmup_enabled: bool = True
    # A cached coverage verdict (unsupported findings of a meeting) older than this many
    # seconds is judged again: the backstop for a change made outside the application.
    # 0 reuses none.
    trend_coverage_verdict_ttl_seconds: int = 600
    allow_mock_providers: bool = False
    asr_provider: str = "local_whisper"
    diarization_provider: str = "unavailable"
    intelligence_provider: str = "openai_compatible"
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    local_ai_base_url: str = ""
    # Offline weights (P0 wave): refuse runtime downloads and load a pinned
    # local model directory whose artifacts the manifest declares.
    whisper_local_files_only: bool = False
    whisper_model_path: str = ""
    # Named Whisper profile (HOM-39, backend/providers/asr_profiles.py). Empty
    # keeps the whisper_model/device/compute_type values above. A profile sets
    # all three and ignores them; local_files_only and model_path still apply.
    asr_profile: str = ""
    # Language policy for auto requests (backend/speech/language_policy.py):
    # a detected language outside this list, or under the threshold, falls back
    # to the default language. Codes are normalized ("fa-IR" becomes "fa").
    asr_allowed_languages: str = "fa,en"
    asr_default_language: str = "fa"
    asr_language_min_probability: float = 0.5
    # Long-call chunking (HOM-40, backend/speech/chunking.py, ADR 0046): a call
    # longer than asr_chunk_min_call_seconds is transcribed in chunks of about
    # asr_chunk_target_seconds, cut in a quiet gap within +- search seconds, with
    # overlap seconds of extra audio past each cut. Off: every call is one request.
    asr_chunking_enabled: bool = True
    asr_chunk_min_call_seconds: float = 1200.0
    asr_chunk_target_seconds: float = 600.0
    asr_chunk_search_seconds: float = 30.0
    asr_chunk_overlap_seconds: float = 2.0
    model_artifact_root: str = ""
    # Speech synthesis (P0 wave): "unavailable" (default), "piper" (local
    # CLI) or "local_http" (private /v1/audio/speech).  tts_voices maps
    # voice ids to model files: "id=path,id2=path2".
    tts_provider: str = "unavailable"
    tts_piper_binary: str = "piper"
    tts_voices: str = ""
    tts_default_voice: str = ""
    tts_base_url: str = ""
    tts_model: str = ""
    # Voice runtime capacity admission (P0 wave, X21).
    voice_deployment_profile: str = "default"
    voice_global_call_limit: int = 200
    voice_default_workspace_call_limit: int = 200
    voice_node_heartbeat_ttl_seconds: int = 15
    voice_slot_lease_seconds: int = 30
    # Model registry (WP08-T01, ADR 0026): operator manifest files (comma
    # separated, in addition to the builtin ones), per-stage pins
    # "stage=manifest_id@version" (restore a prior approved version) and
    # strictness (unset = strict outside development/test; production
    # refuses to switch it off).
    model_manifest_paths: str = ""
    model_manifest_pins: str = ""
    model_manifest_strict: bool | None = None
    # Provider resilience (WP08-T05, ADR 0030): positive jitter on the retry
    # back-off; a networked endpoint's circuit opens after N consecutive
    # transient failures for the cool-down; optional request limits per
    # minute keyed by manifest id ("manifest_id=60,other=30").
    job_retry_jitter_ratio: float = 0.1
    provider_circuit_failure_threshold: int = 5
    provider_circuit_cooldown_seconds: int = 60
    provider_rate_limits: str = ""
    # Same-policy fallback analysis endpoint (OpenAI-compatible); used only
    # when the workspace policy approves fallback and the endpoint passes the
    # same eligibility filter as the primary.
    llm_fallback_base_url: str = ""
    llm_fallback_model: str = ""
    llm_fallback_api_key: SecretSetting = ""

    # Legacy LLM compatibility
    zai_api_key: SecretSetting = ""
    zai_base_url: str = "https://api.z.ai/v1"
    gapgpt_api_key: SecretSetting = ""
    gapgpt_base_url: str = "https://gapgpt.app/api/v1"
    llm_model: str = "default"
    llm_provider: str = "auto"

    # EMAIL
    smtp_host: str = ""
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: SecretSetting = ""
    smtp_from: str = "noreply@homayar.com"
    smtp_use_tls: bool = False

    # OPTIONAL ENTERPRISE / LEGACY INTEGRATIONS
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_allow_insecure_transport: bool = False
    neo4j_user: str = "neo4j"
    neo4j_password: SecretSetting = ""
    neo4j_database: str = "neo4j"
    enable_neo4j_projection: bool = False

    # OBSERVABILITY / BILLING
    sentry_dsn: SecretSetting = ""
    # Prometheus scrape endpoint GET /metrics on the API port (HOM-113, ADR 0036); the
    # proxy never routes it. In staging and production it answers only with
    # METRICS_BEARER_TOKEN (at least 32 characters) as a bearer token, or without one
    # when METRICS_ALLOW_UNAUTHENTICATED=true is set explicitly (a private scrape
    # network); otherwise it answers 404 and startup logs METRICS_ENDPOINT_DISABLED.
    metrics_enabled: bool = True
    metrics_bearer_token: SecretSetting = ""
    metrics_allow_unauthenticated: bool = False
    # Scrape-time state (queue, outbox, providers, dependencies) is cached this long.
    metrics_cache_seconds: int = 15
    billing_provider: str = "manual"

    def __init__(self, **values: Any) -> None:
        # Validation errors never carry input values: the merged input holds every secret
        # (and any unrelated .env entry), which pydantic would otherwise attach and echo.
        try:
            super().__init__(**values)
        except ValidationError as exc:
            redacted = _redacted_validation_error(exc)
        else:
            return
        raise redacted  # outside the except block: no chained original error

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # `<NAME>_FILE` references rank with the variables they come from: keyword arguments
        # over the environment over the .env file, exactly as for a plain `<NAME>`.
        sources: list[PydanticBaseSettingsSource] = [init_settings]
        if isinstance(env_settings, EnvSettingsSource):
            sources.append(
                _SecretFileReferences(settings_cls, env_settings, "environment", os.environ)
            )
        sources.append(env_settings)
        if isinstance(dotenv_settings, EnvSettingsSource):
            sources.append(_SecretFileReferences(settings_cls, dotenv_settings, ".env file"))
        sources += [dotenv_settings, file_secret_settings]
        return tuple(sources)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [value.strip() for value in self.cors_origins.split(",") if value.strip()]

    @model_validator(mode="after")
    def validate_security_configuration(self) -> Settings:
        if self.max_upload_size_mb <= 0 or self.max_audio_duration_seconds <= 0:
            raise ValueError("Upload size and duration limits must be positive")
        if not 1 <= self.retention_days_default <= 3650:
            raise ValueError(
                "RETENTION_DAYS_DEFAULT must be between 1 and 3650 (new workspaces start FINITE)"
            )
        if self.ffprobe_timeout_seconds <= 0 or self.upload_rate_limit_per_hour <= 0:
            raise ValueError("Upload inspection and rate limits must be positive")
        if not 0 < self.ffmpeg_timeout_seconds <= 60:
            raise ValueError("FFMPEG_TIMEOUT_SECONDS must be between 0 and 60")
        # The demo allowance becomes an append-only plan version and an
        # entitlement limit (32-bit INTEGER): refuse it before anything is written.
        if not 0 <= self.demo_quota_seconds <= 2_147_483_647:
            raise ValueError("DEMO_QUOTA_SECONDS must be between 0 and 2147483647")
        if not 0 <= self.media_decode_probe_seconds <= 600 or self.media_min_bytes_per_second < 0:
            raise ValueError("Media decode probe and plausibility limits are out of range")
        if (
            not 0 <= self.media_channel_analysis_seconds <= 120
            or not 0 < self.media_channel_analysis_timeout_seconds <= 60
            or not -120 <= self.media_channel_active_threshold_dbfs < 0
            or not 0 <= self.media_channel_silent_max_active_share < 1
            or not 0 < self.media_channel_duplicate_min_correlation <= 1
            or not 0 <= self.media_channel_duplicate_max_lag_ms <= 20
        ):
            raise ValueError("Media channel analysis settings are out of range")
        if not 0 < self.media_derivative_timeout_seconds <= 1800:
            raise ValueError("MEDIA_DERIVATIVE_TIMEOUT_SECONDS must be above 0 and at most 1800")
        # 2047 MiB is the largest whole MiB that fits the 32-bit media_objects.size_bytes.
        if not 1 <= self.media_derivative_max_size_mb <= 2047:
            raise ValueError("MEDIA_DERIVATIVE_MAX_SIZE_MB must be between 1 and 2047")
        if (
            self.job_retry_delay_seconds <= 0
            or self.job_retry_max_delay_seconds < self.job_retry_delay_seconds
            or self.job_max_attempts <= 0
            or self.job_max_replays < 0
        ):
            raise ValueError(
                "Job retry budget settings must be positive and the maximum retry delay "
                "at least the base delay"
            )
        if self.job_deadline_seconds < self.job_attempt_timeout_seconds:
            raise ValueError("JOB_DEADLINE_SECONDS must be at least JOB_ATTEMPT_TIMEOUT_SECONDS")
        if (
            self.usage_reconciliation_interval_seconds <= 0
            or self.usage_reconciliation_grace_seconds <= 0
            or self.usage_reconciliation_batch_size <= 0
        ):
            raise ValueError("Usage reconciliation interval, grace and batch size must be positive")
        if (
            self.job_artifact_max_bytes <= 0
            or self.job_artifact_grace_seconds <= 0
            or self.job_artifact_sweep_interval_seconds <= 0
            or self.job_artifact_sweep_batch_size <= 0
        ):
            raise ValueError("Stage artifact settings must be positive")
        if (
            self.media_reconciliation_interval_seconds <= 0
            or self.media_reconciliation_grace_seconds <= 0
            or self.media_reconciliation_batch_size <= 0
        ):
            raise ValueError("Media reconciliation interval, grace and batch size must be positive")
        if (
            self.retention_purge_interval_seconds <= 0
            or self.retention_purge_batch_size <= 0
            or self.retention_purge_max_attempts <= 0
            or self.retention_purge_retry_base_seconds <= 0
            or self.retention_purge_retry_max_seconds < self.retention_purge_retry_base_seconds
            or self.retention_purge_hold_recheck_seconds <= 0
        ):
            raise ValueError("Retention purge settings must be positive (max delay >= base)")
        if self.job_artifact_grace_seconds < self.job_lease_seconds:
            raise ValueError(
                "JOB_ARTIFACT_GRACE_SECONDS must not be shorter than JOB_LEASE_SECONDS"
            )
        if (
            self.job_lease_seconds <= 0
            or self.job_lease_renew_interval_seconds <= 0
            or self.job_lease_renew_interval_seconds * 2 > self.job_lease_seconds
            or self.job_stage_timeout_seconds <= 0
            or self.job_attempt_timeout_seconds < self.job_stage_timeout_seconds
            or self.job_recovery_sweep_interval_seconds <= 0
            or self.job_recovery_batch_size <= 0
        ):
            raise ValueError(
                "Job lease settings must be positive, the renew interval at most half the "
                "lease and the attempt timeout at least the stage timeout"
            )
        if (
            self.dispatch_max_attempts <= 0
            or self.dispatch_retry_base_seconds <= 0
            or self.dispatch_retry_max_seconds < self.dispatch_retry_base_seconds
            or self.dispatch_sweep_interval_seconds <= 0
            or self.dispatch_sweep_batch_size <= 0
        ):
            raise ValueError("Dispatch outbox retry and sweep settings must be positive")
        if (
            self.recompute_max_attempts <= 0
            or self.recompute_sweep_interval_seconds <= 0
            or self.recompute_manual_rate_limit_per_minute <= 0
        ):
            raise ValueError("Recompute attempts, sweep interval and rate limit must be positive")
        if self.trend_judgment_cache_max_entries < 0:
            raise ValueError("TREND_JUDGMENT_CACHE_MAX_ENTRIES must be 0 or more")
        if self.trend_coverage_verdict_ttl_seconds < 0:
            raise ValueError("TREND_COVERAGE_VERDICT_TTL_SECONDS must be 0 or more")
        if self.database_app_role and not re.fullmatch(
            r"[a-z_][a-z0-9_]{0,62}", self.database_app_role
        ):
            raise ValueError("DATABASE_APP_ROLE must be a plain lowercase PostgreSQL role name")
        if self.model_manifest_pins:
            from backend.providers.registry import parse_pins

            parse_pins(self.model_manifest_pins)  # raises ValueError
        if not 0 <= self.job_retry_jitter_ratio <= 1:
            raise ValueError("JOB_RETRY_JITTER_RATIO must be between 0 and 1")
        if min(self.provider_circuit_failure_threshold, self.provider_circuit_cooldown_seconds) < 1:
            raise ValueError("Provider circuit threshold and cool-down must be positive")
        if self.provider_rate_limits:
            from backend.providers.registry import parse_rate_limits

            parse_rate_limits(self.provider_rate_limits)  # raises ValueError
        if bool(self.llm_fallback_base_url.strip()) != bool(self.llm_fallback_model.strip()):
            raise ValueError("LLM_FALLBACK_BASE_URL and LLM_FALLBACK_MODEL are set together")
        if self.pii_mapping_keys or self.pii_mapping_active_key_id:
            from backend.privacy.mapping_crypto import parse_keyring

            keyring = parse_keyring(self.pii_mapping_keys)  # raises ValueError
            if self.pii_mapping_active_key_id not in keyring:
                raise ValueError("PII_MAPPING_ACTIVE_KEY_ID must name one of PII_MAPPING_KEYS")
        if self.metrics_bearer_token and len(self.metrics_bearer_token.strip()) < 32:
            raise ValueError("METRICS_BEARER_TOKEN must be at least 32 characters")
        if not 0 <= self.metrics_cache_seconds <= 300:
            raise ValueError("METRICS_CACHE_SECONDS must be between 0 and 300")
        if (
            self.metrics_allow_unauthenticated
            and self.deployment_mode == "cloud"
            and self.environment in ("staging", "production")
        ):
            # Same rule as the HOM-161 transport opt-outs: none in cloud. On-premise
            # the opt-out is logged at startup by name (METRICS_UNAUTHENTICATED_OPT_OUT).
            raise ValueError(
                "METRICS_ALLOW_UNAUTHENTICATED is refused with DEPLOYMENT_MODE=cloud; "
                "set METRICS_BEARER_TOKEN instead (HOM-113)"
            )
        plaintext_hops: list[str] = []
        if self.environment == "production":
            if len(self.jwt_secret) < 32 or len(self.token_pepper) < 32:
                raise ValueError("JWT_SECRET and TOKEN_PEPPER must each be at least 32 characters")
            if self.database_url.startswith("sqlite"):
                raise ValueError("Production requires PostgreSQL; SQLite is development/test only")
            if not self.secure_cookies:
                raise ValueError("SECURE_COOKIES must be enabled in production")
            if self.enable_demo_account:
                raise ValueError("ENABLE_DEMO_ACCOUNT must be disabled in production")
            if self.allow_mock_providers:
                raise ValueError("ALLOW_MOCK_PROVIDERS must be disabled in production")
            if self.model_manifest_strict is False:
                raise ValueError("MODEL_MANIFEST_STRICT cannot be disabled in production")
            if self.auto_create_schema:
                raise ValueError("Production schema changes must run through Alembic migrations")
            # Mirrors backend.services.email_service: 465 is implicit TLS, 587 always
            # issues STARTTLS, any other port only when SMTP_USE_TLS is true.
            if self.smtp_host and self.smtp_port not in (465, 587) and not self.smtp_use_tls:
                raise ValueError(
                    "SMTP_HOST is set, so production mail needs TLS: use SMTP_PORT 465 "
                    "(implicit TLS) or 587 (STARTTLS), or set SMTP_USE_TLS=true"
                )
            if self.storage_backend == "s3":
                plaintext_hops += self._check_transport(
                    "object_storage",
                    "STORAGE_ENDPOINT_URL",
                    "an https:// URL (leave it empty for the provider's default endpoint)",
                    "STORAGE_ALLOW_INSECURE_ENDPOINT",
                    self.storage_allow_insecure_endpoint,
                    _storage_transport(self.storage_endpoint_url),
                )
            if self.storage_backend == "s3":
                insecure = [
                    name for name, value in _ambient_s3_endpoints() if not _is_https_url(value)
                ]
                if insecure:
                    raise ValueError(
                        "Ambient S3 endpoint overrides must be https:// URLs in production "
                        f"(found: {'; '.join(insecure)}). Homayar uses only "
                        "STORAGE_ENDPOINT_URL; unset the override or make it https://"
                    )
            plaintext_hops += self._check_transport(
                "postgresql",
                "DATABASE_URL",
                "sslmode=require, verify-ca or verify-full",
                "DATABASE_ALLOW_INSECURE_TRANSPORT",
                self.database_allow_insecure_transport,
                _postgres_transport(self.database_url),
            )
            plaintext_hops += self._check_transport(
                "redis",
                "REDIS_URL",
                "rediss://",
                "REDIS_ALLOW_INSECURE_TRANSPORT",
                self.redis_allow_insecure_transport,
                _redis_transport(self.redis_url),
            )
            if self.enable_neo4j_projection:
                plaintext_hops += self._check_transport(
                    "neo4j",
                    "NEO4J_URI",
                    "bolt+s:// or neo4j+s://",
                    "NEO4J_ALLOW_INSECURE_TRANSPORT",
                    self.neo4j_allow_insecure_transport,
                    _neo4j_transport(self.neo4j_uri),
                )
        if self.enable_google_auth and not (self.google_client_id and self.google_client_secret):
            raise ValueError("Google authentication is enabled but credentials are missing")
        if self.enable_phone_auth and not (self.asanak_api_key and self.asanak_sender):
            raise ValueError("Phone authentication is enabled but Asanak settings are missing")
        if self.storage_backend == "s3" and not (
            self.storage_bucket and self.storage_access_key and self.storage_secret_key
        ):
            raise ValueError("S3 storage requires bucket and credentials")
        if plaintext_hops:
            # The HOM-161 audit line names hops and variables only: the URLs carry
            # credentials. The logger is created here, not at import, because Alembic's
            # fileConfig disables every logger that exists before it runs (migrate job).
            logging.getLogger(__name__).warning(
                "TRANSPORT_TLS_OPT_OUT deployment_mode=%s hops=%s",
                self.deployment_mode,
                ",".join(plaintext_hops),
            )
        return self

    @model_validator(mode="after")
    def validate_asr_configuration(self) -> Settings:
        # Imported here, like parse_pins above.
        from backend.providers.asr_profiles import get_asr_profile
        from backend.speech.language_policy import LanguagePolicy

        if self.asr_profile:
            get_asr_profile(self.asr_profile)
            # The same name normalization as the provider factory.
            if self.asr_provider.strip().lower().replace("-", "_") != "local_whisper":
                raise ValueError("ASR_PROFILE needs ASR_PROVIDER=local_whisper")
        LanguagePolicy.from_settings(self)
        # Checked even when chunking is off, so turning it on cannot fail later.
        self.asr_chunk_settings()
        return self

    def asr_chunk_settings(self) -> ChunkSettings:
        """The long-call chunking of these settings; raises ``ValueError`` when invalid."""
        from backend.speech.chunking import ChunkSettings

        try:
            return ChunkSettings(
                min_call_seconds=self.asr_chunk_min_call_seconds,
                target_seconds=self.asr_chunk_target_seconds,
                search_seconds=self.asr_chunk_search_seconds,
                overlap_seconds=self.asr_chunk_overlap_seconds,
            )
        except ValueError as exc:
            raise ValueError(f"Invalid ASR_CHUNK_* settings: {exc}") from exc

    def _check_transport(
        self,
        hop: str,
        variable: str,
        tls_form: str,
        opt_out_variable: str,
        opt_out: bool,
        transport: _Transport,
    ) -> list[str]:
        """Production rule for one internal hop (HOM-161); messages never carry the value.

        Returns ``["hop(OPT_OUT_VARIABLE)"]`` when an on-premise opt-out lets the hop run in
        plaintext, for the startup audit line.
        """
        kind, hosts = transport
        if kind == "tls":
            return []
        refused = f"{variable} must use {tls_form} in production"
        if self.deployment_mode == "cloud":
            raise ValueError(
                f"{refused}; DEPLOYMENT_MODE=cloud has no plaintext opt-out, so "
                f"{opt_out_variable} does not apply (HOM-161)"
            )
        if kind == "invalid" or not opt_out:
            raise ValueError(
                f"{refused}; only an on-premise server on the same host may run without "
                f"TLS, with {opt_out_variable}=true (HOM-161)"
            )
        if not hosts or not all(_is_same_host(host) for host in hosts):
            raise ValueError(
                f"{opt_out_variable} covers only a server on the same host (loopback, a unix "
                f"socket or a single-label Compose service name); {variable} names another "
                f"or no host, so it must use {tls_form} (HOM-161)"
            )
        return [f"{hop}({opt_out_variable})"]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Test helper for isolated environment overrides."""
    get_settings.cache_clear()
