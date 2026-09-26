"""Registration of model manifests in the platform catalog (WP08-T01, ADR 0026).

Every manifest a job resolves is stored once, keyed by its digest, before any
stage runs; stage and analysis runs then reference that digest.  The catalog
is append-only: a different document under an existing ``manifest_id@version``
is refused (a released manifest never changes in place).
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database.relational import get_session_factory
from backend.database.saas_models import ModelManifestRecord
from backend.providers.base import ProviderError
from backend.providers.registry import ModelManifest, ResolvedManifest


def _conflict(manifest: ModelManifest) -> ProviderError:
    return ProviderError(
        "manifest_conflict",
        f"{manifest.manifest_id}@{manifest.version} is already registered with different "
        "content; publish a new version instead",
        retryable=False,
    )


def register_manifest(db: Session, manifest: ModelManifest) -> ModelManifestRecord:
    existing = db.get(ModelManifestRecord, manifest.digest)
    if existing is not None:
        return existing
    clash = db.scalar(
        select(ModelManifestRecord).where(
            ModelManifestRecord.manifest_id == manifest.manifest_id,
            ModelManifestRecord.version == manifest.version,
        )
    )
    if clash is not None:
        raise _conflict(manifest)
    record = ModelManifestRecord(
        digest=manifest.digest,
        manifest_id=manifest.manifest_id,
        version=manifest.version,
        stage=manifest.stage,
        status=manifest.status.value,
        document=manifest.document(),
    )
    savepoint = db.begin_nested()
    try:
        db.add(record)
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        existing = db.get(ModelManifestRecord, manifest.digest)
        if existing is None:
            raise _conflict(manifest) from None
        return existing
    return record


def register_resolved(resolved: Iterable[ResolvedManifest]) -> None:
    """Store the manifests of one preflight in their own short transaction."""
    manifests = [item.manifest for item in resolved if item.manifest is not None]
    if not manifests:
        return
    with get_session_factory()() as db:
        for manifest in manifests:
            register_manifest(db, manifest)
        db.commit()
