"""PostgreSQL row-level security policies (WP03-T05).

Single source of the DDL used by the Alembic migration *and* by
``create_schema_for_test_or_development`` so the test suite runs under exactly
the policies production gets.

Roles
-----
* **Ownership / migration role** – whatever runs ``alembic upgrade``; it owns
  the tables.  ``FORCE ROW LEVEL SECURITY`` keeps the owner subject to the
  policies too (only superusers and ``BYPASSRLS`` roles are exempt), so an
  application accidentally connected as the owner fails closed instead of
  leaking.
* **Application role** – ``homayar_app`` (``NOLOGIN``, no superuser, no
  ``BYPASSRLS``).  Deployments either connect with a login role that is a
  member of it, or connect as another role and switch with ``SET ROLE`` on
  checkout (``DATABASE_APP_ROLE``).  Either way ``current_user`` is a
  non-bypass role while application SQL runs.
* **Worker / service access** – no separate role.  A worker binds
  ``homayar.job_id`` to read exactly one ``processing_jobs`` row, then binds
  the workspace it belongs to.  Two narrow service scopes
  (``homayar.service_scope``) let the sweepers *discover* rows across tenants:
  ``dispatch`` reads/updates publication bookkeeping on ``job_dispatches``
  only; ``recovery`` reads ``processing_jobs`` only (no writes).  Neither
  reaches customer content.  Nothing is reachable without a bound scope.

Context comes only from ``backend.database.tenant_context`` (server-validated).
"""

# ruff: noqa: S608 - every f-string below builds DDL from the static tables in
# this module (validated identifiers), never a data query from user input.

from __future__ import annotations

import re

from sqlalchemy import Connection, text

APP_ROLE = "homayar_app"
POLICY_NAME = "homayar_tenant_isolation"

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _setting(name: str) -> str:
    return f"NULLIF(current_setting('{name}', true), '')"


TENANT = _setting("homayar.workspace_id")
USER = _setting("homayar.user_id")
ORGANIZATION = _setting("homayar.organization_id")
JOB = _setting("homayar.job_id")
INVITATION = _setting("homayar.invitation_token_hash")
SHARE = _setting("homayar.share_token_hash")
API_KEY = _setting("homayar.api_key_hash")
SERVICE = _setting("homayar.service_scope")

# Tables whose rows belong to exactly one workspace and are never reachable
# through a share token.
WORKSPACE_TABLES: tuple[str, ...] = (
    "teams",
    "subscriptions",
    "usage_reservations",
    "usage_events",
    "processing_stage_runs",
    "stage_checkpoints",
    "ingestion_records",
    "media_objects",
    "conversation_purges",
    "transcript_redactions",
    "redacted_segments",
    "pii_mappings",
    # Derived text views and entities (ADR 0034): never through a share token.
    "transcript_text_views",
    "text_view_segments",
    "text_view_entities",
    # Judged taxonomy extractions (HOM-53): entity values can be personal data.
    "analysis_extractions",
    # Stored trend judgments (HOM-290): ids, versions and a digest of an extraction's content.
    "trend_extraction_judgments",
    "webhook_endpoints",
    # Webhook signing secrets (encrypted) and the request log (HOM-89).
    "webhook_signing_keys",
    "webhook_delivery_attempts",
    "provider_usage",
    "notifications",
    "ai_policy_versions",
    "run_policy_snapshots",
    # Published QA scorecard versions (HOM-54): never through a share token.
    "scorecard_versions",
    # P0 wave
    "service_locations",
    "voice_workspace_limits",
    "voicemail_messages",
)

# A presented share token exposes exactly one meeting's content (WP03-T06):
# the meeting behind an unrevoked, unexpired share_links row for that token.
# Expiry/revocation/audience/issuer rules are enforced again by the
# application; the database bound is the ceiling, not the policy.
SHARED_MEETING = (
    "(SELECT s.meeting_id FROM share_links s WHERE s.token_hash = "
    f"{SHARE} AND s.revoked_at IS NULL AND (s.expires_at IS NULL OR s.expires_at > now()))"
)
SHARE_WORKSPACE = f"(SELECT s.workspace_id FROM share_links s WHERE s.token_hash = {SHARE})"
SHARE_ISSUER = (
    f"(SELECT s.created_by_user_id FROM share_links s WHERE s.token_hash = {SHARE})"
)

