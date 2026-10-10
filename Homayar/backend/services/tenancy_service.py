from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.saas_models import (
    Entitlement,
    Membership,
    Organization,
    Plan,
    PlanVersion,
    Subscription,
    SubscriptionStatus,
    User,
    Workspace,
    WorkspaceRole,
    new_id,
)
from backend.database.tenant_context import bind_tenant_context, bind_user_context
from backend.security.authorization import selectable_workspace_clause
from backend.security.normalization import normalize_workspace_slug
from backend.services.logging_service import logger
from backend.services.organization_service import bootstrap_owner_membership

PLAN_CATALOG = (
    {
        "key": "demo",
        "name": "Free Demo",
        "is_public": True,
        "entitlements": {"audio_seconds": 1800, "concurrent_jobs": 1, "seats": 1},
    },
    {
        "key": "professional",
        "name": "Professional",
        "is_public": True,
        "entitlements": {},
    },
    {
        "key": "business",
        "name": "Business",
        "is_public": True,
        "entitlements": {},
    },
    {
        "key": "enterprise",
        "name": "Enterprise",
        "is_public": False,
        "entitlements": {},
    },
    {
        "key": "on_premise",
        "name": "On-Premise",
        "is_public": False,
        "entitlements": {},
    },
)


DEMO_PLAN_KEY = "demo"
# Metrics the usage ledger meters: the only entitlement keys a plan version
# provisions as Entitlement rows (other keys, such as "seats", are not metered).
METERED_METRICS: tuple[str, ...] = ("audio_seconds", "concurrent_jobs")
# Sources a caller may publish; catalog_seed and legacy_snapshot are written
# only by the seed and the migration.
PUBLISH_SOURCES = frozenset({"operator", "config"})
ENTITLEMENT_KEY = re.compile(r"[a-z][a-z0-9_]{0,59}")
MAX_ENTITLEMENT_VALUE = 2_147_483_647  # entitlements.limit_value is a 32-bit INTEGER
PLAN_NAME_MAX_LENGTH = 100
_INSERT_BY_DIALECT = {"postgresql": postgresql.insert, "sqlite": sqlite.insert}
_MISSING = object()


class PlanCatalogError(ValueError):
    """A plan version was refused; nothing was written."""


def ensure_plan_catalog(db: Session, settings: Settings | None = None) -> None:
    """Startup seed of the plan catalog (WP18-T03, defect D-12).

    ``PLAN_CATALOG`` owns only the *existence* of its keys: a missing key is
    inserted with the code defaults and version 1 (``catalog_seed``).  An
    existing row is commercial data and is never rewritten.  The one exception
    is the demo allowance, owned by ``DEMO_QUOTA_SECONDS``: when it differs, a
    ``config`` version is appended.  A row whose versioned terms differ from
    its current version (a direct SQL edit) is reported as
    ``PLAN_CATALOG_DRIFT``, not changed.  Flushes, never commits.
    """
    settings = settings or get_settings()
    _insert_missing_catalog_plans(db, settings)
    _report_catalog_drift(db)
    _apply_demo_quota(db, settings.demo_quota_seconds)


