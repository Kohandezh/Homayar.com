"""LDAP sync router — AD user synchronization endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.middleware.auth import require_admin
from backend.services.ad_service import test_ldap_connection, sync_ad_to_neo4j

router = APIRouter()


@router.get("/test")
async def ldap_test(user: dict = Depends(require_admin)):
    """Test LDAP connectivity."""
    return test_ldap_connection()


@router.post("/sync")
async def ldap_sync(
    phones_only: bool = True,
    user: dict = Depends(require_admin),
):
    """Full sync: fetch users from AD and upsert into Neo4j."""
    result = sync_ad_to_neo4j(phones_only=phones_only)
    return result