# An effective owner/admin membership of the validated user in the bound
# organization (WP03-T03): the carrier of organization administration.
ORGANIZATION_ADMIN = (
    "EXISTS (SELECT 1 FROM organization_memberships oa WHERE oa.organization_id = "
    f"{ORGANIZATION} AND oa.user_id = {USER} AND oa.role IN ('OWNER', 'ADMIN') "
    "AND oa.is_active AND oa.revoked_at IS NULL AND oa.valid_from <= now() "
    "AND (oa.valid_until IS NULL OR oa.valid_until > now()))"
)

# Meeting content tables: tenant scope, or read-only through the shared meeting.
SHARED_CONTENT_TABLES: tuple[str, ...] = (
    "recordings",
    "meeting_speakers",
    "transcript_revisions",
    "transcript_segments",
    "analysis_runs",
    "evidence_spans",
    "meeting_summaries",
    "action_items",
)

# table -> (USING expression, WITH CHECK expression)
POLICIES: dict[str, tuple[str, str]] = {
    **{
        table: (f"workspace_id = {TENANT}", f"workspace_id = {TENANT}")
        for table in WORKSPACE_TABLES
    },
    **{
        table: (
            f"workspace_id = {TENANT} OR meeting_id IN {SHARED_MEETING}",
            f"workspace_id = {TENANT}",
        )
        for table in SHARED_CONTENT_TABLES
    },
    "meetings": (
        f"workspace_id = {TENANT} OR id IN {SHARED_MEETING}",
        f"workspace_id = {TENANT}",
    ),
    # A worker may read/claim the one job it was handed before it knows the
    # tenant. The lease-recovery sweeper (WP06-T04) discovers expired RUNNING
    # rows across tenants under the 'recovery' service scope: read-only (the
    # WITH CHECK stays tenant-bound), ids/lease columns only — every recovery
    # write then binds the job's own tenant from the persisted row.
    # Voice recovery (P0 wave) ends the live calls of a lost media node in
    # every tenant; nothing else crosses tenants on this table.
    "voice_call_sessions": (
        f"workspace_id = {TENANT} OR {SERVICE} = 'voice_recovery'",
        f"workspace_id = {TENANT} OR {SERVICE} = 'voice_recovery'",
    ),
    "processing_jobs": (
        f"workspace_id = {TENANT} OR id = {JOB} OR {SERVICE} = 'recovery'",
        f"workspace_id = {TENANT}",
    ),
    # Transactional outbox (WP06-T02): tenants write their own rows; the
    # dispatcher discovers due rows across tenants under the narrow
    # 'dispatch' service scope, which unlocks nothing else (ids only).
    "job_dispatches": (
        f"workspace_id = {TENANT} OR {SERVICE} = 'dispatch'",
        f"workspace_id = {TENANT} OR {SERVICE} = 'dispatch'",
    ),
    # Usage reconciliation (WP06-T06) discovers entitlements across tenants
    # under the read-only 'usage_reconciliation' service scope (ids, metric
    # and counters only); every reconciliation write binds the entitlement's
    # own workspace from the persisted row (WITH CHECK stays tenant-bound).
    "entitlements": (
        f"workspace_id = {TENANT} OR {SERVICE} = 'usage_reconciliation'",
        f"workspace_id = {TENANT}",
    ),
    # The webhook sweeper (HOM-89) discovers due deliveries across tenants under
    # the 'webhook_delivery' service scope.  The scope is read-only: the WITH
    # CHECK refuses every INSERT and UPDATE while it is held, even with a
    # workspace bound (the USING clause would let such a session reach another
    # tenant's row), and NO_DELETE_TABLES refuses DELETE.  Every write runs in
    # a session bound to the delivery's own workspace, without the scope.  The
    # scope reaches no endpoint, key or attempt row.
    "webhook_deliveries": (
        f"workspace_id = {TENANT} OR {SERVICE} = 'webhook_delivery'",
        f"workspace_id = {TENANT} AND {SERVICE} IS DISTINCT FROM 'webhook_delivery'",
    ),
    # Platform-level audit rows (login, OTP) carry no workspace; a share access
    # may *record* an audit row for the share's workspace but never read one.
    "audit_events": (
        f"workspace_id = {TENANT} OR workspace_id IS NULL",
        f"workspace_id = {TENANT} OR workspace_id IS NULL OR workspace_id IN {SHARE_WORKSPACE}",
    ),
    # A presented API key (WP03-T07) exposes exactly its own row before any
    # tenant is known (lookup, expiry/revocation checks, last-use stamp);
    # everything else waits for the workspace bound after the bound user's
    # membership was validated.
    "api_keys": (
        f"workspace_id = {TENANT} OR key_hash = {API_KEY}",
        f"workspace_id = {TENANT} OR key_hash = {API_KEY}",
    ),
    # Token-addressed rows are visible through the presented token only.
    "share_links": (
        f"workspace_id = {TENANT} OR token_hash = {SHARE}",
        f"workspace_id = {TENANT} OR token_hash = {SHARE}",
    ),
    "share_recipients": (
        f"workspace_id = {TENANT} OR share_id IN "
        f"(SELECT s.id FROM share_links s WHERE s.token_hash = {SHARE})",
        f"workspace_id = {TENANT} OR share_id IN "
        f"(SELECT s.id FROM share_links s WHERE s.token_hash = {SHARE})",
    ),
    "workspace_invitations": (
        f"workspace_id = {TENANT} OR token_hash = {INVITATION}",
        f"workspace_id = {TENANT} OR token_hash = {INVITATION}",
    ),
    # Tenancy bootstrap: a user sees their own memberships before a workspace
    # is selected; membership rows are created inside the target workspace.
    # A share access may additionally see the issuer's membership and team
    # assignments in the share's workspace to re-check the issuer's authority.
    "memberships": (
        f"workspace_id = {TENANT} OR user_id = {USER} OR "
        f"(workspace_id IN {SHARE_WORKSPACE} AND user_id = {SHARE_ISSUER})",
        f"workspace_id = {TENANT}",
    ),
    "team_assignments": (
        f"workspace_id = {TENANT} OR "
        f"(workspace_id IN {SHARE_WORKSPACE} AND user_id = {SHARE_ISSUER})",
        f"workspace_id = {TENANT}",
    ),
    # A bound organization (WP03-T03) additionally exposes its workspaces to
    # effective organization owners/admins and to holders of an active scope
    # grant on the workspace; a workspace is written only as the bound tenant.
    "workspaces": (
        f"id = {TENANT} OR id IN {SHARE_WORKSPACE} OR EXISTS (SELECT 1 FROM memberships m "
        f"WHERE m.workspace_id = workspaces.id AND m.user_id = {USER}) OR "
        f"(organization_id = {ORGANIZATION} AND ({ORGANIZATION_ADMIN} OR "
        f"EXISTS (SELECT 1 FROM organization_scope_grants g JOIN organization_memberships om "
        f"ON om.id = g.membership_id WHERE g.workspace_id = workspaces.id "
        f"AND g.revoked_at IS NULL AND om.user_id = {USER})))",
        f"id = {TENANT}",
    ),
    "organizations": (
        f"id = {ORGANIZATION} OR EXISTS (SELECT 1 FROM workspaces w "
        f"WHERE w.organization_id = organizations.id) OR EXISTS (SELECT 1 FROM "
        f"organization_memberships om WHERE om.organization_id = organizations.id "
        f"AND om.user_id = {USER})",
        f"id = {ORGANIZATION}",
    ),
    # Organization membership rows: the bound organization's, or one's own
    # (listing one's organizations before one is selected).  Written only for
    # the bound organization.
    "organization_memberships": (
        f"organization_id = {ORGANIZATION} OR user_id = {USER}",
        f"organization_id = {ORGANIZATION}",
    ),
    # Scope grants: the bound organization's only.  The workspace a grant
    # points at is validated by the application (organization-filtered
    # lookup); a policy subquery on ``workspaces`` here would recurse with the
    # ``workspaces`` policy above, and a stray row pointing outside the
    # organization exposes nothing anyway: the ``workspaces`` clause requires
    # the workspace to belong to the bound organization.
    "organization_scope_grants": (
        f"organization_id = {ORGANIZATION}",
        f"organization_id = {ORGANIZATION}",
    ),
}

