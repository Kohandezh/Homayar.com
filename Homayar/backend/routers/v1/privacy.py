"""Data-subject requests (WP07-T07, ADR 0025).

``POST /privacy/subject-exports`` returns what the workspace holds about a
data subject identified by structured identifiers.  The document contains
originals, so it needs ``pii:original_access`` (owner; never an API key),
CSRF and a reason, and is audited without any identifier.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.privacy.subject_export import (
    SubjectIdentifierError,
    build_subject_export,
    parse_subject_identifier,
)
from backend.schemas.privacy import SubjectExportRequest
from backend.security.context import Permission, WorkspaceContext, require_write_permission
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event

router = APIRouter(prefix="/privacy", tags=["privacy"])


@router.post("/subject-exports")
def export_subject(
    payload: SubjectExportRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.PII_ORIGINAL_ACCESS)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        identifiers = [parse_subject_identifier(value) for value in payload.identifiers]
    except SubjectIdentifierError as exc:
        raise HTTPException(
            status_code=422, detail={"code": exc.code, "message": str(exc)}
        ) from exc
    identifiers = list(dict.fromkeys(identifiers))
    now = datetime.now(UTC)
    document, audit = build_subject_export(
        db,
        workspace_id=context.workspace.id,
        identifiers=identifiers,
        meeting_clause=context.record_access.meeting_clause(),
        now=now,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.SUBJECT_EXPORTED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="workspace",
        target_id=context.workspace.id,
        request_id=getattr(request.state, "request_id", None),
        details={**audit, "reason": payload.reason[:120]},
        settings=settings,
    )
    db.commit()
    return Response(
        content=json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="homayar-subject-export-{now:%Y%m%dT%H%M%SZ}.json"'
            )
        },
    )