def publish_plan_version(
    db: Session,
    plan_key: str,
    *,
    entitlements: dict[str, int | None],
    name: str | None = None,
    is_public: bool | None = None,
    source: str = "operator",
) -> PlanVersion:
    """Append the next version of a plan and make it current.

    The one sanctioned write path for a plan's terms.  ``entitlements`` is
    the complete dict of the new version.  ``name`` and ``is_public`` default
    to the row's current values.  ``is_active`` and ``display_json`` are not
    versioned and stay unchanged.  Existing subscriptions keep their pinned
    version.  The demo plan's ``audio_seconds`` belongs to
    ``DEMO_QUOTA_SECONDS``, so an operator publish must repeat the current
    value.  Raises ``PlanCatalogError`` before writing anything.  Flushes,
    never commits.
    """
    if not isinstance(source, str) or source not in PUBLISH_SOURCES:
        raise PlanCatalogError(f"source must be one of {sorted(PUBLISH_SOURCES)}")
    terms = _validated_entitlements(entitlements)
    if name is not None and (
        not isinstance(name, str) or not name.strip() or len(name) > PLAN_NAME_MAX_LENGTH
    ):
        raise PlanCatalogError(
            f"name must be a non-blank string of at most {PLAN_NAME_MAX_LENGTH} characters"
        )
    if is_public is not None and not isinstance(is_public, bool):
        raise PlanCatalogError("is_public must be a bool")
    plan = _lock_plan(db, plan_key) if isinstance(plan_key, str) else None
    if plan is None:
        raise PlanCatalogError(f"Unknown plan: {plan_key!r}")
    if plan.key == DEMO_PLAN_KEY and source == "operator":
        current = plan.entitlements_json if isinstance(plan.entitlements_json, dict) else {}
        if "audio_seconds" not in terms or not _same_value(
            terms["audio_seconds"], current.get("audio_seconds", _MISSING)
        ):
            raise PlanCatalogError(
                "The demo plan's audio_seconds is owned by DEMO_QUOTA_SECONDS; change that "
                "setting and restart instead, and repeat the current value when publishing"
            )
    return _append_version(
        db,
        plan,
        name=plan.name if name is None else name,
        is_public=plan.is_public if is_public is None else is_public,
        entitlements=terms,
        source=source,
    )


def _same_value(left: object, right: object) -> bool:
    # Strict: True is not 1 and 1.0 is not 1.
    return type(left) is type(right) and left == right


def _validated_entitlements(entitlements: object) -> dict[str, int | None]:
    if not isinstance(entitlements, dict):
        raise PlanCatalogError("entitlements must be a dict of metric -> limit")
    validated: dict[str, int | None] = {}
    for key, value in entitlements.items():
        if not isinstance(key, str) or not ENTITLEMENT_KEY.fullmatch(key):
            raise PlanCatalogError(
                "entitlement keys must be lower-case identifiers of at most 60 characters"
            )
        if value is not None and not _is_limit(value):
            raise PlanCatalogError(
                f"entitlement {key} must be an integer from 0 to {MAX_ENTITLEMENT_VALUE}, "
                "or None for unlimited"
            )
        validated[key] = value
    return validated