# The ``workspaces`` / ``organizations`` expressions before the organization
# carrier existed (revision a2f7c4e9d1b3).  ``enable_policy_statements`` uses
# them while ``organization_memberships`` is absent, so earlier migrations that
# (re)apply the policies keep working on a schema that predates the carrier,
# and the downgrade of a2f7c4e9d1b3 restores them.
PRE_ORGANIZATION_POLICIES: dict[str, tuple[str, str]] = {
    "workspaces": (
        f"id = {TENANT} OR id IN {SHARE_WORKSPACE} OR EXISTS (SELECT 1 FROM memberships m "
        f"WHERE m.workspace_id = workspaces.id AND m.user_id = {USER})",
        f"id = {TENANT}",
    ),
    "organizations": (
        f"id = {ORGANIZATION} OR EXISTS (SELECT 1 FROM workspaces w "
        f"WHERE w.organization_id = organizations.id)",
        f"id = {ORGANIZATION}",
    ),
}
ORGANIZATION_CARRIER_TABLE = "organization_memberships"

# Deliberately unprotected: authentication/bootstrap and global catalog tables.
UNPROTECTED_TABLES: frozenset[str] = frozenset(
    {
        "users",
        "auth_identities",
        "login_sessions",
        "one_time_tokens",
        "oauth_challenges",
        "otp_challenges",
        "plans",
        "plan_versions",
        "model_manifests",
        "provider_health",
        "alembic_version",
        # Deployment-wide voice capacity (P0 wave): counts must span tenants;
        # rows hold node ids, generations and slot leases, no content.
        "voice_media_nodes",
        "voice_admission_locks",
        "voice_capacity_slots",
    }
)


