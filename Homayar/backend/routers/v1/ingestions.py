"""Read access to the ingestion ledger (WP05-T01, ADR 0016)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.database.relational import get_db
from backend.database.saas_models import IngestionSourceType
from backend.schemas.ingestions import IngestionListResponse, IngestionResponse
from backend.security.context import Permission, WorkspaceContext, require_permission
from backend.services.ingestion_service import ingestion_by_id, list_ingestions

router = APIRouter(tags=["ingestions"])


@router.get("/ingestions", response_model=IngestionListResponse)
def list_ingestion_records(
    limit: int = Query(default=50, ge=1, le=100),
    before: datetime | None = Query(default=None),
    source_type: IngestionSourceType | None = Query(default=None),
    external_event_id: str | None = Query(default=None, min_length=1, max_length=200),
    meeting_id: str | None = Query(default=None, min_length=1, max_length=36),
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
) -> IngestionListResponse:
    records = list_ingestions(
        db,
        workspace_id=context.workspace.id,
        access=context.record_access,
        limit=limit,
        before=before,
        source_type=source_type,
        external_event_id=external_event_id,
        meeting_id=meeting_id,
    )
    next_before = records[-1].received_at if len(records) == limit else None
    return IngestionListResponse(
        items=[IngestionResponse.model_validate(record) for record in records],
        next_before=next_before,
    )


@router.get("/ingestions/{ingestion_id}", response_model=IngestionResponse)
def get_ingestion_record(
    ingestion_id: str,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
) -> IngestionResponse:
    record = ingestion_by_id(
        db,
        workspace_id=context.workspace.id,
        access=context.record_access,
        ingestion_id=ingestion_id,
    )
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "ingestion_not_found", "message": "Ingestion record not found"},
        )
    return IngestionResponse.model_validate(record)
