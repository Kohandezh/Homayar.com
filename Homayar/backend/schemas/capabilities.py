from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CapabilityModuleResponse(BaseModel):
    key: str
    name: str
    description: str
    enabled: bool
    reason: str | None
    install_enabled: bool
    licensed: bool
    admin_enabled: bool


class CapabilityListResponse(BaseModel):
    registry_version: int
    workspace_id: str
    modules: list[CapabilityModuleResponse]


class CapabilityUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # strict: only a JSON bool, never "true" or 1
    enabled: bool = Field(strict=True)
