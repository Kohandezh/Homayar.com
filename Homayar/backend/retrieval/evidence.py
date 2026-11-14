"""Evidence retrieval under authorization re-resolved from live rows (HOM-94).

Authorization filters run BEFORE retrieval, and the same filters are
re-checked on every evidence fetch, so no candidate outside the caller's
scope is ever scored and none is ever returned (ADR 0077, parent HOM-21
features F57 and F58).

:func:`resolve_scope` re-resolves the caller from live rows on every call:
an active membership inside its validity window, an active user, an active
workspace, the ``meeting:read`` permission and, for an API-key actor, the
key's scope.  It never trusts the ``record_access`` a ``WorkspaceContext``
resolved at request start: a long Copilot session, or any caller that holds
the object, loses access the moment the live rows change.

:func:`search_evidence_lexical` and :func:`search_evidence_vector` resolve
the scope first (a denied caller runs no query over the index at all) and
then run the read primitives of :mod:`backend.retrieval.lexical` and
:mod:`backend.retrieval.vector_store`, whose live joins put every filter -
tenant, workspace, record ACL, retention, deletion, current revision and the
current reader view - inside the candidate SQL, before scoring and before
``LIMIT``.

:func:`fetch_evidence` fetches evidence by entry id for a model that named
the ids (untrusted input).  Every unreachable id - another tenant's, another
team's, a deleted meeting's, an expired retention, an old redaction policy,
a superseded revision, a random guess - gives exactly the same result: the
id is ``missing``.  No reason per id, no error, no per-id lookup path, and
no id found inside an entry's text is ever followed.  Nothing here logs
entry text, query text or vectors: ids and counts only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    ApiKey,
    Membership,
    RetrievalEntry,
    TeamAssignment,
    User,
    Workspace,
)
from backend.database.tenant_context import current_context
from backend.retrieval.filters import SearchFilters, checked_filters
from backend.retrieval.lexical import checked_now, lexical_search, live_entries
from backend.retrieval.vector_store import VectorStore
from backend.security.authorization import (
    Permission,
    RecordAccess,
    resolve_record_access,
    role_has,
    selectable_workspace_clause,
)
from backend.security.context import WorkspaceContext, effective_api_key_scopes
from backend.speech.text_normalize import get_normalizer

EVIDENCE_PERMISSION = Permission.MEETING_READ
MAX_FETCH_IDS = 100
# An id a caller may ask for: a str of length 1..64, compared exactly
# (case-sensitive, no strip, no normalization).
_MIN_ID_LENGTH = 1
_MAX_ID_LENGTH = 64


def _checked_context(context: WorkspaceContext) -> WorkspaceContext:
    if not isinstance(context, WorkspaceContext):
        raise ValueError("context must be a WorkspaceContext")
    return context


def _required_now(now: datetime) -> datetime:
    """The evidence API takes ``now`` from the server clock (SPEC 3.2): here it
    is required, so ``None`` is a ``ValueError`` before any SQL runs.  Only
    the read primitives keep ``None`` as the real clock (SPEC 3.1)."""
    if now is None:
        raise ValueError("now must be a datetime with a timezone")
    return checked_now(now)


def _live_key_scopes(row: ApiKey) -> frozenset[Permission]:
    """The scopes of the live row, under the same rule as ``Actor.scopes``:
    an unparsable value is skipped, never an error, and the result is
    intersected with ``API_KEY_SCOPES`` (HOM-361)."""
    return effective_api_key_scopes(row.scopes)


def _refresh_assignments(db: Session, *, workspace_id: str, user_id: str) -> None:
    """Reload the caller's ``TeamAssignment`` rows from the database first.

    ``resolve_record_access`` (``security/*``, not changed here) selects the
    assignments as entities.  SQL filters them on live columns, but a row the
    session already holds comes back as the cached object, so a changed
    ``role`` (supervisor to agent) would stay stale.  ``populate_existing``
    writes the live values over the cached ones, for these rows only."""
    db.scalars(
        select(TeamAssignment)
        .where(TeamAssignment.workspace_id == workspace_id, TeamAssignment.user_id == user_id)
        .execution_options(populate_existing=True)
    ).all()


def _as_utc(value: datetime) -> datetime:
    """A datetime read from the database (naive on SQLite) as a UTC instant."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class RetrievalScope:
    """What the caller may read right now, resolved from live rows."""

    workspace_id: str
    user_id: str
    access: RecordAccess  # resolved from live rows, now
    now: datetime


def resolve_scope(
    db: Session, *, context: WorkspaceContext, now: datetime
) -> RetrievalScope | None:
    """Re-resolve the caller from live rows.  ``None`` means denied.

    The checks run in this order and the first defect decides:

    1. ``now`` without a timezone, or ``context`` not a ``WorkspaceContext``:
       ``ValueError``;
    2. a session not bound to the caller's workspace: ``ValueError`` before
       any query runs (the rule of ``run_tool``, ADR 0075);
    3. an API-key actor: the ``ApiKey`` row is re-read by id from live rows,
       and a missing row, a set ``revoked_at``, an ``expires_at <= now``, a
       row of another workspace, or a live scope set without the permission
       denies with no index query.  The scopes of the stale object are never
       used.  A session actor keeps the vacuous scope rule;
    4. one query over live rows (workspace, membership, user): an inactive
       membership, a membership outside its validity window, a non-active
       workspace or a deactivated user all give ``None``;
    5. a live membership role without the permission: ``None``;
    6. the record access, resolved from the live workspace and membership.

    Login sessions are not re-checked here: the session is validated at the
    request edge, and this module only re-resolves what a long-lived caller
    object could outlive (membership, user, workspace, key).

    "Live" holds in the caller's own session too.  A request loads the
    context's ``ApiKey``, ``Workspace`` and ``Membership`` into the same
    session it passes here, and a plain ``get`` or entity ``select`` would
    return those cached objects with their old values.  So every read below
    uses ``populate_existing``: it runs SQL and writes the live values over
    the cached ones.  Nothing is expired or expunged, so other objects of the
    session keep their unflushed changes.  The session is flushed first (after
    the argument and binding checks), so the caller's own unflushed changes
    to these objects reach the database and the refresh reads them back
    instead of dropping them.
    """
    _checked_context(context)
    moment = _required_now(now)
    if current_context(db).workspace_id != context.workspace.id:
        raise ValueError("the session is not bound to the caller's workspace")
    # Before the first live read: populate_existing would otherwise write the
    # database values over the caller's own pending changes.
    db.flush()
    if context.actor.api_key is not None:
        key = db.get(ApiKey, context.actor.api_key.id, populate_existing=True)
        if (
            key is None
            or key.revoked_at is not None
            or (key.expires_at is not None and _as_utc(key.expires_at) <= moment)
            or key.workspace_id != context.workspace.id
        ):
            return None
        if EVIDENCE_PERMISSION not in _live_key_scopes(key):
            return None
    elif not context.scope_permits(EVIDENCE_PERMISSION):
        return None
    row = db.execute(
        select(Workspace, Membership)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .join(User, User.id == Membership.user_id)
        .where(
            Workspace.id == context.workspace.id,
            Membership.user_id == context.membership.user_id,
            selectable_workspace_clause(moment),
            User.is_active.is_(True),
        )
        .execution_options(populate_existing=True)
    ).first()
    if row is None:
        return None
    workspace, membership = row
    if not role_has(membership.role, EVIDENCE_PERMISSION):
        return None
    _refresh_assignments(db, workspace_id=workspace.id, user_id=membership.user_id)
    access = resolve_record_access(db, workspace=workspace, membership=membership, now=moment)
    return RetrievalScope(
        workspace_id=workspace.id,
        user_id=membership.user_id,
        access=access,
        now=moment,
    )


@dataclass(frozen=True)
class EvidenceHit:
    """One search result: ids, the score and the history label only."""

    entry_id: str
    meeting_id: str
    score: float
    historical: bool


@dataclass(frozen=True)
class SearchOutcome:
    state: str  # "answered" or "denied"
    hits: tuple[EvidenceHit, ...]  # empty when denied


def _sql_safe_text(value: str) -> bool:
    """A str the database can take as a bound parameter: no NUL character,
    and encodable as UTF-8 (no lone surrogate).  SPEC 12, for query and ids."""
    if "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _checked_query(query: str, language: str) -> None:
    """The query and language checks of SPEC 12: a ``ValueError``, never an
    ``AttributeError``, ``KeyError`` or ``UnicodeEncodeError``."""
    if not isinstance(query, str):
        raise ValueError("query must be a str")
    if not _sql_safe_text(query):
        raise ValueError("query must be encodable as UTF-8 and hold no NUL character")
    if not isinstance(language, str):
        raise ValueError("language must be a str")
    try:
        get_normalizer(language)
    except KeyError as exc:
        raise ValueError(f"unknown language: {language!r}") from exc


def search_evidence_lexical(
    db: Session,
    *,
    context: WorkspaceContext,
    query: str,
    now: datetime,
    limit: int = 20,
    include_history: bool = False,
    language: str = "fa",
    filters: SearchFilters | None = None,
) -> SearchOutcome:
    """Lexical search under a freshly resolved scope (see :func:`resolve_scope`).

    A denied caller returns before the query checks run, so its defects (a
    non-str query, a query the database cannot encode, an unknown language,
    ``limit``, an oversized query) never surface.  An answered caller gets a
    ``ValueError`` for each of them.  ``filters`` (HOM-95) only narrows the
    candidates, inside the same live joins.
    """
    scope = resolve_scope(db, context=context, now=now)
    if scope is None:
        return SearchOutcome("denied", ())
    _checked_query(query, language)
    checked_filters(filters)
    scored = lexical_search(
        db,
        access=scope.access,
        workspace_id=scope.workspace_id,
        query=query,
        limit=limit,
        include_history=include_history,
        language=language,
        now=scope.now,
        filters=filters,
    )
    return SearchOutcome(
        "answered",
        tuple(
            EvidenceHit(
                entry_id=hit.entry_id,
                meeting_id=hit.meeting_id,
                score=hit.score,
                historical=hit.historical,
            )
            for hit in scored
        ),
    )


def search_evidence_vector(
    db: Session,
    *,
    context: WorkspaceContext,
    store: VectorStore,
    embedding_version: str,
    query_vector: Sequence[float],
    now: datetime,
    k: int = 20,
    candidate_limit: int = 2000,
    include_history: bool = False,
    filters: SearchFilters | None = None,
) -> SearchOutcome:
    """Vector search under a freshly resolved scope (see :func:`resolve_scope`).

    ``filters`` (HOM-95) only narrows the candidate window.
    """
    scope = resolve_scope(db, context=context, now=now)
    if scope is None:
        return SearchOutcome("denied", ())
    checked_filters(filters)
    scored = store.nearest(
        db,
        access=scope.access,
        workspace_id=scope.workspace_id,
        embedding_version=embedding_version,
        query=query_vector,
        k=k,
        candidate_limit=candidate_limit,
        include_history=include_history,
        now=scope.now,
        filters=filters,
    )
    return SearchOutcome(
        "answered",
        tuple(
            EvidenceHit(
                entry_id=hit.entry_id,
                meeting_id=hit.meeting_id,
                score=hit.score,
                historical=hit.historical,
            )
            for hit in scored
        ),
    )


@dataclass(frozen=True)
class Evidence:
    """One entry served as evidence, with the lineage a citation needs."""

    entry_id: str
    meeting_id: str
    revision_id: str
    revision_number: int
    text_view_id: str
    text_view_segment_id: str
    sequence: int
    language: str
    text: str
    historical: bool


@dataclass(frozen=True)
class EvidenceOutcome:
    state: str  # "answered" or "denied"
    evidence: tuple[Evidence, ...]  # in the order the ids were asked, duplicates once
    missing: tuple[str, ...]  # asked ids not returned, in asked order, duplicates once


def fetch_evidence(
    db: Session,
    *,
    context: WorkspaceContext,
    entry_ids: Sequence[object],
    now: datetime,
    include_history: bool = False,
) -> EvidenceOutcome:
    """Fetch evidence by entry id under a fresh authorization check.

    ``entry_ids`` is untrusted (a model wrote it).  The checks run in this
    order: ``now``/``context`` defects, then the ``entry_ids`` shape and
    count (a ``ValueError`` even for a denied caller), then the session
    binding, then the scope.  An item that is not a ``str`` of length 1..64
    (an int, ``None``, an unhashable list or dict, an empty or oversized
    string) can match nothing: it is never queried and never ``missing``.
    A str id of length 1..64 that holds a NUL or cannot be encoded as UTF-8
    is also never put in SQL, but it IS reported in ``missing`` (SPEC 12).
    Ids are compared exactly and every unreachable id - another tenant's,
    another team's, deleted, expired, old-policy, superseded, or a random
    guess - gives the one same result: ``missing``, with no reason and no
    per-id lookup.  A denied caller gets ``("denied", (), ())`` and no query
    over the index.
    """
    _checked_context(context)
    moment = _required_now(now)
    if not isinstance(entry_ids, list | tuple):
        raise ValueError("entry_ids must be a list or tuple")
    asked: list[str] = []
    seen: set[str] = set()
    distinct = 0
    for item in entry_ids:
        if isinstance(item, str):
            if item not in seen:  # dedup the str items only, asked order kept
                seen.add(item)
                asked.append(item)
                distinct += 1
        else:
            distinct += 1  # every non-str item counts as distinct, hashable or not
    if distinct > MAX_FETCH_IDS:
        raise ValueError(f"entry_ids holds at most {MAX_FETCH_IDS} distinct items")
    reportable = [item for item in asked if _MIN_ID_LENGTH <= len(item) <= _MAX_ID_LENGTH]
    queryable = [item for item in reportable if _sql_safe_text(item)]
    scope = resolve_scope(db, context=context, now=moment)
    if scope is None:
        return EvidenceOutcome("denied", (), ())
    found: dict[str, Evidence] = {}
    if queryable:
        candidates = live_entries(
            db,
            access=scope.access,
            workspace_id=scope.workspace_id,
            include_history=include_history,
            now=scope.now,
        )
        if candidates is not None:
            live = candidates.subquery()
            rows = db.execute(
                select(
                    RetrievalEntry.id,
                    RetrievalEntry.meeting_id,
                    RetrievalEntry.revision_id,
                    RetrievalEntry.revision_number,
                    RetrievalEntry.text_view_id,
                    RetrievalEntry.text_view_segment_id,
                    RetrievalEntry.sequence,
                    RetrievalEntry.language,
                    RetrievalEntry.text,
                    live.c.is_current,
                )
                .join(live, live.c.id == RetrievalEntry.id)
                .where(RetrievalEntry.id.in_(queryable))
            ).all()
            for row in rows:
                found[row.id] = Evidence(
                    entry_id=row.id,
                    meeting_id=row.meeting_id,
                    revision_id=row.revision_id,
                    revision_number=row.revision_number,
                    text_view_id=row.text_view_id,
                    text_view_segment_id=row.text_view_segment_id,
                    sequence=row.sequence,
                    language=row.language,
                    text=row.text,
                    historical=not row.is_current,
                )
    return EvidenceOutcome(
        "answered",
        tuple(found[item] for item in reportable if item in found),
        tuple(item for item in reportable if item not in found),
    )
