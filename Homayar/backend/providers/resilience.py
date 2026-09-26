"""Provider resilience: endpoint circuits and request windows (WP08-T05, ADR 0030).

Applies to networked endpoints only; an in-process adapter has no endpoint to
protect.  State lives in ``provider_health`` so every worker sees the same
circuit and window:

* **Circuit.** ``provider_circuit_failure_threshold`` consecutive transient
  failures (timeouts, 429, 5xx, connection errors) open the circuit for
  ``provider_circuit_cooldown_seconds``; a provider's ``Retry-After`` opens it
  at least that long.  While open, no call is made: the attempt ends with the
  retryable ``provider_circuit_open`` (or moves to an approved same-policy
  fallback).  After the cool-down the next call is a trial: success closes
  the circuit, another failure re-opens it at once.  Permanent errors
  (authentication, invalid schema) do not count: retrying cannot fix them.
* **Request window.** ``provider_rate_limits`` (``manifest_id=N``) admits at
  most N calls per minute per endpoint across all workers; beyond it the
  attempt ends with the retryable ``provider_rate_limited`` before any call.

Admission never calls a provider.  The caller owns the transaction.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database.saas_models import ProviderHealth
from backend.jobs.leases import as_utc
from backend.providers.base import ProviderIdentity
from backend.providers.registry import ResolvedManifest

WINDOW = timedelta(minutes=1)
CIRCUIT_OPEN = "provider_circuit_open"
RATE_LIMITED = "provider_rate_limited"


def endpoint_key(identity: ProviderIdentity) -> str:
    canonical = json.dumps(identity.as_manifest(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Admission:
    allowed: bool
    code: str | None = None
    retry_after_seconds: float | None = None


def _locked_row(db: Session, resolved: ResolvedManifest, *, now: datetime) -> ProviderHealth:
    key = endpoint_key(resolved.identity)
    query = select(ProviderHealth).where(ProviderHealth.endpoint_key == key).with_for_update()
    row = db.scalar(query)
    if row is not None:
        return row
    savepoint = db.begin_nested()
    try:
        row = ProviderHealth(
            endpoint_key=key,
            stage=resolved.stage,
            provider=resolved.identity.provider[:128],
            model=resolved.identity.model[:256],
            consecutive_failures=0,
            window_count=0,
            updated_at=now,
        )
        db.add(row)
        db.flush()
        savepoint.commit()
        return row
    except IntegrityError:  # another worker created it first
        savepoint.rollback()
        return db.scalars(query).one()


def is_open(row: ProviderHealth | None, *, now: datetime) -> bool:
    open_until = as_utc(row.open_until) if row is not None else None
    return open_until is not None and open_until > now


def admit(
    db: Session, resolved: ResolvedManifest, *, limit_per_minute: int | None, now: datetime
) -> Admission:
    row = _locked_row(db, resolved, now=now)
    if is_open(row, now=now):
        remaining = (as_utc(row.open_until) - now).total_seconds()
        return Admission(False, CIRCUIT_OPEN, max(remaining, 0.0))
    if limit_per_minute:
        started = as_utc(row.window_started_at)
        if started is None or now - started >= WINDOW:
            row.window_started_at, row.window_count, started = now, 0, now
        if row.window_count >= limit_per_minute:
            return Admission(
                False, RATE_LIMITED, max((started + WINDOW - now).total_seconds(), 0.0)
            )
        row.window_count += 1
    row.updated_at = now
    return Admission(True)


def record_success(db: Session, resolved: ResolvedManifest, *, now: datetime) -> None:
    row = _locked_row(db, resolved, now=now)
    row.consecutive_failures = 0
    row.open_until = None
    row.last_success_at = now
    row.updated_at = now


def record_failure(
    db: Session,
    resolved: ResolvedManifest,
    *,
    code: str,
    retry_after_seconds: float | None,
    threshold: int,
    cooldown: timedelta,
    now: datetime,
) -> ProviderHealth:
    """Count one transient failure; open the circuit at the threshold and for
    at least as long as the provider asked (Retry-After)."""
    row = _locked_row(db, resolved, now=now)
    row.consecutive_failures += 1
    row.last_failure_code = code[:80]
    row.last_failure_at = now
    until = now + cooldown if row.consecutive_failures >= threshold else None
    if retry_after_seconds:
        asked = now + timedelta(seconds=retry_after_seconds)
        until = max(until, asked) if until is not None else asked
    if until is not None:
        current = as_utc(row.open_until)
        row.open_until = max(until, current) if current is not None else until
    row.updated_at = now
    return row


def health_rank(db: Session, *, now: datetime):
    """A ranker (routing.select) that keeps the configured order but moves
    candidates whose circuit is open behind the others."""

    def rank(eligible: Sequence[ResolvedManifest]) -> list[ResolvedManifest]:
        keys = [endpoint_key(candidate.identity) for candidate in eligible]
        rows = {
            row.endpoint_key: row
            for row in db.scalars(
                select(ProviderHealth).where(ProviderHealth.endpoint_key.in_(keys))
            )
        }
        order = sorted(
            range(len(eligible)),
            key=lambda i: (
                eligible[i].placement.networked and is_open(rows.get(keys[i]), now=now),
                i,
            ),
        )
        return [eligible[i] for i in order]

    return rank
