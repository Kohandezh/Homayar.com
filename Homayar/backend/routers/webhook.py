"""VOIP webhook router — receives call events from Asterisk/3CX/FreePBX."""
import json
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel

from backend.config import get_settings
from backend.middleware.auth import require_admin
from backend.services.user_service import get_user_by_extension, create_user_from_voip
from backend.services import call_service

router = APIRouter()


# ─── Normalized VOIP Event Model ──────────────────────────────

class VoipCallEvent(BaseModel):
    """Normalized call event from any VOIP system."""
    source: str = "generic"  # asterisk | 3cx | freepbx | generic
    call_id: Optional[str] = None
    extension: Optional[str] = None  # Agent extension e.g. "101"
    caller_id: Optional[str] = None  # External caller number
    direction: str = "inbound"  # inbound | outbound
    duration: float = 0.0
    recording_url: Optional[str] = None
    recording_base64: Optional[str] = None
    language: str = "fa"
    timestamp: Optional[str] = None
    # Raw fields kept for debugging
    raw_data: Optional[dict] = None


# ─── Webhook Endpoint ─────────────────────────────────────────

@router.post("/voip/call-event")
async def voip_call_event(request: Request):
    """
    Receive call events from VOIP systems.
    Auto-normalizes Asterisk, 3CX, FreePBX, and generic formats.
    Auto-creates users by extension on first call.
    """
    settings = get_settings()

    # Verify webhook secret if configured
    if settings.voip_webhook_secret:
        auth = request.headers.get("X-Webhook-Secret", "")
        if auth != settings.voip_webhook_secret:
            raise HTTPException(401, "Invalid webhook secret")

    # Parse body
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    # Detect source and normalize
    event = _normalize_event(body)

    if not event.extension:
        raise HTTPException(400, "Missing extension/caller ID in event")

    # Auto-create user from VOIP extension if not exists
    user = get_user_by_extension(event.extension)
    if not user:
        user = create_user_from_voip(event.extension, event.caller_id)

    # Create call record
    call_id = None
    if event.recording_base64 or event.recording_url:
        import base64, uuid
        import os

        audio_bytes = None
        if event.recording_base64:
            try:
                audio_bytes = base64.b64decode(event.recording_base64)
            except Exception:
                pass

        if audio_bytes:
            cid, file_path = call_service.save_upload_bytes(audio_bytes, f"voip_{event.extension}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.wav")
            call_service.create_call_record(
                call_id=cid,
                agent_id=user.get("id", "unknown"),
                file_path=file_path,
                file_name=f"voip_{event.extension}.wav",
                direction=event.direction,
                language=event.language,
                phone_number=event.caller_id,
            )
            call_id = cid

    return {
        "status": "processed",
        "extension": event.extension,
        "user": user.get("name"),
        "call_id": call_id,
        "message": "Call event received. User auto-created." if not get_user_by_extension(event.extension) else "Call event received.",
    }


@router.post("/voip/batch")
async def voip_batch_events(request: Request):
    """Receive multiple call events at once (e.g., daily sync from VOIP system)."""
    settings = get_settings()
    if settings.voip_webhook_secret:
        auth = request.headers.get("X-Webhook-Secret", "")
        if auth != settings.voip_webhook_secret:
            raise HTTPException(401, "Invalid webhook secret")

    try:
        events = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    if not isinstance(events, list):
        events = [events]

    results = []
    for body in events[:100]:  # Max 100 events per batch
        try:
            event = _normalize_event(body)
            if event.extension:
                user = get_user_by_extension(event.extension)
                if not user:
                    user = create_user_from_voip(event.extension)
                results.append({"extension": event.extension, "status": "ok"})
            else:
                results.append({"status": "skipped", "reason": "no extension"})
        except Exception as e:
            results.append({"status": "error", "detail": str(e)})

    return {"processed": len(results), "results": results}


# ─── Normalizers ──────────────────────────────────────────────

def _normalize_event(body: dict) -> VoipCallEvent:
    """Auto-detect VOIP source and normalize to standard format."""

    # Asterisk ARI format
    if "asterisk_event" in body or "stasis_app" in body:
        return VoipCallEvent(
            source="asterisk",
            call_id=body.get("channel", {}).get("id") or body.get("uniqueid"),
            extension=_extract_extension(body.get("channel", {}).get("caller", {}).get("number", "")),
            caller_id=body.get("channel", {}).get("connected", {}).get("number"),
            direction="inbound" if body.get("asterisk_event") == "StasisStart" else "outbound",
            duration=float(body.get("duration", 0)),
            recording_url=body.get("recording", {}).get("url"),
            timestamp=body.get("timestamp"),
            raw_data=body,
        )

    # 3CX format
    if "CallId" in body or "Extension" in body or "3CX" in str(body):
        return VoipCallEvent(
            source="3cx",
            call_id=body.get("CallId"),
            extension=body.get("Extension", body.get("Agent", "")),
            caller_id=body.get("CallerId", body.get("Number", "")),
            direction=body.get("Direction", "inbound").lower(),
            duration=float(body.get("Duration", 0)),
            recording_url=body.get("Recording"),
            timestamp=body.get("Time"),
            raw_data=body,
        )

    # FreePBX / generic format
    return VoipCallEvent(
        source="generic",
        call_id=body.get("call_id", body.get("id")),
        extension=body.get("extension", body.get("agent", body.get("ext", ""))),
        caller_id=body.get("caller_id", body.get("caller", body.get("from", ""))),
        direction=body.get("direction", "inbound"),
        duration=float(body.get("duration", 0)),
        recording_url=body.get("recording_url", body.get("recording")),
        recording_base64=body.get("recording_base64"),
        language=body.get("language", "fa"),
        timestamp=body.get("timestamp", body.get("time")),
        raw_data=body,
    )


def _extract_extension(value: str) -> str:
    """Extract extension number from various formats."""
    if not value:
        return ""
    # Strip non-numeric chars, keep extension-like numbers (2-5 digits)
    cleaned = "".join(c for c in str(value) if c.isdigit())
    if 2 <= len(cleaned) <= 5:
        return cleaned
    return str(value)