# Append-only tables (WP08-T01/T02): the application role may read and insert
# but never update or delete rows, whatever the ORM guards do.  Referential
# cascades (workspace removal) run as the table owner and are unaffected.
# ``audit_events`` (HOM-115, ADR 0036): history cannot be rewritten by the
# application; the user/workspace ``ON DELETE SET NULL`` still runs as owner.
# ``scorecard_versions`` (HOM-54): a published scorecard version never changes.
# ``webhook_delivery_attempts`` (HOM-89): one row per HTTP request sent, never
# rewritten, so the ledger shows every request that left.
APPEND_ONLY_TABLES: tuple[str, ...] = (
    "model_manifests",
    "ai_policy_versions",
    "run_policy_snapshots",
    "plan_versions",
    "audit_events",
    "scorecard_versions",
    "webhook_delivery_attempts",
)

# Tables the application role may read, insert and update but never delete.
# ``webhook_deliveries`` (HOM-89) is the outbound delivery ledger: the
# application never deletes a ledger row.  The missing grant also keeps the
# 'webhook_delivery' scope, which the FOR ALL policy's USING clause lets see
# every tenant's rows, from deleting them.  Workspace and endpoint cascades run
# as the table owner and are unaffected.
NO_DELETE_TABLES: tuple[str, ...] = ("webhook_deliveries",)

# Protected audit sink reader (HOM-115, ADR 0036): a NOLOGIN role with SELECT on
# ``audit_events`` only, across tenants through a policy granted to it alone.
# ``homayar_app`` is never a member and the application's login role must not be
# one either (SET ROLE checks the session user's memberships); the exporter logs
# in with its own role that is a member.  The tenant policy and its WITH CHECK
# are unchanged; the reader holds no INSERT/UPDATE/DELETE privilege.
AUDIT_READER_ROLE = "homayar_audit_reader"
AUDIT_READER_POLICY = "homayar_audit_export"
AUDIT_TABLE = "audit_events"


def validate_role_name(role: str) -> str:
    if not _IDENTIFIER.fullmatch(role):
        raise ValueError(f"Invalid PostgreSQL role name: {role!r}")
    return role


def ensure_app_role_statements(role: str = APP_ROLE) -> list[str]:
    role = validate_role_name(role)
    return [
        (
            "DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
            f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE; "
            "END IF; END $$;"
        ),
        f"GRANT USAGE ON SCHEMA public TO {role}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",
        (
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"
        ),
    ]


