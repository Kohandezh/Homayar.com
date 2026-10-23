"""Opt-in target for the MinIO storage contract run (HOM-36).

The ``s3-minio`` parametrization of ``test_storage_contract.py`` and the
``minio_s3``-marked cases run against a *disposable* local MinIO container
started by ``infra/ci/minio-contract-target.sh up <port> <env-file>``.  They
run only when *all* of the following hold; otherwise they are skipped as
NOT_RUN (or, with ``HOMAYAR_REQUIRE_MINIO_TESTS=1``, the run stops):

* ``HOMAYAR_MINIO_TEST=1`` — explicit opt-in;
* ``HOMAYAR_MINIO_ENDPOINT_URL`` — an ``http``/``https`` URL on a loopback
  host (``127.0.0.1``, ``::1`` or ``localhost``).  This target is only for the
  disposable local container; an external bucket uses the ``s3-live`` path;
* ``HOMAYAR_MINIO_BUCKET`` — the bucket the script created;
* ``HOMAYAR_MINIO_ACCESS_KEY`` / ``HOMAYAR_MINIO_SECRET_KEY`` — the app user
  with the ``homayar-app`` policy of the on-prem Compose profile;
* ``HOMAYAR_MINIO_DENIED_ACCESS_KEY`` / ``HOMAYAR_MINIO_DENIED_SECRET_KEY`` —
  a user with no policy attached;
* ``HOMAYAR_MINIO_PROBE_ACCESS_KEY`` / ``HOMAYAR_MINIO_PROBE_SECRET_KEY`` — a
  user whose policy covers only ``<bucket>-missing``, a bucket that is never
  created.

The script generates every credential fresh on each ``up``.  Credential
values are never logged: the target's ``repr`` hides them and the SDK loggers
are held at WARNING while the run is active (``quiet_sdk_logging``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

MINIO_OPT_IN_ENV = "HOMAYAR_MINIO_TEST"
MINIO_ENDPOINT_ENV = "HOMAYAR_MINIO_ENDPOINT_URL"
MINIO_BUCKET_ENV = "HOMAYAR_MINIO_BUCKET"
MINIO_ACCESS_KEY_ENV = "HOMAYAR_MINIO_ACCESS_KEY"
MINIO_SECRET_KEY_ENV = "HOMAYAR_MINIO_SECRET_KEY"  # noqa: S105 - variable name only
MINIO_DENIED_ACCESS_KEY_ENV = "HOMAYAR_MINIO_DENIED_ACCESS_KEY"
MINIO_DENIED_SECRET_KEY_ENV = "HOMAYAR_MINIO_DENIED_SECRET_KEY"  # noqa: S105 - variable name only
MINIO_PROBE_ACCESS_KEY_ENV = "HOMAYAR_MINIO_PROBE_ACCESS_KEY"
MINIO_PROBE_SECRET_KEY_ENV = "HOMAYAR_MINIO_PROBE_SECRET_KEY"  # noqa: S105 - variable name only

MINIO_REQUIRED_ENVS = (
    MINIO_ENDPOINT_ENV,
    MINIO_BUCKET_ENV,
    MINIO_ACCESS_KEY_ENV,
    MINIO_SECRET_KEY_ENV,
    MINIO_DENIED_ACCESS_KEY_ENV,
    MINIO_DENIED_SECRET_KEY_ENV,
    MINIO_PROBE_ACCESS_KEY_ENV,
    MINIO_PROBE_SECRET_KEY_ENV,
)
MINIO_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
MINIO_REGION = "us-east-1"


@dataclass(frozen=True)
class MinioS3Target:
    endpoint_url: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    denied_access_key_id: str
    denied_secret_access_key: str
    probe_access_key_id: str
    probe_secret_access_key: str
    region_name: str = MINIO_REGION

    @property
    def missing_bucket(self) -> str:
        """The bucket the probe user's policy covers; the script never creates it."""
        return f"{self.bucket}-missing"

    def __repr__(self) -> str:  # never leak credentials through pytest output
        return (
            f"MinioS3Target(endpoint_url={self.endpoint_url!r}, bucket={self.bucket!r}, "
            f"region_name={self.region_name!r})"
        )


def minio_unavailable_reason() -> str | None:
    """``None`` when the MinIO run is explicitly enabled and fully configured."""
    if os.environ.get(MINIO_OPT_IN_ENV, "") != "1":
        return (
            f"{MINIO_OPT_IN_ENV} is not 1 (the MinIO run is opt-in; start the target with "
            "infra/ci/minio-contract-target.sh)"
        )
    missing = [name for name in MINIO_REQUIRED_ENVS if not os.environ.get(name, "").strip()]
    if missing:
        return "missing " + ", ".join(missing)
    endpoint = os.environ[MINIO_ENDPOINT_ENV].strip()
    try:
        parts = urlsplit(endpoint)
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError on an invalid port
    except ValueError:
        return f"{MINIO_ENDPOINT_ENV} is not a valid URL"
    if parts.scheme not in {"http", "https"}:
        return f"{MINIO_ENDPOINT_ENV} must use http or https"
    if host not in MINIO_LOOPBACK_HOSTS:
        return (
            f"{MINIO_ENDPOINT_ENV} must be a loopback address (127.0.0.1, ::1 or localhost); "
            "the MinIO target is only for a disposable local container, an external bucket "
            "uses the s3-live path"
        )
    return None


def minio_target() -> MinioS3Target:
    reason = minio_unavailable_reason()
    if reason is not None:
        raise RuntimeError(reason)
    return MinioS3Target(
        endpoint_url=os.environ[MINIO_ENDPOINT_ENV].strip(),
        bucket=os.environ[MINIO_BUCKET_ENV].strip(),
        access_key_id=os.environ[MINIO_ACCESS_KEY_ENV],
        secret_access_key=os.environ[MINIO_SECRET_KEY_ENV],
        denied_access_key_id=os.environ[MINIO_DENIED_ACCESS_KEY_ENV],
        denied_secret_access_key=os.environ[MINIO_DENIED_SECRET_KEY_ENV],
        probe_access_key_id=os.environ[MINIO_PROBE_ACCESS_KEY_ENV],
        probe_secret_access_key=os.environ[MINIO_PROBE_SECRET_KEY_ENV],
    )
