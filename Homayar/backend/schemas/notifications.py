"""The in-app notification inbox (HOM-76, ADR 0057 D6)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class NotificationView(BaseModel):
    id: str
    kind: str
    title: str
    body: str
    data: dict[str, Any]  # ids only
    read_at: datetime | None
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[NotificationView]  # newest first
    next_cursor: str | None
