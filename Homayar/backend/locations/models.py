"""Workspace-scoped service locations (branches, dealers, service centres).

Registered on the shared ``Base`` metadata; the integrator owns the Alembic
migration and the RLS policy (tenant key ``workspace_id``).
"""

from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.saas_models import Base, TimestampMixin, new_id


class LocationKind(str, enum.Enum):
    BRANCH = "branch"
    DEALER = "dealer"
    SERVICE_CENTER = "service_center"


LOCATION_KINDS = tuple(kind.value for kind in LocationKind)


class ServiceLocation(Base, TimestampMixin):
    __tablename__ = "service_locations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="branch")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    province: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    district: Mapped[str | None] = mapped_column(String(120))
    street: Mapped[str | None] = mapped_column(String(300))
    postal_code: Mapped[str | None] = mapped_column(String(10))
    address_text: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Numeric(9, 6, asdecimal=False))
    longitude: Mapped[float | None] = mapped_column(Numeric(9, 6, asdecimal=False))
    phone: Mapped[str | None] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    service_tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    opening_hours: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    __table_args__ = (
        UniqueConstraint("workspace_id", "code", name="uq_service_location_workspace_code"),
        CheckConstraint(
            "kind IN ('branch', 'dealer', 'service_center')", name="ck_service_location_kind"
        ),
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_service_location_latitude",
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_service_location_longitude",
        ),
        CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)", name="ck_service_location_latlon_pair"
        ),
        CheckConstraint(
            "postal_code IS NULL OR length(postal_code) = 10",
            name="ck_service_location_postal_code",
        ),
        Index("ix_service_locations_workspace_active", "workspace_id", "is_active"),
        Index("ix_service_locations_workspace_lat_lon", "workspace_id", "latitude", "longitude"),
        Index("ix_service_locations_workspace_city", "workspace_id", "city"),
    )
