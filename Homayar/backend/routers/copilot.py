"""AI Copilot API router."""
from fastapi import APIRouter, Depends
from backend.middleware.auth import require_auth
from backend.database.models import ChatRequest
from backend.services.copilot_service import process_question

router = APIRouter(dependencies=[Depends(require_auth)])


@router.post("/chat")
async def chat(request: ChatRequest):
    result = await process_question(request.message)
    return result
