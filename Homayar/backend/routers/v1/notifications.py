"""The in-app notification inbox (HOM-76, ADR 0057 D6).

* ``GET /notifications`` (``notification:read``): the caller's own rows of the current
  workspace, newest first, keyset pages; ``unread_only`` keeps the unread rows.
* ``POST /notifications/{notification_id}/read`` (``notification:read``, CSRF): mark the
  caller's own row read; idempotent.  Another user's row is ``404
  notification_not_found``.

``notification:read`` is never an API-key scope, so a key gets ``403
api_key_scope_denied``.  Marking a row read is not an audited change.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.database.relational import get_db
from backend.schemas.notifications import NotificationListResponse, NotificationView
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_permission,
    require_write_permission,
)
from backend.services.notification_service import list_inbox, mark_read, view
from backend.services.review_queue_service import ReviewQueueError

router = APIRouter(prefix="/notifications", tags=["notifications"])

require_read = require_permission(Permission.NOTIFICATION_READ)
require_mark = require_write_permission(Permission.NOTIFICATION_READ)


def _refused(error: ReviewQueueError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code, detail={"code": error.code, "message": str(error)}
    )


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    context: WorkspaceContext = Depends(require_read),
    db: Session = Depends(get_db),
) -> NotificationListResponse:
    try:
        page = list_inbox(
            db,
            workspace_id=context.workspace.id,
            user_id=context.actor.user.id,
            unread_only=unread_only,
            limit=limit,
            cursor=cursor,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return NotificationListResponse(
        items=[NotificationView.model_validate(view(row)) for row in page.rows],
        next_cursor=page.next_cursor,
    )


@router.post("/{notification_id}/read", response_model=NotificationView)
def read_notification(
    notification_id: str,
    context: WorkspaceContext = Depends(require_mark),
    db: Session = Depends(get_db),
) -> NotificationView:
    try:
        row, changed = mark_read(
            db,
            workspace_id=context.workspace.id,
            user_id=context.actor.user.id,
            notification_id=notification_id,
            now=datetime.now(UTC),
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    if changed:
        db.commit()
    return NotificationView.model_validate(view(row))
