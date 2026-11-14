"""Lexical search over the retrieval index (HOM-93, ADR 0068 D1, D2, D6, D7).

The query is normalized with the normalizer the text view used
(:func:`backend.speech.text_normalize.get_normalizer`).  On PostgreSQL the
search is ``to_tsvector('simple', text) @@ plainto_tsquery('simple', :q)``,
scored with ``ts_rank`` and served by the GIN expression index of PR 1.
SQLite (development and tests) has no full-text index: every query token must
appear in the text (an escaped ``LIKE``), and the score is the number of
matched tokens.  Both paths order by score descending, then entry id.

Every read joins live rows and never trusts the stored snapshot alone
(:func:`live_entries`):

* the live ``meetings`` row: same workspace, not deleted, inside the caller's
  record access (ADR 0068 D6);
* the live ``transcript_revisions`` row with ``is_current`` true (ADR 0068 D7);
* the entry's ``transcript_text_views`` row, which must match the workspace's
  current default reader view (SPEC Revision 2 R3).  So when an admin turns
  redaction on, entries indexed from the original text stop being evidence at
  once, before any reindex.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, and_, func, literal_column, or_, select
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    Meeting,
    RetrievalEntry,
    TranscriptRevision,
    TranscriptTextView,
    Workspace,
)
from backend.privacy.transcript_view import ViewPolicy, choose_view
from backend.retrieval.filters import (
    SearchFilters,
    apply_search_filters,
    checked_filters,
    conversation_at,
)
from backend.security.authorization import RecordAccess
from backend.speech.text_normalize import get_normalizer

MAX_LIMIT = 100
# Bounds on a search query, checked before any SQL runs.
MAX_QUERY_CHARS = 1000  # of the stripped query
MAX_QUERY_TOKENS = 32  # of the normalized query
# A literal, not a bound parameter, so the expression matches the GIN index
# ``to_tsvector('simple', text)`` of PR 1 exactly.
_FTS_CONFIG = literal_column("'simple'")
_LIKE_ESCAPE = "\\"


@dataclass(frozen=True)
class ScoredEntry:
    """One search result: the entry, its conversation, its score, its kind."""

    entry_id: str
    meeting_id: str
    score: float
    historical: bool  # the entry's revision is not the current one


def _reader_view(db: Session, workspace_id: str) -> ViewPolicy | None:
    """The workspace's current default reader view, or None if it is not visible.

    The two policy columns are read from the database on every call, not from
    a cached ``Workspace`` object, so a policy change made by another request
    counts at once.  ``choose_view(..., requested=None, may_read_original=False)``
    gives the original view with no digest when redaction is off, and the
    redacted view with the policy digest when it is on.  The report reader
    applies the same rule (``routers/v1/reports.py``).
    """
    row = db.execute(
        select(Workspace.pii_redaction_enabled, Workspace.pii_redaction_classes).where(
            Workspace.id == workspace_id
        )
    ).first()
    if row is None:
        return None
    # A detached object that only carries the policy columns.  It is never
    # added to the session.
    policy_holder = Workspace(
        pii_redaction_enabled=row.pii_redaction_enabled,
        pii_redaction_classes=row.pii_redaction_classes,
    )
    return choose_view(policy_holder, requested=None, may_read_original=False)


def checked_now(now: datetime | None) -> datetime:
    """The shared ``now`` check of the retrieval package (HOM-94 SPEC 12).

    ``None`` means the current UTC instant.  Anything that is not a
    ``datetime``, or a ``datetime`` without a UTC offset, is a ``ValueError``
    (never a ``TypeError`` or an ``AttributeError``).  An aware non-UTC value
    is returned as its UTC instant: instants are compared, never wall-clock
    text.
    """
    if now is None:
        return datetime.now(UTC)
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("now must be a datetime with a timezone")
    return now.astimezone(UTC)


def live_entries(
    db: Session,
    *,
    access: RecordAccess,
    workspace_id: str,
    include_history: bool,
    now: datetime | None = None,
    filters: SearchFilters | None = None,
) -> Select | None:
    """The entries of ``workspace_id`` this caller may use as evidence now.

    Selects ``id``, ``meeting_id``, the revision's live ``is_current`` and
    ``conversation_at`` (``COALESCE(started_at, created_at)``, the column the
    vector window orders by, HOM-95).  With ``include_history`` the
    ``is_current`` and ``superseded_at`` conditions are dropped; the caller
    marks a row historical when its revision is not current.  None when the
    workspace is not visible (for example another tenant under row-level
    security).  A meeting past its retention (``retention_expires_at <= now``)
    is never evidence, on both dialects, so the retention sweep only removes
    what reads already hide.  ``filters`` (HOM-95) only narrows: the set
    conditions of :class:`SearchFilters` become extra AND conditions here,
    before scoring and before any ``LIMIT``.
    """
    checked_filters(filters)
    moment = checked_now(now)
    policy = _reader_view(db, workspace_id)
    if policy is None:
        return None
    digest_matches = (
        TranscriptTextView.input_policy_digest.is_(None)
        if policy.digest is None
        else TranscriptTextView.input_policy_digest == policy.digest
    )
    query = (
        select(
            RetrievalEntry.id,
            RetrievalEntry.meeting_id,
            TranscriptRevision.is_current,
            conversation_at().label("conversation_at"),
        )
        .join(
            Meeting,
            and_(
                Meeting.id == RetrievalEntry.meeting_id,
                Meeting.workspace_id == workspace_id,
                Meeting.deleted_at.is_(None),
                or_(
                    Meeting.retention_expires_at.is_(None),
                    Meeting.retention_expires_at > moment,
                ),
                access.meeting_clause(),
            ),
        )
        .join(
            TranscriptRevision,
            and_(
                TranscriptRevision.id == RetrievalEntry.revision_id,
                TranscriptRevision.workspace_id == workspace_id,
            ),
        )
        .join(
            TranscriptTextView,
            and_(
                TranscriptTextView.id == RetrievalEntry.text_view_id,
                TranscriptTextView.workspace_id == workspace_id,
                TranscriptTextView.input_view == policy.view,
                digest_matches,
            ),
        )
        .where(RetrievalEntry.workspace_id == workspace_id)
    )
    if not include_history:
        query = query.where(
            TranscriptRevision.is_current.is_(True),
            RetrievalEntry.superseded_at.is_(None),
        )
    if filters is not None:
        query = apply_search_filters(query, filters, dialect_name=db.get_bind().dialect.name)
    return query


def _like_pattern(token: str) -> str:
    """A ``LIKE`` pattern that matches ``token`` anywhere, wildcards escaped."""
    escaped = (
        token.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


def lexical_search(
    db: Session,
    *,
    access: RecordAccess,
    workspace_id: str,
    query: str,
    limit: int = 20,
    include_history: bool = False,
    language: str = "fa",
    now: datetime | None = None,
    filters: SearchFilters | None = None,
) -> list[ScoredEntry]:
    """The live entries of the workspace whose indexed text matches ``query``.

    ``limit`` must be 1..100.  The query is normalized with the normalizer of
    ``language``.  A query that is empty after normalizing returns nothing.
    An oversized query is a ValueError before any SQL runs, on both
    dialects: more than 1000 characters after strip, or more than 32 tokens
    after normalizing.  ``filters`` only narrows the candidate select.
    """
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be 1..{MAX_LIMIT}")
    checked_filters(filters)
    stripped = query.strip()
    if len(stripped) > MAX_QUERY_CHARS:
        raise ValueError(f"a query holds at most {MAX_QUERY_CHARS} characters")
    normalized = get_normalizer(language)(stripped).strip()
    if not normalized:
        return []
    tokens = normalized.split()
    if len(tokens) > MAX_QUERY_TOKENS:
        raise ValueError(f"a query holds at most {MAX_QUERY_TOKENS} words")
    candidates = live_entries(
        db,
        access=access,
        workspace_id=workspace_id,
        include_history=include_history,
        now=now,
        filters=filters,
    )
    if candidates is None:
        return []
    if db.get_bind().dialect.name == "postgresql":
        document = func.to_tsvector(_FTS_CONFIG, RetrievalEntry.text)
        needle = func.plainto_tsquery(_FTS_CONFIG, normalized)
        score = func.ts_rank(document, needle).label("score")
        rows = db.execute(
            candidates.add_columns(score)
            .where(document.op("@@")(needle))
            .order_by(score.desc(), RetrievalEntry.id)
            .limit(limit)
        ).all()
        return [
            ScoredEntry(
                entry_id=row.id,
                meeting_id=row.meeting_id,
                score=float(row.score),
                historical=not row.is_current,
            )
            for row in rows
        ]
    # SQLite: every distinct token must appear, so each row matches all of
    # them and the score is the same for every result.
    tokens = list(dict.fromkeys(tokens))
    rows = db.execute(
        candidates.where(
            *(
                RetrievalEntry.text.like(_like_pattern(token), escape=_LIKE_ESCAPE)
                for token in tokens
            )
        )
        .order_by(RetrievalEntry.id)
        .limit(limit)
    ).all()
    return [
        ScoredEntry(
            entry_id=row.id,
            meeting_id=row.meeting_id,
            score=float(len(tokens)),
            historical=not row.is_current,
        )
        for row in rows
    ]
