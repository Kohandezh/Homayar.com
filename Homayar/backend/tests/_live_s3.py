"""Opt-in target for the live S3-compatible storage contract run (WP05-T05).

Disabled by default.  The live parametrization of ``test_storage_contract.py``
runs only when *all* of the following hold; otherwise it is skipped and must
be reported as NOT_RUN / BLOCKED_EXTERNAL, never as a pass:

* ``HOMAYAR_LIVE_S3_TEST=1`` — explicit opt-in (credentials alone are not
  authorization to mutate a bucket);
* ``HOMAYAR_LIVE_S3_BUCKET`` — a designated *disposable* bucket;
* ``HOMAYAR_LIVE_S3_TENANT_PREFIX`` — a disposable tenant-id prefix; every
  object the run creates lives under ``tenants/<prefix>-<random>/objects/``
  and only those exact keys are removed afterwards;
* ``HOMAYAR_LIVE_S3_ACCESS_KEY`` / ``HOMAYAR_LIVE_S3_SECRET_KEY``.

Optional: ``HOMAYAR_LIVE_S3_ENDPOINT_URL`` (S3-compatible services),
``HOMAYAR_LIVE_S3_REGION`` (default ``us-east-1``).

Credential values are never logged; botocore/boto3/urllib3 loggers are held
at WARNING while the live run is active.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

LIVE_S3_OPT_IN_ENV = "HOMAYAR_LIVE_S3_TEST"
LIVE_S3_BUCKET_ENV = "HOMAYAR_LIVE_S3_BUCKET"
LIVE_S3_TENANT_PREFIX_ENV = "HOMAYAR_LIVE_S3_TENANT_PREFIX"
LIVE_S3_ACCESS_KEY_ENV = "HOMAYAR_LIVE_S3_ACCESS_KEY"
LIVE_S3_SECRET_KEY_ENV = "HOMAYAR_LIVE_S3_SECRET_KEY"  # noqa: S105 - variable name only
LIVE_S3_ENDPOINT_ENV = "HOMAYAR_LIVE_S3_ENDPOINT_URL"
LIVE_S3_REGION_ENV = "HOMAYAR_LIVE_S3_REGION"

# Hard bounds for the live run: tiny fixtures, short time.
LIVE_S3_MAX_OBJECT_BYTES = 64 * 1024
LIVE_S3_MAX_OBJECTS = 8

_SENSITIVE_LOGGERS = ("boto3", "botocore", "urllib3", "s3transfer")


@dataclass(frozen=True)
class LiveS3Target:
    bucket: str
    tenant_prefix: str
    access_key_id: str
    secret_access_key: str
    endpoint_url: str | None
    region_name: str

    def __repr__(self) -> str:  # never leak credentials through pytest output
        return (
            f"LiveS3Target(bucket={self.bucket!r}, tenant_prefix={self.tenant_prefix!r}, "
            f"endpoint_url={self.endpoint_url!r}, region_name={self.region_name!r})"
        )


def live_s3_unavailable_reason() -> str | None:
    """``None`` when the live run is explicitly enabled and fully configured."""
    if os.environ.get(LIVE_S3_OPT_IN_ENV, "") != "1":
        return f"{LIVE_S3_OPT_IN_ENV} is not 1 (live S3 run is opt-in)"
    missing = [
        name
        for name in (
            LIVE_S3_BUCKET_ENV,
            LIVE_S3_TENANT_PREFIX_ENV,
            LIVE_S3_ACCESS_KEY_ENV,
            LIVE_S3_SECRET_KEY_ENV,
        )
        if not os.environ.get(name, "").strip()
    ]
    if missing:
        return "missing " + ", ".join(missing)
    prefix = os.environ[LIVE_S3_TENANT_PREFIX_ENV].strip()
    if len(prefix) > 40 or not prefix.replace("-", "").replace("_", "").isalnum():
        return f"{LIVE_S3_TENANT_PREFIX_ENV} must be alphanumeric/-/_ (max 40 chars)"
    return None


def live_s3_target() -> LiveS3Target:
    reason = live_s3_unavailable_reason()
    if reason is not None:
        raise RuntimeError(reason)
    return LiveS3Target(
        bucket=os.environ[LIVE_S3_BUCKET_ENV].strip(),
        tenant_prefix=os.environ[LIVE_S3_TENANT_PREFIX_ENV].strip(),
        access_key_id=os.environ[LIVE_S3_ACCESS_KEY_ENV],
        secret_access_key=os.environ[LIVE_S3_SECRET_KEY_ENV],
        endpoint_url=os.environ.get(LIVE_S3_ENDPOINT_ENV, "").strip() or None,
        region_name=os.environ.get(LIVE_S3_REGION_ENV, "").strip() or "us-east-1",
    )


def quiet_sdk_logging() -> list[tuple[logging.Logger, int]]:
    """Raise SDK loggers to WARNING; returns previous levels for restoration."""
    previous = []
    for name in _SENSITIVE_LOGGERS:
        logger = logging.getLogger(name)
        previous.append((logger, logger.level))
        logger.setLevel(logging.WARNING)
    return previous
