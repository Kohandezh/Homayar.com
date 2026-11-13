"""Response of ``GET /api/v1/admin/license`` (HOM-109, ADR 0073).

Every key is always present. Fields that need a verified licence are ``null``
(lists and maps empty) when the licence is not enforced, missing or invalid;
an expired licence still shows them. Timestamps are UTC ``YYYY-MM-DDTHH:MM:SSZ``.
"""

from __future__ import annotations

from pydantic import BaseModel


class LicenseCounter(BaseModel):
    metric: str
    limit: int | None
    license_limit: int | None
    used: int
    reserved: int
    remaining: int | None


class OfflineLicenseResponse(BaseModel):
    enforced: bool
    state: str
    reason: str | None
    processing_allowed: bool
    license_id: str | None
    customer_id: str | None
    customer_name: str | None
    issued_at: str | None
    expires_at: str | None
    grace_until: str | None
    days_left: int | None
    modules: list[str]
    unknown_modules: list[str]
    limits: dict[str, int | None]
    unknown_limits: list[str]
    key_fingerprint: str | None
    warnings: list[str]
    counters: list[LicenseCounter]
