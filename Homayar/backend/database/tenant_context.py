"""Transaction-scoped tenant context for PostgreSQL row-level security (WP03-T05).

The application never trusts a client-supplied tenant id.  After the request
(or worker) has *validated* who is acting and on which workspace, it binds that
fact to the SQLAlchemy ``Session``:

    bind_user_context(db, user_id)                 # after session validation
    bind_tenant_context(db, workspace_id=..., organization_id=...)
    bind_job_context(db, job_id)                   # worker, before claiming
    bind_capability_context(db, invitation_token_hash=...)  # token-addressed rows

Binding stores the values in ``session.info`` and pushes them into PostgreSQL
with ``set_config(name, value, is_local => true)``.  ``is_local`` makes the
value **transaction-scoped**: it disappears at COMMIT/ROLLBACK, so a pooled
connection can never carry one tenant's context into another request.  Because
SQLAlchemy sessions release their connection on commit and begin a new
transaction on the next statement, a ``Session`` event re-applies the stored
context at the start of every transaction.  A session without a bound context
sees no tenant rows at all (deny by default); it does not fall back to any
default tenant.

Policies read the values through ``NULLIF(current_setting(name, true), '')``
so an unset or reset variable behaves as NULL rather than matching anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, event, text
from sqlalchemy.orm import Session, sessionmaker

SESSION_INFO_KEY = "homayar_tenant_context"

GUC_USER_ID = "homayar.user_id"
GUC_WORKSPACE_ID = "homayar.workspace_id"
GUC_ORGANIZATION_ID = "homayar.organization_id"
GUC_JOB_ID = "homayar.job_id"
GUC_INVITATION_TOKEN_HASH = "homayar.invitation_token_hash"  # noqa: S105 - setting name
GUC_SHARE_TOKEN_HASH = "homayar.share_token_hash"  # noqa: S105 - setting name
# A presented API key (WP03-T07): exposes exactly its own api_keys row before
# the tenant is known; the workspace is bound after the bound user's
# membership was validated, like any session.
GUC_API_KEY_HASH = "homayar.api_key_hash"  # noqa: S105 - setting name
# Narrow service authority for cross-tenant *bookkeeping* tables only (the
# dispatch outbox, lease discovery on processing_jobs); never exposes
# customer content and never permits writes.
GUC_SERVICE_SCOPE = "homayar.service_scope"

SERVICE_SCOPE_DISPATCH = "dispatch"
SERVICE_SCOPE_RECOVERY = "recovery"
# Usage reconciliation (WP06-T06): discovers entitlements across tenants
# (ids, workspace, metric — no customer content, no writes); every repair
# then binds the workspace read from the persisted entitlement row.
SERVICE_SCOPE_USAGE_RECONCILIATION = "usage_reconciliation"

ALL_GUCS: tuple[str, ...] = (
    GUC_USER_ID,
    GUC_WORKSPACE_ID,
    GUC_ORGANIZATION_ID,
    GUC_JOB_ID,
    GUC_INVITATION_TOKEN_HASH,
    GUC_SHARE_TOKEN_HASH,
    GUC_API_KEY_HASH,
    GUC_SERVICE_SCOPE,
)

_SET_CONFIG = text("SELECT set_config(:name, :value, true)")


@dataclass(frozen=True)
class TenantContext:
    user_id: str | None = None
    workspace_id: str | None = None
    organization_id: str | None = None
    job_id: str | None = None
    invitation_token_hash: str | None = None
    share_token_hash: str | None = None
    api_key_hash: str | None = None
    service_scope: str | None = None

    def as_settings(self) -> dict[str, str]:
        values = {
            GUC_USER_ID: self.user_id,
            GUC_WORKSPACE_ID: self.workspace_id,
            GUC_ORGANIZATION_ID: self.organization_id,
            GUC_JOB_ID: self.job_id,
            GUC_INVITATION_TOKEN_HASH: self.invitation_token_hash,
            GUC_SHARE_TOKEN_HASH: self.share_token_hash,
            GUC_API_KEY_HASH: self.api_key_hash,
            GUC_SERVICE_SCOPE: self.service_scope,
        }
        return {name: value for name, value in values.items() if value}


def current_context(session: Session) -> TenantContext:
    return session.info.get(SESSION_INFO_KEY) or TenantContext()


def _is_postgres(connection: Connection) -> bool:
    return connection.dialect.name == "postgresql"


def apply_context(connection: Connection, context: TenantContext) -> None:
    """Push ``context`` into the *current* transaction of ``connection``."""
    if not _is_postgres(connection):
        return
    values = context.as_settings()
    for name in ALL_GUCS:
        connection.execute(_SET_CONFIG, {"name": name, "value": values.get(name, "")})


def _store(session: Session, context: TenantContext) -> None:
    session.info[SESSION_INFO_KEY] = context
    if session.in_transaction():
        apply_context(session.connection(), context)


def bind_user_context(session: Session, user_id: str) -> None:
    _store(session, _replace(current_context(session), user_id=user_id))


def bind_tenant_context(
    session: Session, *, workspace_id: str, organization_id: str | None = None
) -> None:
    _store(
        session,
        _replace(
            current_context(session),
            workspace_id=workspace_id,
            organization_id=organization_id,
        ),
    )


def bind_organization_context(session: Session, organization_id: str) -> None:
    """Organization-level request (WP03-T03): no workspace is selected; the
    policies admit organization rows, memberships and the workspaces the
    validated user may know about, nothing workspace-scoped."""
    _store(session, _replace(current_context(session), organization_id=organization_id))


def bind_job_context(session: Session, job_id: str) -> None:
    _store(session, _replace(current_context(session), job_id=job_id))


def bind_capability_context(
    session: Session,
    *,
    invitation_token_hash: str | None = None,
    share_token_hash: str | None = None,
    api_key_hash: str | None = None,
) -> None:
    _store(
        session,
        _replace(
            current_context(session),
            invitation_token_hash=invitation_token_hash,
            share_token_hash=share_token_hash,
            api_key_hash=api_key_hash,
        ),
    )


def bind_service_scope(session: Session, scope: str) -> None:
    """Grant a session one narrow service scope (e.g. the dispatch outbox)."""
    _store(session, _replace(current_context(session), service_scope=scope))


def clear_context(session: Session) -> None:
    _store(session, TenantContext())


def _replace(context: TenantContext, **changes: Any) -> TenantContext:
    data = {
        "user_id": context.user_id,
        "workspace_id": context.workspace_id,
        "organization_id": context.organization_id,
        "job_id": context.job_id,
        "invitation_token_hash": context.invitation_token_hash,
        "share_token_hash": context.share_token_hash,
        "api_key_hash": context.api_key_hash,
        "service_scope": context.service_scope,
    }
    for key, value in changes.items():
        if value is not None:
            data[key] = value
    return TenantContext(**data)


def install_session_listeners(factory: sessionmaker[Session]) -> None:
    """Re-apply the stored context whenever a session starts a new transaction."""

    @event.listens_for(factory, "after_begin")
    def _reapply(session: Session, _transaction, connection: Connection) -> None:
        context = session.info.get(SESSION_INFO_KEY)
        if context is not None:
            apply_context(connection, context)