def audit_reader_statements(
    role: str = AUDIT_READER_ROLE, *, app_role: str = APP_ROLE
) -> list[str]:
    """Create the sink reader role, its SELECT grant and its read-only policy.

    A role of that name that already exists (pre-created by a DBA, or left by an
    earlier install) is made inert: ``NOLOGIN`` without a password, no superuser,
    ``BYPASSRLS``, ``CREATEDB`` or ``CREATEROLE``, a member of no role (it would
    inherit that role's privileges), and ``app_role`` is no member of it.  Only the
    changes it needs are issued, so a migration role without SUPERUSER runs this
    when nothing is wrong; a change it may not make fails the migration.  The last
    statement refuses to continue while the role is not inert (an indirect path from
    ``app_role`` included).
    """
    role = validate_role_name(role)
    app_role = validate_role_name(app_role)
    return [
        (
            "DO $$ DECLARE reader pg_roles%ROWTYPE; grant_row record; BEGIN "
            f"SELECT * INTO reader FROM pg_roles WHERE rolname = '{role}'; "
            "IF NOT FOUND THEN "
            f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE; "
            "ELSE "
            "IF reader.rolcanlogin OR reader.rolcreatedb OR reader.rolcreaterole THEN "
            f"ALTER ROLE {role} NOLOGIN NOCREATEDB NOCREATEROLE PASSWORD NULL; "
            "END IF; "
            f"IF reader.rolsuper THEN ALTER ROLE {role} NOSUPERUSER; END IF; "
            f"IF reader.rolbypassrls THEN ALTER ROLE {role} NOBYPASSRLS; END IF; "
            # every membership grant of the reader, and of app_role in the reader,
            # with the grantor PostgreSQL recorded for it
            "FOR grant_row IN SELECT g.rolname AS granted, r.rolname AS member, "
            "o.rolname AS grantor FROM pg_auth_members m "
            "JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles r ON r.oid = m.member "
            "JOIN pg_roles o ON o.oid = m.grantor "
            f"WHERE r.rolname = '{role}' OR (g.rolname = '{role}' AND r.rolname = '{app_role}') "
            "LOOP "
            "EXECUTE format('REVOKE %I FROM %I GRANTED BY %I', "
            "grant_row.granted, grant_row.member, grant_row.grantor); "
            "END LOOP; "
            "END IF; END $$;"
        ),
        f"GRANT USAGE ON SCHEMA public TO {role}",
        f"REVOKE ALL ON {AUDIT_TABLE} FROM {role}",
        f"GRANT SELECT ON {AUDIT_TABLE} TO {role}",
        f"DROP POLICY IF EXISTS {AUDIT_READER_POLICY} ON {AUDIT_TABLE}",
        f"CREATE POLICY {AUDIT_READER_POLICY} ON {AUDIT_TABLE} AS PERMISSIVE FOR SELECT "
        f"TO {role} USING (true)",
        (
            "DO $$ BEGIN "
            f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}' AND (rolcanlogin "
            "OR rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole)) "
            "OR EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member "
            f"WHERE r.rolname = '{role}') "
            f"OR pg_has_role('{app_role}', '{role}', 'MEMBER') THEN "
            f"RAISE EXCEPTION '{role} is not inert: it can log in, holds a role attribute, is "
            f"a member of another role, or {app_role} reaches it through a membership; "
            "remove that before this migration (docs/operations/audit.md#the-audit-reader-role)'; "
            "END IF; END $$;"
        ),
    ]


def remove_audit_reader_statements(role: str = AUDIT_READER_ROLE) -> list[str]:
    """Undo ``audit_reader_statements`` in this database.

    The role is cluster-wide: it is dropped only when no other database still grants
    it anything and the migration role may drop roles (CREATEROLE); otherwise it
    stays, ``NOLOGIN`` and without privileges in this database."""
    role = validate_role_name(role)
    return [
        f"DROP POLICY IF EXISTS {AUDIT_READER_POLICY} ON {AUDIT_TABLE}",
        (
            "DO $$ BEGIN "
            f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
            f"REVOKE ALL ON {AUDIT_TABLE} FROM {role}; "
            f"REVOKE USAGE ON SCHEMA public FROM {role}; "
            "BEGIN "
            f"DROP ROLE {role}; "
            "EXCEPTION WHEN dependent_objects_still_exist OR insufficient_privilege "
            "THEN NULL; "
            "END; "
            "END IF; END $$;"
        ),
    ]


