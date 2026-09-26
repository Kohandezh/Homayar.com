"""User service — Neo4j-backed user management with bcrypt password hashing."""
import uuid
from datetime import datetime
from typing import Optional
from passlib.context import CryptContext

from backend.database.neo4j_connection import Neo4jConnection
from backend.database.models import UserCreate, UserUpdate

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def authenticate_user(email: str, password: str) -> Optional[dict]:
    """Authenticate user by email + password. Returns user dict or None."""
    # Try LDAP first if configured
    try:
        from backend.services.ad_service import authenticate_ldap
        ldap_user = authenticate_ldap(email, password)
        if ldap_user:
            # Sync LDAP user to local DB on first login
            local = get_user_by_email(email)
            if not local:
                create_user(UserCreate(
                    email=ldap_user["mail"],
                    name=ldap_user["displayName"],
                    password=password,  # Will be hashed by create_user
                    role="agent",
                    extension=ldap_user.get("telephoneNumber"),
                    department=ldap_user.get("department"),
                ))
            return get_user_by_email(email)
    except (ImportError, Exception):
        pass  # LDAP not configured or failed, try local

    # Local authentication
    user = get_user_by_email(email)
    if not user:
        return None
    if not user.get("is_active", True):
        return None
    if not verify_password(password, user.get("password_hash", "")):
        return None

    # Update last_login
    Neo4jConnection.execute_write("""
        MATCH (u:User {id: $uid})
        SET u.last_login = datetime($now)
    """, parameters={"uid": user["id"], "now": datetime.utcnow().isoformat()})

    return user


def get_user_by_email(email: str) -> Optional[dict]:
    """Get user by email address."""
    results = Neo4jConnection.execute_query("""
        MATCH (u:User {email: $email})
        RETURN u.id AS id, u.email AS email, u.name AS name, u.role AS role,
               u.extension AS extension, u.department AS department,
               u.phone_number AS phone_number, u.is_active AS is_active,
               u.auth_method AS auth_method, u.password_hash AS password_hash,
               u.created_at AS created_at, u.last_login AS last_login
    """, parameters={"email": email})
    if not results:
        return None
    return results[0]


def get_user_by_extension(extension: str) -> Optional[dict]:
    """Get user by VOIP extension number."""
    results = Neo4jConnection.execute_query("""
        MATCH (u:User {extension: $ext, is_active: true})
        RETURN u.id AS id, u.email AS email, u.name AS name, u.role AS role,
               u.extension AS extension, u.department AS department,
               u.is_active AS is_active, u.auth_method AS auth_method
    """, parameters={"ext": extension})
    if not results:
        return None
    return results[0]


def get_user_by_id(user_id: str) -> Optional[dict]:
    """Get user by ID."""
    results = Neo4jConnection.execute_query("""
        MATCH (u:User {id: $uid})
        RETURN u.id AS id, u.email AS email, u.name AS name, u.role AS role,
               u.extension AS extension, u.department AS department,
               u.phone_number AS phone_number, u.is_active AS is_active,
               u.auth_method AS auth_method, u.created_at AS created_at,
               u.last_login AS last_login
    """, parameters={"uid": user_id})
    if not results:
        return None
    return results[0]


def create_user(req: UserCreate) -> dict:
    """Create a new user with hashed password."""
    # Check if email already exists
    existing = get_user_by_email(req.email)
    if existing:
        raise ValueError(f"User with email {req.email} already exists")

    # Check if extension already taken
    if req.extension:
        ext_user = get_user_by_extension(req.extension)
        if ext_user:
            raise ValueError(f"Extension {req.extension} already assigned to {ext_user['name']}")

    user_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    hashed = hash_password(req.password)

    Neo4jConnection.execute_write("""
        CREATE (u:User {
            id: $id, email: $email, name: $name, role: $role,
            extension: $extension, department: $department,
            phone_number: $phone_number, is_active: true,
            auth_method: 'local', password_hash: $password_hash,
            created_at: datetime($created_at), last_login: null
        })
    """, parameters={
        "id": user_id, "email": req.email, "name": req.name, "role": req.role,
        "extension": req.extension, "department": req.department,
        "phone_number": req.phone_number, "password_hash": hashed, "created_at": now,
    })

    return {"id": user_id, "email": req.email, "name": req.name, "role": req.role,
            "extension": req.extension, "department": req.department}


def create_user_from_voip(extension: str, caller_id: Optional[str] = None) -> dict:
    """Auto-create user from VOIP extension on first call."""
    existing = get_user_by_extension(extension)
    if existing:
        return existing

    user_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    email = f"ext{extension}@homayar.local"
    name = f"Agent {extension}"
    random_pwd = str(uuid.uuid4())  # Random password — VOIP users don't use passwords

    Neo4jConnection.execute_write("""
        CREATE (u:User {
            id: $id, email: $email, name: $name, role: 'agent',
            extension: $extension, department: null,
            phone_number: $phone_number, is_active: true,
            auth_method: 'voip', password_hash: $password_hash,
            created_at: datetime($created_at), last_login: null
        })
    """, parameters={
        "id": user_id, "email": email, "name": name, "extension": extension,
        "phone_number": caller_id, "password_hash": hash_password(random_pwd),
        "created_at": now,
    })

    return {"id": user_id, "email": email, "name": name, "extension": extension,
            "auth_method": "voip"}


def list_users(role: Optional[str] = None) -> list:
    """List all users with optional role filter."""
    if role:
        query = """
            MATCH (u:User)
            WHERE u.role = $role
            RETURN u.id AS id, u.email AS email, u.name AS name, u.role AS role,
                   u.extension AS extension, u.department AS department,
                   u.is_active AS is_active, u.auth_method AS auth_method,
                   u.created_at AS created_at, u.last_login AS last_login
            ORDER BY u.name
        """
        return Neo4jConnection.execute_query(query, parameters={"role": role})
    else:
        query = """
            MATCH (u:User)
            RETURN u.id AS id, u.email AS email, u.name AS name, u.role AS role,
                   u.extension AS extension, u.department AS department,
                   u.is_active AS is_active, u.auth_method AS auth_method,
                   u.created_at AS created_at, u.last_login AS last_login
            ORDER BY u.name
        """
        return Neo4jConnection.execute_query(query)


def update_user(user_id: str, req: UserUpdate) -> dict:
    """Update user fields."""
    user = get_user_by_id(user_id)
    if not user:
        raise ValueError("User not found")

    updates = []
    params = {"uid": user_id}
    for field in ["name", "role", "extension", "department", "phone_number", "is_active"]:
        val = getattr(req, field, None)
        if val is not None:
            updates.append(f"u.{field} = ${field}")
            params[field] = val

    if not updates:
        return user

    set_clause = ", ".join(updates)
    Neo4jConnection.execute_write(f"""
        MATCH (u:User {{id: $uid}})
        SET {set_clause}
    """, parameters=params)

    return get_user_by_id(user_id)


def delete_user(user_id: str):
    """Soft-delete user by setting is_active = false."""
    Neo4jConnection.execute_write("""
        MATCH (u:User {id: $uid})
        SET u.is_active = false
    """, parameters={"uid": user_id})


def ensure_admin_exists():
    """Create default admin user if no users exist."""
    from backend.config import get_settings
    settings = get_settings()
    if not settings.admin_default_password:
        return  # Skip if no default password configured
    try:
        existing = list_users()
        if existing:
            return
        create_user(UserCreate(
            email="admin@homayar.io",
            name="Admin",
            password=settings.admin_default_password,
            role="admin",
        ))
    except Exception:
        pass  # Neo4j not available yet — admin will be created on next startup
