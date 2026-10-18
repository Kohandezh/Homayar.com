"""Voice call runtime control plane API (X21 / V02 / V05).

Mounted by the integrator under ``settings.api_prefix``. Tenant-scoped through
``X-Workspace-ID``; every route is permission-checked.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Never

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_permission,
    require_platform_operator,
    require_write_permission,
)
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.voice.admission import AdmissionPolicy, VoiceControlPlane
from backend.voice.models import VoiceCallSession, VoiceMediaNode
from backend.voice.state import VoiceStateError

router = APIRouter(prefix="/voice", tags=["voice"])

_control_plane: VoiceControlPlane | None = None


def get_voice_control_plane(settings: Settings = Depends(get_settings)) -> VoiceControlPlane:
    """Process-wide control plane built from the deployment settings;
    override in tests."""
    global _control_plane
    if _control_plane is None:
        _control_plane = VoiceControlPlane(
            AdmissionPolicy(
                global_limit=settings.voice_global_call_limit,
                default_workspace_limit=settings.voice_default_workspace_call_limit,
                heartbeat_ttl_seconds=float(settings.voice_node_heartbeat_ttl_seconds),
                slot_lease_seconds=float(settings.voice_slot_lease_seconds),
                profile=settings.voice_deployment_profile,
            )
        )
    return _control_plane


# --- schemas -----------------------------------------------------------------


class AdmitCallRequest(BaseModel):
    external_call_id: str = Field(min_length=1, max_length=255)
    direction: Literal["inbound", "outbound", "browser"]
    channel: Literal["sip", "webrtc", "pbx"]
    language: str | None = Field(default=None, max_length=16)


class TransitionRequest(BaseModel):
    action: Literal["ring", "answer", "end", "fail"]
    node_generation: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=64)


class CallSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    external_call_id: str
    direction: str
    channel: str
    state: str
    node_id: str | None
    node_generation: int | None
    language: str | None
    rejection_reason: str | None
    end_reason: str | None
    admitted_at: datetime | None
    started_at: datetime | None
    answered_at: datetime | None
    ended_at: datetime | None
    created_at: datetime


class NodeRegisterRequest(BaseModel):
    node_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    max_concurrent: int = Field(ge=0, le=10_000)


class NodeHeartbeatRequest(BaseModel):
    generation: int = Field(ge=1)


class NodeDrainRequest(BaseModel):
    draining: bool = True


class NodeResponse(BaseModel):
    node_id: str
    max_concurrent: int
    draining: bool
    generation: int
    heartbeat_at: datetime


def _call_response(call: VoiceCallSession) -> CallSessionResponse:
    return CallSessionResponse(
        id=call.id,
        workspace_id=call.workspace_id,
        external_call_id=call.external_call_id,
        direction=call.direction.value,
        channel=call.channel.value,
        state=call.state.value,
        node_id=call.node_id,
        node_generation=call.node_generation,
        language=call.language,
        rejection_reason=call.rejection_reason,
        end_reason=call.end_reason,
        admitted_at=call.admitted_at,
        started_at=call.started_at,
        answered_at=call.answered_at,
        ended_at=call.ended_at,
        created_at=call.created_at,
    )


def _node_response(node: VoiceMediaNode) -> NodeResponse:
    return NodeResponse(
        node_id=node.id,
        max_concurrent=node.max_concurrent,
        draining=node.draining,
        generation=node.generation,
        heartbeat_at=node.heartbeat_at,
    )


def _raise(exc: VoiceStateError) -> Never:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.public_message},
    ) from exc


def _audit(
    db: Session,
    request: Request,
    context: WorkspaceContext,
    settings: Settings,
    event_type: str,
    target_id: str,
    details: dict | None = None,
) -> None:
    record_audit_event(
        db,
        event_type=event_type,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="voice_media_node",
        target_id=target_id,
        request_id=getattr(request.state, "request_id", None),
        details=details,
        settings=settings,
    )
    db.commit()


# --- calls -------------------------------------------------------------------


@router.post("/calls", response_model=CallSessionResponse, status_code=201)
def admit_call(
    payload: AdmitCallRequest,
    response: Response,
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEETING_CREATE)),
    db: Session = Depends(get_db),
    plane: VoiceControlPlane = Depends(get_voice_control_plane),
) -> CallSessionResponse:
    try:
        result = plane.admit(
            db,
            workspace_id=context.workspace.id,
            external_call_id=payload.external_call_id,
            direction=payload.direction,
            channel=payload.channel,
            language=payload.language,
        )
    except VoiceStateError as exc:
        _raise(exc)
    if not result.admitted:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "voice_admission_rejected",
                "message": "The call was not admitted",
                "details": {"reason": result.reason, "session_id": result.session.id},
            },
            headers={"Retry-After": "1"},
        )
    if result.idempotent:
        response.status_code = 200
    return _call_response(result.session)


@router.get("/calls/{session_id}", response_model=CallSessionResponse)
def read_call(
    session_id: str,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
    plane: VoiceControlPlane = Depends(get_voice_control_plane),
) -> CallSessionResponse:
    try:
        call = plane.get_session(db, workspace_id=context.workspace.id, session_id=session_id)
    except VoiceStateError as exc:
        _raise(exc)
    return _call_response(call)


@router.post("/calls/{session_id}/transitions", response_model=CallSessionResponse)
def transition_call(
    session_id: str,
    payload: TransitionRequest,
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEETING_UPDATE)),
    db: Session = Depends(get_db),
    plane: VoiceControlPlane = Depends(get_voice_control_plane),
) -> CallSessionResponse:
    try:
        call = plane.transition(
            db,
            workspace_id=context.workspace.id,
            session_id=session_id,
            action=payload.action,
            node_generation=payload.node_generation,
            reason=payload.reason,
        )
    except VoiceStateError as exc:
        _raise(exc)
    return _call_response(call)


# --- capacity & nodes ----------------------------------------------------------


@router.get("/capacity")
def capacity(
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
    plane: VoiceControlPlane = Depends(get_voice_control_plane),
) -> dict:
    return plane.capacity_snapshot(
        db,
        workspace_id=context.workspace.id,
        include_deployment=context.has(Permission.WORKSPACE_MANAGE)
        and context.actor.api_key is None
        and context.actor.user.system_role == "operator",
    )


@router.post("/nodes", response_model=NodeResponse)
def register_node(
    payload: NodeRegisterRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_platform_operator(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    plane: VoiceControlPlane = Depends(get_voice_control_plane),
) -> NodeResponse:
    try:
        node = plane.register_node(
            db, node_id=payload.node_id, max_concurrent=payload.max_concurrent
        )
    except VoiceStateError as exc:
        _raise(exc)
    response = _node_response(node)
    _audit(
        db,
        request,
        context,
        settings,
        AuditEventType.VOICE_NODE_REGISTERED,
        node.id,
        {"max_concurrent": node.max_concurrent, "generation": node.generation},
    )
    return response


@router.post("/nodes/{node_id}/heartbeat", response_model=NodeResponse)
def node_heartbeat(
    node_id: str,
    payload: NodeHeartbeatRequest,
    context: WorkspaceContext = Depends(require_platform_operator(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
    plane: VoiceControlPlane = Depends(get_voice_control_plane),
) -> NodeResponse:
    try:
        node = plane.heartbeat(db, node_id=node_id, generation=payload.generation)
    except VoiceStateError as exc:
        _raise(exc)
    return _node_response(node)


@router.post("/nodes/{node_id}/drain", response_model=NodeResponse)
def drain_node(
    node_id: str,
    payload: NodeDrainRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_platform_operator(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    plane: VoiceControlPlane = Depends(get_voice_control_plane),
) -> NodeResponse:
    try:
        node = plane.set_draining(db, node_id=node_id, draining=payload.draining)
    except VoiceStateError as exc:
        _raise(exc)
    response = _node_response(node)
    _audit(
        db,
        request,
        context,
        settings,
        AuditEventType.VOICE_NODE_DRAIN_CHANGED,
        node.id,
        {"draining": node.draining},
    )
    return response


@router.post("/recover")
def recover(
    context: WorkspaceContext = Depends(require_platform_operator(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
    plane: VoiceControlPlane = Depends(get_voice_control_plane),
) -> dict:
    return plane.recover(db)
