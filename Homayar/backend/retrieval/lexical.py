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

from sqlalchemy import Select, and_, func, literal_column, select
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    Meeting,
    RetrievalEntry,
    TranscriptRevision,
    TranscriptTextView,
    Workspace,
)
from backend.privacy.transcript_view import ViewPolicy, choose_view
from backend.security.authorization import RecordAccess
from backend.speech.text_normalize import get_normalizer

MAX_LIMIT = 100
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


def live_entries(
    db: Session, *, access: RecordAccess, workspace_id: str, include_history: bool
) -> Select | None:
    """The entries of ``workspace_id`` this caller may use as evidence now.

    Selects ``id``, ``meeting_id`` and the revision's live ``is_current``.
    With ``include_history`` the ``is_current`` and ``superseded_at``
    conditions are dropped; the caller marks a row historical when its
    revision is not current.  None when the workspace is not visible (for
    example another tenant under row-level security).
    """
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
        )
        .join(
            Meeting,
            and_(
                Meeting.id == RetrievalEntry.meeting_id,
                Meeting.workspace_id == workspace_id,
                Meeting.deleted_at.is_(None),
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
) -> list[ScoredEntry]:
    """The live entries of the workspace whose indexed text matches ``query``.

    ``limit`` must be 1..100.  The query is normalized with the normalizer of
    ``language``.  A query that is empty after normalizing returns nothing.
    """
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be 1..{MAX_LIMIT}")
    normalized = get_normalizer(language)(query).strip()
    if not normalized:
        return []
    candidates = live_entries(
        db, access=access, workspace_id=workspace_id, include_history=include_history
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
    tokens = list(dict.fromkeys(normalized.split()))
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
