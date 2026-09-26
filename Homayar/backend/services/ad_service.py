"""Active Directory / LDAP integration — user sync and authentication."""
import base64
from typing import Optional
from datetime import datetime

from backend.config import get_settings

# LDAP filter for enabled users with phone extensions
LDAP_FILTER_USERS_WITH_PHONE = (
    "(&"
    "  (objectClass=user)"
    "  (objectCategory=person)"
    "  (!(userAccountControl:1.2.840.113556.1.4.803:=2))"
    "  (|(telephoneNumber=*)(ipPhone=*))"
    ")"
)

LDAP_FILTER_ALL_ENABLED = (
    "(&"
    "  (objectClass=user)"
    "  (objectCategory=person)"
    "  (!(userAccountControl:1.2.840.113556.1.4.803:=2))"
    ")"
)

AD_ATTRIBUTES = [
    "objectGUID", "sAMAccountName", "displayName", "mail",
    "telephoneNumber", "ipPhone", "department", "memberOf",
    "distinguishedName", "whenChanged",
]


def _get_connection():
    """Create and return a bound LDAP connection. Returns None if LDAP not configured."""
    settings = get_settings()
    if not settings.ldap_server:
        return None

    from ldap3 import Server, Connection, ALL
    from ldap3.core.exceptions import LDAPException

    try:
        server = Server(
            host=settings.ldap_server,
            port=settings.ldap_port,
            use_ssl=settings.ldap_use_ssl,
            get_info=ALL,
            connect_timeout=10,
        )
        conn = Connection(
            server,
            user=settings.ldap_bind_dn,
            password=settings.ldap_bind_password,
            auto_bind=True,
            raise_exceptions=True,
        )
        return conn
    except Exception:
        return None


def authenticate_ldap(username: str, password: str) -> Optional[dict]:
    """Authenticate user via LDAP bind. Returns user attrs or None."""
    settings = get_settings()
    if not settings.ldap_server:
        return None

    from ldap3 import Server, Connection, ALL, SUBTREE
    from ldap3.core.exceptions import LDAPException

    # UPN format: user@domain
    domain = settings.ldap_server.split(".")[0] + "." + ".".join(settings.ldap_server.split(".")[1:])
    upn = username if "@" in username else f"{username}@{domain}"

    try:
        server = Server(
            host=settings.ldap_server,
            port=settings.ldap_port,
            use_ssl=settings.ldap_use_ssl,
            get_info=ALL,
            connect_timeout=10,
        )
        conn = Connection(server, user=upn, password=password, auto_bind=True, raise_exceptions=True)

        # Fetch user attributes
        sam = username.split("@")[0]
        conn.search(
            search_base=settings.ldap_search_base,
            search_filter=f"(sAMAccountName={sam})",
            attributes=AD_ATTRIBUTES,
            search_scope=SUBTREE,
        )

        user_data = None
        if conn.entries:
            entry = conn.entries[0]
            attrs = entry.entry_attributes_as_dict
            user_data = {
                "sAMAccountName": attrs.get("sAMAccountName", [""])[0],
                "displayName": attrs.get("displayName", [""])[0],
                "mail": attrs.get("mail", [""])[0],
                "telephoneNumber": attrs.get("telephoneNumber", [""])[0],
                "department": attrs.get("department", [""])[0],
                "memberOf": attrs.get("memberOf", []),
            }
        conn.unbind()
        return user_data
    except Exception:
        return None


def fetch_ad_users(phones_only: bool = True) -> list:
    """Fetch users from AD with pagination. Returns list of dicts."""
    conn = _get_connection()
    if not conn:
        return []

    settings = get_settings()
    from ldap3 import SUBTREE

    search_filter = LDAP_FILTER_USERS_WITH_PHONE if phones_only else LDAP_FILTER_ALL_ENABLED
    search_base = f"{settings.ldap_user_ou},{settings.ldap_search_base}" if settings.ldap_user_ou else settings.ldap_search_base

    users = []
    try:
        entry_generator = conn.extend.standard.paged_search(
            search_base=search_base,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=AD_ATTRIBUTES,
            paged_size=500,
            generator=True,
        )
        for entry in entry_generator:
            if entry.get("type") != "searchResEntry":
                continue
            try:
                attrs = entry.get("attributes", {})
                users.append({
                    "username": attrs.get("sAMAccountName", ""),
                    "name": attrs.get("displayName", ""),
                    "email": attrs.get("mail", ""),
                    "extension": attrs.get("telephoneNumber", "") or attrs.get("ipPhone", ""),
                    "department": attrs.get("department", ""),
                    "groups": attrs.get("memberOf", []),
                })
            except Exception:
                continue
    except Exception:
        pass
    finally:
        try:
            conn.unbind()
        except Exception:
            pass

    return users


def test_ldap_connection() -> dict:
    """Test LDAP connectivity."""
    conn = _get_connection()
    if conn is None:
        settings = get_settings()
        if not settings.ldap_server:
            return {"status": "not_configured", "detail": "LDAP_SERVER not set in .env"}
        return {"status": "error", "detail": "Connection failed"}
    info = {"status": "ok", "server": conn.server.host}
    conn.unbind()
    return info


def sync_ad_to_neo4j(phones_only: bool = True) -> dict:
    """Fetch users from AD and upsert into Neo4j User nodes."""
    from backend.database.neo4j_connection import Neo4jConnection

    ad_users = fetch_ad_users(phones_only)
    created, updated, errors = 0, 0, 0

    for u in ad_users:
        if not u["username"]:
            continue
        try:
            Neo4jConnection.execute_write("""
                MERGE (u:User {email: $email})
                ON CREATE SET
                    u.id = randomUUID(),
                    u.name = $name,
                    u.role = 'agent',
                    u.extension = $extension,
                    u.department = $department,
                    u.is_active = true,
                    u.auth_method = 'ldap',
                    u.password_hash = '',
                    u.created_at = datetime(),
                    u.last_login = null
                ON MATCH SET
                    u.name = $name,
                    u.extension = $extension,
                    u.department = $department
            """, parameters={
                "email": u["email"] or f"{u['username']}@ldap.local",
                "name": u["name"], "extension": u["extension"], "department": u["department"],
            })
            created += 1
        except Exception:
            errors += 1

    return {"fetched": len(ad_users), "synced": created, "errors": errors}