def enable_policy_statements(tables: tuple[str, ...] | None = None) -> list[str]:
    """Statements enabling, forcing and (re)creating the policy of every table.

    ``tables`` limits the set to tables that exist (migrations add tables
    over time); while the organization carrier is not among them, the
    ``workspaces`` / ``organizations`` policies use their pre-organization
    expressions so the statements never reference a missing table.
    """
    statements: list[str] = []
    carrier_missing = tables is not None and ORGANIZATION_CARRIER_TABLE not in tables
    for table, (using, check) in POLICIES.items():
        if tables is not None and table not in tables:
            continue
        if carrier_missing and table in PRE_ORGANIZATION_POLICIES:
            using, check = PRE_ORGANIZATION_POLICIES[table]
        statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        statements.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        statements.append(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        statements.append(
            f"CREATE POLICY {POLICY_NAME} ON {table} AS PERMISSIVE FOR ALL TO PUBLIC "
            f"USING ({using}) WITH CHECK ({check})"
        )
    return statements


def disable_policy_statements(tables: tuple[str, ...] | None = None) -> list[str]:
    statements: list[str] = []
    for table in POLICIES:
        if tables is not None and table not in tables:
            continue
        statements.append(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        statements.append(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        statements.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    return statements


def existing_policy_tables(connection: Connection) -> tuple[str, ...]:
    """Policy tables that exist in the current schema (migrations add tables over time)."""
    present = []
    for table in POLICIES:
        exists = connection.execute(
            text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table}"}
        ).scalar_one()
        if exists:
            present.append(table)
    return tuple(present)


def apply_row_level_security(connection: Connection, *, role: str = APP_ROLE) -> None:
    """Idempotently create the app role, grants and policies (PostgreSQL only).

    Policies are (re)created for every policy table that exists, so a later
    migration that adds a table or changes an expression simply calls this
    again after its DDL.
    """
    if connection.dialect.name != "postgresql":
        return
    tables = existing_policy_tables(connection)
    for statement in ensure_app_role_statements(role) + enable_policy_statements(tables):
        connection.execute(text(statement))
    role = validate_role_name(role)
    for table in APPEND_ONLY_TABLES:
        if connection.execute(
            text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table}"}
        ).scalar_one():
            connection.execute(text(f"REVOKE UPDATE, DELETE ON {table} FROM {role}"))
    for table in NO_DELETE_TABLES:
        if table in tables:
            connection.execute(text(f"REVOKE DELETE ON {table} FROM {role}"))
    if AUDIT_TABLE in tables:
        for statement in audit_reader_statements(app_role=role):
            connection.execute(text(statement))


def remove_row_level_security(connection: Connection) -> None:
    if connection.dialect.name != "postgresql":
        return
    for statement in disable_policy_statements(existing_policy_tables(connection)):
        connection.execute(text(statement))


def describe_current_role(connection: Connection) -> dict[str, object]:
    """Effective privileges of ``current_user`` and RLS state of ``meetings``."""
    role = connection.execute(
        text(
            "SELECT current_user AS name, rolsuper, rolbypassrls "
            "FROM pg_roles WHERE rolname = current_user"
        )
    ).mappings().one()
    table = connection.execute(
        text(
            "SELECT relrowsecurity, relforcerowsecurity, "
            "pg_get_userbyid(relowner) = current_user AS is_owner "
            "FROM pg_class WHERE relname = 'meetings' AND relkind = 'r'"
        )
    ).mappings().first()
    return {
        "role": role["name"],
        "superuser": bool(role["rolsuper"]),
        "bypassrls": bool(role["rolbypassrls"]),
        "rls_enabled": bool(table and table["relrowsecurity"]),
        "rls_forced": bool(table and table["relforcerowsecurity"]),
        "is_owner": bool(table and table["is_owner"]),
    }


class RowLevelSecurityPostureError(RuntimeError):
    pass


def assert_production_posture(connection: Connection) -> dict[str, object]:
    """Refuse to serve when the runtime role could bypass tenant isolation."""
    posture = describe_current_role(connection)
    problems = []
    if posture["superuser"]:
        problems.append("runtime role is a superuser")
    if posture["bypassrls"]:
        problems.append("runtime role has BYPASSRLS")
    if not posture["rls_enabled"]:
        problems.append("row-level security is not enabled on tenant tables")
    if problems:
        raise RowLevelSecurityPostureError(
            "Refusing to start: " + "; ".join(problems) + f" (role={posture['role']})"
        )
    return posture
