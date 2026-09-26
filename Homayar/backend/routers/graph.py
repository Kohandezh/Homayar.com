"""Graph Intelligence API router."""
from fastapi import APIRouter, Depends
from backend.middleware.auth import require_auth
from backend.services.graph_service import get_entities, get_entity_detail, get_neighborhood, get_graph_stats

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/entities")
async def entities(label: str = "", search: str = ""):
    return get_entities(label, search)


@router.get("/entity/{node_id}")
async def entity_detail(node_id: str):
    return get_entity_detail(node_id)


@router.get("/neighborhood/{node_id}")
async def neighborhood(node_id: str, depth: int = 1):
    return get_neighborhood(node_id, depth)


@router.get("/stats")
async def stats():
    return get_graph_stats()
