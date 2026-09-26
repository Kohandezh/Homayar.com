"""Share contract (WP03-T06).

A share grants *one* meeting's processed result to an explicit audience for a
bounded time, plus optional, separately granted operations:

* ``view``     – transcript, summary, actions (always implied by a valid share)
* ``export``   – the same content as a downloadable file (``allow_export``)
* ``download`` – the original audio (``allow_download``)

It never grants workspace membership, other meetings, search, jobs or exports
beyond the shared meeting.  Every access re-evaluates *current* server-side
state; nothing is trusted because the token was valid when issued:

* issuer must still hold an effective membership with ``share:manage`` and, if
  record-scoped, must still see the meeting;
* ``workspace`` audience: caller must hold an effective membership in the
  source workspace and, under team record scope, see the meeting;
* ``recipients`` audience: caller must be signed in and prove ownership of a
  listed email or Iranian mobile number (verified on the account); the
  recipient row is then bound to that user id so a later address change or a
  reassigned number cannot transfer the grant; the issuer edits the list
  (``edit_recipients``) under a row lock, and a removed recipient is refused
  on the next request;
* ``organization`` audience: caller must be signed in and *currently* a member
  of the organization the source workspace belongs to (an effective membership
  in any active workspace of that organization, or an effective organization
  membership); only while the workspace keeps organization shares enabled;
  the source workspace's team record scope is not applied (the issuer chose
  the audience explicitly and the issuer's authority is re-checked);
* ``link`` audience: only while the workspace keeps public links enabled and
  never with audio download.

Revocation and expiry are enforced on every request, including range requests
of an application-mediated download.  Where object storage presigns URLs, an
already-issued URL stays usable until its short TTL: that mode is
expiry-bounded, not immediately revocable, and the response says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.config import Settings
from backend.database.saas_models import (
    Meeting,
    Membership,
    OrganizationMembership,
    ShareAudience,
    ShareLink,
    ShareRecipient,
    User,
    Workspace,
)
from backend.database.tenant_context import bind_capability_context, bind_tenant_context
from backend.security.authorization import (
    Permission,
    membership_effective_clause,
    organization_membership_effective_clause,
    resolve_record_access,
    role_has,
)
from backend.security.normalization import normalize_email, normalize_iranian_mobile
from backend.security.tokens import token_digest

ShareOperation = Literal["view", "export", "download"]

MAX_RECIPIENTS = 50


class ShareError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, **kwargs) -> None:
        super().__init__(
            status_code=status_code, detail={"code": code, "message": message}, **kwargs
        )


def share_not_found() -> ShareError:
    # Missing, expired, revoked, suspended and disabled shares all look alike
    # to an anonymous caller so tokens cannot be used to probe state.
    return ShareError(404, "share_not_found", "Share link not found")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def share_token_hash(raw_token: str, settings: Settings) -> str:
    return token_digest(f"share:{raw_token}", settings)


# --- creation -----------------------------------------------------------------


@dataclass(frozen=True)
class ShareSpec:
    audience: ShareAudience
    recipients: tuple[str, ...]
    allow_download: bool
    allow_export: bool
    expires_in_hours: int | None
    phone_recipients: tuple[str, ...] = ()

    @property
    def recipient_count(self) -> int:
        return len(self.recipients) + len(self.phone_recipients)


def validate_share_spec(spec: ShareSpec, *, workspace: Workspace) -> None:
    """Policy checks at creation time (the caller already holds share:manage)."""
    if spec.audience == ShareAudience.LINK:
        if not workspace.public_share_links_enabled:
            raise ShareError(
                403,
                "public_share_links_disabled",
                "Anonymous share links are disabled for this workspace",
            )
        if spec.allow_download:
            raise ShareError(
                422,
                "download_requires_named_audience",
                "Audio download cannot be granted to anonymous links",
            )
        if spec.recipient_count:
            raise ShareError(422, "recipients_not_allowed", "Anonymous links take no recipients")
    elif spec.audience == ShareAudience.RECIPIENTS:
        if not spec.recipient_count:
            raise ShareError(422, "recipients_required", "At least one recipient is required")
        if spec.recipient_count > MAX_RECIPIENTS:
            raise ShareError(422, "too_many_recipients", "Too many recipients")
    elif spec.audience == ShareAudience.ORGANIZATION:
        if not workspace.organization_share_links_enabled:
            raise ShareError(
                403,
                "organization_share_links_disabled",
                "Organization share links are disabled for this workspace",
            )
        if spec.recipient_count:
            raise ShareError(
                422, "recipients_not_allowed", "Organization shares do not take recipients"
            )
    elif spec.recipient_count:
        raise ShareError(422, "recipients_not_allowed", "Workspace shares do not take recipients")


def normalized_recipients(emails: list[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for email in emails:
        try:
            seen.setdefault(normalize_email(email), None)
        except ValueError as exc:
            raise ShareError(422, "invalid_recipient", "Invalid recipient email") from exc
    return tuple(seen)


def normalized_phone_recipients(phones: list[str]) -> tuple[str, ...]:
    """Iranian mobile numbers in E.164 through the one normalisation rule of the product."""
    seen: dict[str, None] = {}
    for phone in phones:
        try:
            seen.setdefault(normalize_iranian_mobile(phone), None)
        except ValueError as exc:
            raise ShareError(422, "invalid_recipient", "Invalid recipient phone number") from exc
    return tuple(seen)


def issue_share(
    db: Session,
    *,
    workspace: Workspace,
    meeting: Meeting,
    created_by_user_id: str,
    spec: ShareSpec,
    raw_token: str,
    settings: Settings,
) -> ShareLink:
    validate_share_spec(spec, workspace=workspace)
    share = ShareLink(
        workspace_id=workspace.id,
        meeting_id=meeting.id,
        created_by_user_id=created_by_user_id,
        token_hash=share_token_hash(raw_token, settings),
        audience=spec.audience,
        require_authentication=spec.audience != ShareAudience.LINK,
        allow_download=spec.allow_download,
        allow_export=spec.allow_export,
        expires_at=(
            datetime.now(UTC) + timedelta(hours=spec.expires_in_hours)
            if spec.expires_in_hours is not None
            else None
        ),
    )
    for email in spec.recipients:
        share.recipients.append(ShareRecipient(workspace_id=workspace.id, email=email))
    for phone in spec.phone_recipients:
        share.recipients.append(ShareRecipient(workspace_id=workspace.id, phone_e164=phone))
    db.add(share)
    db.flush()
    return share


# --- access -------------------------------------------------------------------


@dataclass(frozen=True)
class ShareGrant:
    share: ShareLink
    meeting: Meeting
    workspace: Workspace
    actor_user: User | None
    recipient: ShareRecipient | None

    def permits(self, operation: ShareOperation) -> bool:
        if operation == "view":
            return True
        if operation == "export":
            return self.share.allow_export
        if operation == "download":
            return self.share.allow_download
        return False


def _issuer_still_authorized(db: Session, *, share: ShareLink, meeting: Meeting) -> bool:
    now = datetime.now(UTC)
    issuer = db.execute(
        select(Membership, Workspace)
        .join(Workspace, Workspace.id == Membership.workspace_id)
        .where(
            Membership.workspace_id == share.workspace_id,
            Membership.user_id == share.created_by_user_id,
            membership_effective_clause(now),
        )
    ).first()
    if issuer is None:
        return False
    membership, workspace = issuer
    if not role_has(membership.role, Permission.SHARE_MANAGE):
        return False
    access = resolve_record_access(db, workspace=workspace, membership=membership, now=now)
    if access.unrestricted:
        return True
    return (
        db.scalar(select(Meeting.id).where(Meeting.id == meeting.id, access.meeting_clause()))
        is not None
    )


def _match_recipient(db: Session, *, share: ShareLink, actor: User) -> ShareRecipient | None:
    """Bind by stable user id first; otherwise by a *verified* email or phone the actor owns.

    Only unbound rows are matched by address, so a number or email that was
    later reassigned to another account transfers nothing: the row stays with
    the user it was bound to until the issuer removes it.
    """
    bound = db.scalar(
        select(ShareRecipient).where(
            ShareRecipient.share_id == share.id, ShareRecipient.user_id == actor.id
        )
    )
    if bound is not None:
        return bound
    candidate: ShareRecipient | None = None
    if actor.email is not None and actor.email_verified_at is not None:
        try:
            actor_email = normalize_email(actor.email)
        except ValueError:
            actor_email = None
        if actor_email is not None:
            candidate = db.scalar(
                select(ShareRecipient)
                .where(
                    ShareRecipient.share_id == share.id,
                    ShareRecipient.email == actor_email,
                    ShareRecipient.user_id.is_(None),
                )
                .with_for_update()
            )
    if candidate is None and actor.phone_e164 and actor.phone_verified_at is not None:
        candidate = db.scalar(
            select(ShareRecipient)
            .where(
                ShareRecipient.share_id == share.id,
                ShareRecipient.phone_e164 == actor.phone_e164,
                ShareRecipient.user_id.is_(None),
            )
            .with_for_update()
        )
    if candidate is None:
        return None
    candidate.user_id = actor.id
    candidate.bound_at = datetime.now(UTC)
    db.flush()
    return candidate


def _organization_member(db: Session, *, organization_id: str, actor: User, now: datetime) -> bool:
    """Current membership of the organization: any effective workspace membership in an
    active workspace of the organization, or an effective organization membership."""
    via_workspace = db.scalar(
        select(Membership.id)
        .join(Workspace, Workspace.id == Membership.workspace_id)
        .where(
            Workspace.organization_id == organization_id,
            Workspace.status == "active",
            Membership.user_id == actor.id,
            membership_effective_clause(now),
        )
        .limit(1)
    )
    if via_workspace is not None:
        return True
    via_organization = db.scalar(
        select(OrganizationMembership.id)
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == actor.id,
            organization_membership_effective_clause(now),
        )
        .limit(1)
    )
    return via_organization is not None


def resolve_share_grant(
    db: Session,
    *,
    raw_token: str,
    actor: User | None,
    operation: ShareOperation,
    settings: Settings,
) -> ShareGrant:
    """Resolve and fully authorize a share access, or raise.

    Binds only the share capability to the database session; the tenant context
    is bound solely for the ``workspace`` audience after the caller's membership
    in that workspace has been validated (the same authority a normal request
    for that member would carry).
    """
    digest = share_token_hash(raw_token, settings)
    bind_capability_context(db, share_token_hash=digest)
    now = datetime.now(UTC)

    share = db.scalar(
        select(ShareLink)
        .options(selectinload(ShareLink.recipients))
        .where(ShareLink.token_hash == digest, ShareLink.revoked_at.is_(None))
    )
    if share is None:
        raise share_not_found()
    expires_at = _as_utc(share.expires_at)
    if expires_at is not None and expires_at <= now:
        raise share_not_found()

    workspace = db.scalar(
        select(Workspace).where(Workspace.id == share.workspace_id, Workspace.status == "active")
    )
    meeting = db.scalar(
        select(Meeting).where(
            Meeting.id == share.meeting_id,
            Meeting.workspace_id == share.workspace_id,
            Meeting.deleted_at.is_(None),
        )
    )
    if workspace is None or meeting is None:
        raise share_not_found()
    if share.audience == ShareAudience.LINK and not workspace.public_share_links_enabled:
        raise share_not_found()
    if (
        share.audience == ShareAudience.ORGANIZATION
        and not workspace.organization_share_links_enabled
    ):
        raise share_not_found()
    if not _issuer_still_authorized(db, share=share, meeting=meeting):
        raise share_not_found()

    recipient: ShareRecipient | None = None
    if share.audience != ShareAudience.LINK:
        if actor is None:
            raise ShareError(
                401,
                "share_authentication_required",
                "Sign in to access this share link",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if share.audience == ShareAudience.WORKSPACE:
            membership = db.scalar(
                select(Membership).where(
                    Membership.workspace_id == share.workspace_id,
                    Membership.user_id == actor.id,
                    membership_effective_clause(now),
                )
            )
            if membership is None:
                raise ShareError(403, "share_membership_required", "Not a member of this workspace")
            bind_tenant_context(
                db, workspace_id=workspace.id, organization_id=workspace.organization_id
            )
            access = resolve_record_access(db, workspace=workspace, membership=membership, now=now)
            visible = access.unrestricted or (
                db.scalar(
                    select(Meeting.id).where(Meeting.id == meeting.id, access.meeting_clause())
                )
                is not None
            )
            if not visible:
                raise ShareError(403, "share_record_scope_denied", "Record is outside your scope")
        elif share.audience == ShareAudience.ORGANIZATION:
            # The audience follows the workspace's *current* organization; no
            # tenant context is bound: the share token alone bounds the reads.
            if not _organization_member(
                db, organization_id=workspace.organization_id, actor=actor, now=now
            ):
                raise ShareError(
                    403,
                    "share_organization_membership_required",
                    "Not a member of this organization",
                )
        else:
            recipient = _match_recipient(db, share=share, actor=actor)
            if recipient is None:
                raise ShareError(403, "share_recipient_denied", "This link is not addressed to you")

    grant = ShareGrant(
        share=share, meeting=meeting, workspace=workspace, actor_user=actor, recipient=recipient
    )
    if not grant.permits(operation):
        raise ShareError(
            403, "share_operation_not_permitted", f"This share does not permit {operation}"
        )
    return grant


# --- recipient editing ---------------------------------------------------------


@dataclass(frozen=True)
class RecipientEdit:
    share: ShareLink
    added_ids: tuple[str, ...]
    removed_ids: tuple[str, ...]


def edit_recipients(
    db: Session,
    *,
    share_id: str,
    workspace_id: str,
    meeting_id: str,
    add_emails: tuple[str, ...],
    add_phones: tuple[str, ...],
    remove: list[str],
) -> RecipientEdit:
    """Apply a delta to the recipient list of a ``recipients`` share.

    The share row is locked for the transaction, so concurrent editors are
    serialised and each sees the other's committed changes: nothing is lost
    silently and the limit is enforced on the final list. Removals are applied
    first (an unknown id is refused before anything changes); additions
    already present are ignored. Callers hold ``share:manage`` on a visible
    record and commit afterwards.
    """
    share = db.scalar(
        select(ShareLink)
        .where(
            ShareLink.id == share_id,
            ShareLink.workspace_id == workspace_id,
            ShareLink.meeting_id == meeting_id,
        )
        .with_for_update()
    )
    if share is None:
        raise share_not_found()
    if share.audience != ShareAudience.RECIPIENTS:
        raise ShareError(
            422, "recipients_not_editable", "Only recipient shares have an editable recipient list"
        )
    if share.revoked_at is not None:
        raise ShareError(422, "recipients_not_editable", "Revoked shares cannot be edited")
    current = list(
        db.scalars(
            select(ShareRecipient)
            .where(ShareRecipient.share_id == share.id)
            .order_by(ShareRecipient.created_at, ShareRecipient.id)
        )
    )
    by_id = {row.id: row for row in current}
    removed: list[str] = []
    for recipient_id in dict.fromkeys(remove):
        row = by_id.get(recipient_id)
        if row is None:
            raise ShareError(404, "recipient_not_found", "Recipient not found")
        removed.append(row.id)
    kept = [row for row in current if row.id not in removed]
    emails = {row.email for row in kept if row.email is not None}
    phones = {row.phone_e164 for row in kept if row.phone_e164 is not None}
    new_emails = [email for email in add_emails if email not in emails]
    new_phones = [phone for phone in add_phones if phone not in phones]
    if len(kept) + len(new_emails) + len(new_phones) > MAX_RECIPIENTS:
        raise ShareError(422, "too_many_recipients", "Too many recipients")
    if not kept and not new_emails and not new_phones:
        raise ShareError(422, "recipients_required", "At least one recipient is required")
    for recipient_id in removed:
        db.delete(by_id[recipient_id])
    added: list[ShareRecipient] = [
        ShareRecipient(workspace_id=share.workspace_id, share_id=share.id, email=email)
        for email in new_emails
    ] + [
        ShareRecipient(workspace_id=share.workspace_id, share_id=share.id, phone_e164=phone)
        for phone in new_phones
    ]
    db.add_all(added)
    db.flush()
    db.expire(share, ["recipients"])
    return RecipientEdit(
        share=share,
        added_ids=tuple(row.id for row in added),
        removed_ids=tuple(removed),
    )