def _is_limit(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_ENTITLEMENT_VALUE


def _catalog_entitlements(item: dict[str, Any], settings: Settings) -> dict[str, Any]:
    entitlements = dict(item["entitlements"])
    if item["key"] == DEMO_PLAN_KEY:
        entitlements["audio_seconds"] = settings.demo_quota_seconds
    return entitlements


def _insert_missing_catalog_plans(db: Session, settings: Settings) -> list[str]:
    """Insert each catalog key that has no row, with its version 1.

    Insert-only and race-safe: ``ON CONFLICT DO NOTHING`` waits for a
    concurrent transaction inserting the same key and then skips it.  An
    existing row is never locked or rewritten.
    """
    keys = [item["key"] for item in PLAN_CATALOG]
    existing = set(db.scalars(select(Plan.key).where(Plan.key.in_(keys))))
    insert = _INSERT_BY_DIALECT[db.get_bind().dialect.name]
    inserted: list[str] = []
    for item in PLAN_CATALOG:
        if item["key"] in existing:
            continue
        entitlements = _catalog_entitlements(item, settings)
        created = db.execute(
            insert(Plan.__table__)
            .values(
                key=item["key"],
                name=item["name"],
                is_public=item["is_public"],
                is_active=True,
                entitlements_json=entitlements,
                display_json={},
                current_version=1,
            )
            .on_conflict_do_nothing(index_elements=["key"])
            .returning(Plan.__table__.c.key)
        ).scalar_one_or_none()
        if created is None:
            continue  # inserted concurrently by another transaction
        db.add(
            PlanVersion(
                plan_key=item["key"],
                version=1,
                name=item["name"],
                is_public=item["is_public"],
                entitlements_json=dict(entitlements),
                source="catalog_seed",
            )
        )
        inserted.append(item["key"])
    if inserted:
        db.flush()
    return inserted


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _drifted_fields(plan: Plan, version: PlanVersion | None) -> list[str]:
    if version is None:
        return ["name", "is_public", "entitlements_json"]
    fields = []
    if plan.name != version.name:
        fields.append("name")
    if plan.is_public != version.is_public:
        fields.append("is_public")
    if _canonical(plan.entitlements_json) != _canonical(version.entitlements_json):
        fields.append("entitlements_json")
    return fields


def _report_catalog_drift(db: Session) -> None:
    """Warn about rows whose versioned terms differ from their current version.

    Direct SQL edits persist but are not versions: new sign-ups keep getting the
    current version's terms.  Field names only, never values.
    """
    rows = db.execute(
        select(Plan, PlanVersion)
        .outerjoin(
            PlanVersion,
            and_(PlanVersion.plan_key == Plan.key, PlanVersion.version == Plan.current_version),
        )
        .order_by(Plan.key)
    ).all()
    for plan, version in rows:
        fields = _drifted_fields(plan, version)
        if fields:
            logger.warning("PLAN_CATALOG_DRIFT plan=%s fields=%s", plan.key, ",".join(fields))


def _lock_plan(db: Session, plan_key: str) -> Plan | None:
    """The plan row under ``FOR NO KEY UPDATE``, refreshed from the database.

    Serializes version appends per plan without waiting for sign-ups, whose
    subscription foreign key holds only ``FOR KEY SHARE`` on the row.
    ``plan_versions`` rows are never locked or updated (the application role
    holds no UPDATE grant on them).
    """
    db.flush()
    return db.scalars(
        select(Plan)
        .where(Plan.key == plan_key)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    ).one_or_none()


def _current_version(db: Session, plan: Plan) -> PlanVersion | None:
    return db.get(PlanVersion, (plan.key, plan.current_version))


def _quota_applied(plan: Plan, version: PlanVersion | None, quota: int) -> bool:
    def carries(terms: object) -> bool:
        return isinstance(terms, dict) and _same_value(terms.get("audio_seconds"), quota)

    return carries(plan.entitlements_json) and (
        version is None or carries(version.entitlements_json)
    )


def _apply_demo_quota(db: Session, quota: int) -> None:
    """Append a ``config`` demo version when ``DEMO_QUOTA_SECONDS`` changed.

    Every other term is carried from the row as it is.  Checked without a lock
    first, so an unchanged setting writes and locks nothing; re-checked under
    the row lock, so concurrent starts append exactly one version.
    """
    plan = db.get(Plan, DEMO_PLAN_KEY)
    if plan is None or _quota_applied(plan, _current_version(db, plan), quota):
        return
    plan = _lock_plan(db, DEMO_PLAN_KEY)
    if plan is None or _quota_applied(plan, _current_version(db, plan), quota):
        return
    entitlements = dict(plan.entitlements_json) if isinstance(plan.entitlements_json, dict) else {}
    entitlements["audio_seconds"] = quota
    _append_version(
        db,
        plan,
        name=plan.name,
        is_public=plan.is_public,
        entitlements=entitlements,
        source="config",
    )


def _append_version(
    db: Session,
    plan: Plan,
    *,
    name: str,
    is_public: bool,
    entitlements: dict[str, Any],
    source: str,
) -> PlanVersion:
    """Append the plan's next version to a plan row locked by the caller.

    Numbered after the latest stored version, not ``current_version``: a direct
    SQL edit may have moved ``current_version`` back to an earlier version.
    """
    latest = db.scalar(
        select(func.max(PlanVersion.version)).where(PlanVersion.plan_key == plan.key)
    )
    version = PlanVersion(
        plan_key=plan.key,
        version=(latest or 0) + 1,
        name=name,
        is_public=is_public,
        entitlements_json=dict(entitlements),
        source=source,
    )
    db.add(version)
    plan.name = name
    plan.is_public = is_public
    plan.entitlements_json = dict(entitlements)
    plan.current_version = version.version
    db.flush()
    logger.info(
        "PLAN_VERSION_PUBLISHED plan=%s version=%d source=%s", plan.key, version.version, source
    )
    return version


def _grant_demo(db: Session, *, workspace_id: str, now: datetime) -> None:
    """The free demo: a subscription pinned to the demo plan's current version.

    Granted only while the demo plan is active: retiring it (``is_active =
    false``) stops the grant as well as the public listing.  Entitlement rows
    come from that version's metered metrics.  JSON null is an unlimited limit;
    a metric the version does not define gets no row, so its use is refused
    (fail closed).
    """
    plan = db.get(Plan, DEMO_PLAN_KEY)
    if plan is not None and not plan.is_active:
        logger.info("DEMO_GRANT_SKIPPED plan=%s reason=retired", DEMO_PLAN_KEY)
        return
    version = None if plan is None else db.get(PlanVersion, (plan.key, plan.current_version))
    if version is None:
        logger.warning("DEMO_GRANT_SKIPPED plan=%s reason=no_current_version", DEMO_PLAN_KEY)
        return
    db.add(
        Subscription(
            workspace_id=workspace_id,
            plan_key=DEMO_PLAN_KEY,
            plan_version=version.version,
            status=SubscriptionStatus.TRIALING,
            provider="internal",
            starts_at=now,
        )
    )
    terms = version.entitlements_json if isinstance(version.entitlements_json, dict) else {}
    for metric in METERED_METRICS:
        if metric not in terms:
            continue
        limit = terms[metric]
        if limit is not None and not _is_limit(limit):
            logger.warning(
                "DEMO_GRANT_METRIC_SKIPPED plan=%s version=%d metric=%s",
                DEMO_PLAN_KEY,
                version.version,
                metric,
            )
            continue
        db.add(
            Entitlement(
                workspace_id=workspace_id,
                metric=metric,
                source="demo",
                limit_value=limit,
                used_value=0,
                reserved_value=0,
                period_start=now,
                period_end=None,
            )
        )


def create_personal_workspace(
    db: Session,
    *,
    user: User,
    name: str | None = None,
    settings: Settings | None = None,
) -> Workspace:
    settings = settings or get_settings()
    # Sign-up may create a missing catalog key but never modifies the catalog.
    _insert_missing_catalog_plans(db, settings)
    workspace_name = (name or user.display_name or "My Workspace").strip()[:180]
    base_slug = normalize_workspace_slug(workspace_name)
    unique_suffix = secrets.token_hex(3)
    organization = Organization(
        id=new_id(),
        name=workspace_name,
        slug=f"{base_slug}-{unique_suffix}",
        status="active",
    )
    workspace = Workspace(
        id=new_id(),
        organization=organization,
        name=workspace_name,
        slug="default",
        status="active",
        retention_days=settings.retention_days_default,
    )
    # The new tenant's ids are generated here, so the transaction can be scoped
    # to them before the first row is written (row-level security WITH CHECK).
    bind_user_context(db, user.id)
    bind_tenant_context(db, workspace_id=workspace.id, organization_id=organization.id)
    db.add(organization)
    db.flush()
    db.add(Membership(workspace=workspace, user=user, role=WorkspaceRole.OWNER))
    db.flush()
    # The creator owns the organization as well (WP03-T03, ADR 0012); this is
    # the only organization role a bootstrap ever grants.
    bootstrap_owner_membership(db, organization=organization, user=user)

    if settings.enable_free_demo:
        _grant_demo(db, workspace_id=workspace.id, now=datetime.now(UTC))
    db.flush()
    return workspace


def list_user_workspaces(
    db: Session, user_id: str, *, now: datetime | None = None
) -> list[tuple[Workspace, Membership]]:
    """The workspaces a request of ``user_id`` may select right now.

    Same predicate as ``require_workspace`` (``selectable_workspace_clause``):
    an inactive, not-yet-valid or expired membership and a workspace that is
    not ``active`` are left out, so the session never lists a workspace that
    every request would refuse with ``workspace_access_denied``.
    """
    rows = db.execute(
        select(Workspace, Membership)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(
            Membership.user_id == user_id,
            selectable_workspace_clause(now or datetime.now(UTC)),
        )
        .order_by(Membership.created_at.asc())
    )
    return list(rows.all())
