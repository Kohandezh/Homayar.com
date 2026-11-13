"""The signed offline licence of an on-prem install (HOM-109, ADR 0073)."""

from backend.licensing.offline_license import (
    LICENSE_MAX_BYTES,
    LICENSE_SCHEMA,
    LicenseRestrictedError,
    LicenseStatus,
    clear_license_cache,
    current_license_status,
    evaluate,
    status_line,
)

__all__ = [
    "LICENSE_MAX_BYTES",
    "LICENSE_SCHEMA",
    "LicenseRestrictedError",
    "LicenseStatus",
    "clear_license_cache",
    "current_license_status",
    "evaluate",
    "status_line",
]
