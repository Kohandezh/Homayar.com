"""The fixture corpus of the HOM-94 filtered-recall harness (SPEC 3.5 A).

A deterministic world (fixed seed, fixed NOW, no network) that the recall
harness of ``test_retrieval_filtered_recall.py`` measures.  Nothing depends
on the real clock: every membership window and team assignment the fixtures
create opens a year before NOW, including the owners that registration
stamps with the real clock (their rows are moved onto the same window):

* two organizations/workspaces: A (``record_scope`` team-restricted) and B;
* in A: 3 teams and the personas an admin (unrestricted), a supervisor of
  team 1, an agent of team 2 (only their own attributed records) and a member
  with no team (only the records they created); in B an unrestricted owner;
* 20 normal meetings of A (10 entries each, teams and the 8 vector clusters
  mixed inside every cluster so a post-filter loses recall), 2 deleted
  meetings, 2 meetings past retention, 1 meeting with a superseded revision
  (8 old plus 8 current entries) and 1 meeting whose entries sit under an old
  redaction policy (a redacted view while the workspace reads the original);
* 5 meetings (50 entries) in B;
* one embedding version, dimension 16, 8 seeded cluster centres plus small
  seeded noise (hashlib-derived, not ``random``), written directly with
  ``pack_vector``.

Entries are created through the real indexer (``index_text_view``) on chains
built with the seeding helpers of ``test_retrieval_index.py``.  The ground
truth is computed in Python from this module's own metadata (never from the
SQL of the code under test): the authorized set is the predicate below, the
ranking the exact cosine over the stored float32 vectors.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update

from backend.database.saas_models import (
    ConversationType,
    Meeting,
    Membership,
    RetrievalEmbedding,
    RetrievalEntry,
    Team,
    TeamAssignment,
    TeamRole,
    TranscriptRevision,
    TranscriptSegment,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.retrieval.embeddings import pack_vector, unpack_vector
from backend.retrieval.filters import SearchFilters
from backend.retrieval.index import INDEX_VERSION
from backend.security.authorization import resolve_record_access
from backend.security.context import Actor, WorkspaceContext
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import _tenant
from backend.tests.test_retrieval_index import _index, _seed_view

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
SEED = 20261114
DIMENSIONS = 16
CLUSTERS = 8
NORMAL_MEETINGS = 20
ENTRIES_PER_MEETING = 10
SUPERSEDED_ENTRIES = 8
QUERIES_PER_CLUSTER = 3
# One fixed embedding space for the whole corpus, written directly.
EMBEDDING_VERSION = hashlib.sha256(b"hom94-recall-corpus/clustered-16").hexdigest()
EMBEDDING_PROVIDER = "corpus"
EMBEDDING_MODEL = "clustered-16"

TEAM_LABELS = {1: "Alpha", 2: "Beta", 3: "Gamma"}
# The personas of workspace A: the admin is unrestricted; the supervisor of
# team 1 sees team 1's meetings; the agent of team 2 sees only the meetings
# attributed to them; the member with no team sees only what they created.
AGENT2_MEETINGS = (1, 7, 13, 19)
MEMBER_MEETINGS = (2, 5, 11, 17)
DELETED_MEETINGS = (20, 21)
EXPIRED_MEETINGS = (22, 23)
SUPERSEDED_MEETING = 24
OLD_POLICY_MEETING = 25
B_MEETINGS = 5
# HOM-95 filter metadata (SPEC 3.7).  Conversation types cycle over call,
# meeting and voicemail by meeting index in blocks of three: ``index % 3`` is
# already the team rule above, so a plain ``index % 3`` would give every
# restricted persona one single type and any type filter would empty one
# persona's truth.
CONVERSATION_CYCLE = (
    ConversationType.CALL.value,
    ConversationType.MEETING.value,
    ConversationType.VOICEMAIL.value,
)
# Normal meetings start ``index + 1`` days before NOW.  These indexes keep
# ``started_at`` NULL, and every meeting without ``started_at`` gets a pinned
# ``created_at`` (``index + 1`` days and 12 hours before NOW), so the
# ``created_at`` fallback of the conversation time is exercised without the
# real clock.
NULL_STARTED_MEETINGS = (4, 13, 18)
# A segment confidence per entry number ``eNNN``; every sixth one stays NULL.
CONFIDENCE_LEVELS = (0.55, 0.65, 0.75, 0.85, 0.95)


def _seeded_values(seed: str, count: int, scale: float) -> list[float]:
    """Deterministic noise from sha256 digests: uniform in [-scale, scale]."""
    values: list[float] = []
    counter = 0
    while len(values) < count:
        digest = hashlib.sha256(f"{SEED}:{seed}:{counter}".encode()).digest()
        values.extend((byte / 255.0) * 2.0 - 1.0 for byte in digest)
        counter += 1
    return [value * scale for value in values[:count]]


def _unit(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values]


def _noisy(base: list[float], seed: str, scale: float) -> list[float]:
    noise = _seeded_values(seed, len(base), scale)
    return _unit([b + n for b, n in zip(base, noise, strict=True)])


def _as_stored(values: list[float]) -> list[float]:
    """The values exactly as the database gives them back: float32 rounded."""
    return unpack_vector(pack_vector(values), DIMENSIONS)


def _cosine(left: list[float], right: list[float]) -> float:
    """The store's cosine: dot over norms, zero vectors have no direction."""
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return dot / (norm_left * norm_right)


CENTRES: tuple[list[float], ...] = tuple(
    _as_stored(_unit(_seeded_values(f"centre-{cluster}", DIMENSIONS, 1.0)))
    for cluster in range(CLUSTERS)
)
# 24 seeded queries, three near every cluster centre.
QUERY_VECTORS: tuple[list[float], ...] = tuple(
    _noisy(CENTRES[cluster], f"query-{cluster}-{variant}", 0.01)
    for cluster in range(CLUSTERS)
    for variant in range(QUERIES_PER_CLUSTER)
)
LEXICAL_QUERIES: tuple[str, ...] = (
    *(f"tok{cluster}" for cluster in range(CLUSTERS)),
    "e000",
    "e012",
    "tok3 item",
)


@dataclass(frozen=True)
class CorpusEntry:
    """One indexed entry and the fixture's own truth about it."""

    entry_id: str
    meeting_id: str
    workspace: str  # "A" or "B"
    cluster: int
    team: int | None  # 1..3, A only
    agent_user_id: str | None
    created_by_user_id: str | None
    deleted: bool
    expired: bool
    historical: bool  # the entry's revision is superseded
    old_policy: bool  # under a redacted view the workspace no longer reads
    text: str
    vector: list[float]
    # HOM-95 filter metadata, written by ``build_corpus`` (SPEC 3.7).
    conversation_type: str | None = None
    conversation_at: datetime | None = None  # COALESCE(started_at, created_at)
    confidence: float | None = None  # the entry's transcript segment


@dataclass(frozen=True)
class Persona:
    key: str
    user_id: str
    workspace: str
    unrestricted: bool
    supervised_teams: frozenset[int]


def _acl_allowed(persona: Persona, entry: CorpusEntry) -> bool:
    """The ACL rule of ``RecordAccess.meeting_clause`` as a Python predicate."""
    if persona.unrestricted:
        return True
    return (
        entry.agent_user_id == persona.user_id
        or entry.created_by_user_id == persona.user_id
        or (entry.team is not None and entry.team in persona.supervised_teams)
    )


def _alive(entry: CorpusEntry) -> bool:
    """The filters that are not the caller's ACL: the workspace liveness rules."""
    return not (entry.deleted or entry.expired or entry.historical or entry.old_policy)


def authorized(persona: Persona, entry: CorpusEntry) -> bool:
    return entry.workspace == persona.workspace and _alive(entry) and _acl_allowed(persona, entry)


def ranked_truth(corpus: Corpus, persona: Persona, query: list[float], k: int = 10) -> list[str]:
    """The filtered truth: the exact cosine ranking, unauthorized removed."""
    scored = sorted(
        (
            (_cosine(query, entry.vector), entry.entry_id)
            for entry in corpus.entries
            if authorized(persona, entry)
        ),
        key=lambda pair: (-pair[0], pair[1]),
    )
    return [entry_id for _score, entry_id in scored[:k]]


def post_filter_baseline(
    corpus: Corpus, persona: Persona, query: list[float], k: int = 10
) -> list[str]:
    """The control: an unrestricted top-k of the workspace, then the ACL filter."""
    scored = sorted(
        (
            (_cosine(query, entry.vector), entry.entry_id)
            for entry in corpus.entries
            if entry.workspace == persona.workspace and _alive(entry)
        ),
        key=lambda pair: (-pair[0], pair[1]),
    )
    by_id = {entry.entry_id: entry for entry in corpus.entries}
    return [entry_id for _score, entry_id in scored[:k] if _acl_allowed(persona, by_id[entry_id])]


def lexical_truth(corpus: Corpus, persona: Persona, query: str) -> frozenset[str]:
    """The entries whose text holds every query token and that are authorized."""
    tokens = query.split()
    return frozenset(
        entry.entry_id
        for entry in corpus.entries
        if authorized(persona, entry) and all(token in entry.text.split() for token in tokens)
    )


def passes_filters(entry: CorpusEntry, filters: SearchFilters | None) -> bool:
    """The SPEC 3.1 filter predicate in Python, from the fixture metadata.

    The corpus records conversation type, conversation time and segment
    confidence only; it has no source or channel-inspection metadata, so
    those two filters are not part of the harness.
    """
    if filters is None:
        return True
    if filters.sources is not None or filters.exclude_audio_flags is not None:
        raise AssertionError("the corpus has no source or audio metadata")
    if filters.conversation_types is not None:
        if entry.conversation_type not in filters.conversation_types:
            return False
    if filters.started_from is not None and entry.conversation_at < filters.started_from:
        return False
    if filters.started_before is not None and entry.conversation_at >= filters.started_before:
        return False
    if filters.min_confidence is not None:
        if entry.confidence is None or entry.confidence < filters.min_confidence:
            return False
    return True


def hybrid_truth(
    corpus: Corpus,
    persona: Persona,
    query_vector: list[float],
    filters: SearchFilters | None,
    k: int = 10,
) -> list[str]:
    """The filtered hybrid truth: authorized entries that pass the filter
    predicate, ranked by the exact cosine (as :func:`ranked_truth`)."""
    scored = sorted(
        (
            (_cosine(query_vector, entry.vector), entry.entry_id)
            for entry in corpus.entries
            if authorized(persona, entry) and passes_filters(entry, filters)
        ),
        key=lambda pair: (-pair[0], pair[1]),
    )
    return [entry_id for _score, entry_id in scored[:k]]


def _meeting_index(item: dict) -> int:
    """The fixture index of a meeting item: every key ends in ``{index:02d}``."""
    return int(item["key"][-2:])


def _conversation_metadata(index: int) -> tuple[str, datetime | None, datetime | None]:
    """(conversation_type, started_at, pinned created_at) of meeting ``index``."""
    conversation_type = CONVERSATION_CYCLE[(index // 3) % len(CONVERSATION_CYCLE)]
    if index < NORMAL_MEETINGS and index not in NULL_STARTED_MEETINGS:
        return conversation_type, NOW - timedelta(days=index + 1), None
    return conversation_type, None, NOW - timedelta(days=index + 1, hours=12)


def _segment_confidence(number: int) -> float | None:
    if number % 6 == 5:
        return None
    return CONFIDENCE_LEVELS[number % len(CONFIDENCE_LEVELS)]


def _write_filter_metadata(
    meetings: list[dict], seeded: dict[str, dict], entries: list[CorpusEntry]
) -> list[CorpusEntry]:
    """Write the HOM-95 metadata to the meeting and segment rows, and return
    the entries with the same metadata recorded for the Python truth."""
    times: dict[str, tuple[str, datetime]] = {}
    with inspection_session_factory()() as db:
        for item in meetings:
            meeting_id = seeded[item["key"]]["meeting_id"]
            conversation_type, started_at, created_at = _conversation_metadata(_meeting_index(item))
            values: dict = {"conversation_type": conversation_type, "started_at": started_at}
            if created_at is not None:
                values["created_at"] = created_at
            db.execute(update(Meeting).where(Meeting.id == meeting_id).values(**values))
            times[meeting_id] = (conversation_type, started_at or created_at)
        segment_of = dict(
            db.execute(
                select(RetrievalEntry.id, RetrievalEntry.segment_id).where(
                    RetrievalEntry.id.in_([entry.entry_id for entry in entries])
                )
            ).all()
        )
        assert len(set(segment_of.values())) == len(entries), "one segment per entry"
        recorded: list[CorpusEntry] = []
        for entry in entries:
            number = int(next(word for word in entry.text.split() if word.startswith("e"))[1:])
            confidence = _segment_confidence(number)
            db.execute(
                update(TranscriptSegment)
                .where(TranscriptSegment.id == segment_of[entry.entry_id])
                .values(confidence=confidence)
            )
            conversation_type, conversation_at = times[entry.meeting_id]
            recorded.append(
                replace(
                    entry,
                    conversation_type=conversation_type,
                    conversation_at=conversation_at,
                    confidence=confidence,
                )
            )
        db.commit()
    return recorded


def _assert_filter_shape(corpus: Corpus) -> None:
    """The metadata the HOM-95 harness needs: every type, NULL starts and
    NULL confidences among the authorized entries of workspace A."""
    live_a = [e for e in corpus.entries if e.workspace == "A" and _alive(e)]
    assert {e.conversation_type for e in live_a} == set(CONVERSATION_CYCLE)
    assert any(e.confidence is None for e in live_a)
    assert all(e.conversation_at is not None for e in corpus.entries)
    for key in ("supervisor1", "agent2", "member"):
        persona = corpus.persona(key)
        types = {e.conversation_type for e in corpus.entries if authorized(persona, e)}
        assert len(types) >= 2, key


@dataclass(frozen=True)
class Corpus:
    workspace_a: str
    workspace_b: str
    team_ids: dict[int, str]
    entries: tuple[CorpusEntry, ...]
    personas: dict[str, Persona]
    embedding_version: str

    def persona(self, key: str) -> Persona:
        return self.personas[key]

    def entry(self, entry_id: str) -> CorpusEntry:
        return {item.entry_id: item for item in self.entries}[entry_id]


def _meeting_item(index: int, counter: int) -> tuple[dict, int]:
    """One meeting's fixture metadata and the next global entry number."""

    def texts(cluster: int, start: int, count: int) -> tuple[str, ...]:
        return tuple(f"cluster tok{cluster} item e{start + i:03d}" for i in range(count))

    if index < NORMAL_MEETINGS:
        item = {
            "key": f"normal-{index:02d}",
            "texts": texts(index % CLUSTERS, counter, ENTRIES_PER_MEETING),
            "cluster": index % CLUSTERS,
            "team": (index % 3) + 1,
            "agent": "agent2" if index in AGENT2_MEETINGS else None,
            "creator": "member" if index in MEMBER_MEETINGS else None,
            "deleted": False,
            "expired": False,
            "old_policy": False,
        }
        return item, counter + ENTRIES_PER_MEETING
    if index in DELETED_MEETINGS or index in EXPIRED_MEETINGS:
        item = {
            "key": f"{'deleted' if index in DELETED_MEETINGS else 'expired'}-{index:02d}",
            "texts": texts(index % CLUSTERS, counter, ENTRIES_PER_MEETING),
            "cluster": index % CLUSTERS,
            "team": (index % 3) + 1,
            "agent": None,
            "creator": None,
            "deleted": index in DELETED_MEETINGS,
            "expired": index in EXPIRED_MEETINGS,
            "old_policy": False,
        }
        return item, counter + ENTRIES_PER_MEETING
    if index == SUPERSEDED_MEETING:
        # Two revisions of one meeting: the old entries historical, the new
        # ones current.  Both hold the cluster token so the lexical truth of
        # this cluster keeps its meaning.
        cluster = index % CLUSTERS
        old = tuple(
            f"cluster tok{cluster} item e{counter + 2 * i:03d}" for i in range(SUPERSEDED_ENTRIES)
        )
        new = tuple(
            f"cluster tok{cluster} item e{counter + 2 * i + 1:03d}"
            for i in range(SUPERSEDED_ENTRIES)
        )
        item = {
            "key": f"superseded-{index:02d}",
            "texts": old,
            "current_texts": new,
            "cluster": cluster,
            "team": (index % 3) + 1,
            "agent": None,
            "creator": None,
            "deleted": False,
            "expired": False,
            "old_policy": False,
        }
        return item, counter + 2 * SUPERSEDED_ENTRIES
    if index == OLD_POLICY_MEETING:
        item = {
            "key": f"old-policy-{index:02d}",
            "texts": texts(index % CLUSTERS, counter, ENTRIES_PER_MEETING),
            "cluster": index % CLUSTERS,
            "team": (index % 3) + 1,
            "agent": None,
            "creator": None,
            "deleted": False,
            "expired": False,
            "old_policy": True,
        }
        return item, counter + ENTRIES_PER_MEETING
    raise AssertionError(f"unknown meeting index {index}")


def build_corpus(client, email_outbox) -> Corpus:
    """Seed the whole world through the real indexer.  Deterministic."""
    tenant_a = _tenant(client, email_outbox, "hom94-corpus-a")
    tenant_b = _tenant(client, email_outbox, "hom94-corpus-b")
    workspace_a, workspace_b = tenant_a["workspace_id"], tenant_b["workspace_id"]
    # Registration stamps the real clock on the owners' memberships; move
    # them onto the same explicit window so nothing depends on the clock.
    for tenant in (tenant_a, tenant_b):
        with inspection_session_factory()() as db:
            db.execute(
                update(Membership)
                .where(
                    Membership.workspace_id == tenant["workspace_id"],
                    Membership.user_id == tenant["user_id"],
                )
                .values(valid_from=NOW - timedelta(days=365))
            )
            db.commit()

    meetings: list[dict] = []
    counter = 0
    for index in (
        list(range(NORMAL_MEETINGS))
        + list(DELETED_MEETINGS)
        + list(EXPIRED_MEETINGS)
        + [
            SUPERSEDED_MEETING,
            OLD_POLICY_MEETING,
        ]
    ):
        item, counter = _meeting_item(index, counter)
        item["workspace"] = "A"
        meetings.append(item)
    for index in range(B_MEETINGS):
        item, counter = _meeting_item(index, counter)
        item["workspace"] = "B"
        item["key"] = f"b-{item['key']}"  # the seeded map is keyed by item key
        meetings.append(item)

    # The teams and the personas of workspace A.
    with inspection_session_factory()() as db:
        db.execute(update(Workspace).where(Workspace.id == workspace_a).values(record_scope="TEAM"))
        team_ids: dict[int, str] = {}
        for number, label in TEAM_LABELS.items():
            team = Team(
                workspace_id=workspace_a, name=f"Corpus {label}", slug=f"corpus-{label.lower()}"
            )
            db.add(team)
            db.flush()
            team_ids[number] = team.id
        personas: dict[str, Persona] = {}
        for key, role, team_number, assignment in (
            ("admin", WorkspaceRole.ADMIN, None, None),
            ("supervisor1", WorkspaceRole.MEMBER, 1, TeamRole.SUPERVISOR),
            ("agent2", WorkspaceRole.MEMBER, 2, TeamRole.AGENT),
            ("member", WorkspaceRole.MEMBER, None, None),
        ):
            user = User(email=f"hom94-corpus-{key}@example.com", display_name=f"Corpus {key}")
            db.add(user)
            db.flush()
            db.add(
                Membership(
                    workspace_id=workspace_a,
                    user_id=user.id,
                    role=role,
                    valid_from=NOW - timedelta(days=365),
                )
            )
            if assignment is not None:
                db.add(
                    TeamAssignment(
                        workspace_id=workspace_a,
                        team_id=team_ids[team_number],
                        user_id=user.id,
                        role=assignment,
                        assigned_by_user_id=tenant_a["user_id"],
                        valid_from=NOW - timedelta(days=365),
                    )
                )
            personas[key] = Persona(
                key=key,
                user_id=user.id,
                workspace="A",
                unrestricted=role == WorkspaceRole.ADMIN,
                supervised_teams=frozenset({team_number})
                if assignment == TeamRole.SUPERVISOR
                else frozenset(),
            )
        db.commit()
    personas["admin_b"] = Persona(
        key="admin_b",
        user_id=tenant_b["user_id"],
        workspace="B",
        unrestricted=True,
        supervised_teams=frozenset(),
    )

    # Every chain is seeded, its meeting row moves to its fixture truth (the
    # ACL attribution before indexing, so the snapshot stays correct), then
    # the real indexer writes the entries.  Deletion and retention come after
    # indexing (the indexer refuses a deleted meeting), like the events they
    # model.
    seeded: dict[str, dict] = {}
    for item in meetings:
        tenant = tenant_b if item["workspace"] == "B" else tenant_a
        setup = _seed_view(tenant, item["key"], texts=item["texts"], redacted=item["old_policy"])
        if item["workspace"] == "A":
            values: dict = {}
            if item["team"] is not None:
                values["team_id"] = team_ids[item["team"]]
            if item["agent"] == "agent2":
                values["agent_user_id"] = personas["agent2"].user_id
            if item["creator"] == "member":
                values["created_by_user_id"] = personas["member"].user_id
            if values:
                with inspection_session_factory()() as db:
                    db.execute(
                        update(Meeting).where(Meeting.id == setup["meeting_id"]).values(**values)
                    )
                    db.commit()
        _index(setup["text_view_id"], setup["workspace_id"])
        liveness: dict = {}
        if item["deleted"]:
            liveness["deleted_at"] = NOW
        if item["expired"]:
            liveness["retention_expires_at"] = NOW - timedelta(seconds=1)
        if liveness:
            with inspection_session_factory()() as db:
                db.execute(
                    update(Meeting).where(Meeting.id == setup["meeting_id"]).values(**liveness)
                )
                db.commit()
        seeded[item["key"]] = setup

    # The superseded meeting: revision 1 is indexed; stop it being current,
    # derive revision 2 and index it (the indexer supersedes revision 1).
    old_setup = seeded[f"superseded-{SUPERSEDED_MEETING:02d}"]
    with inspection_session_factory()() as db:
        db.execute(
            update(TranscriptRevision)
            .where(TranscriptRevision.id == old_setup["revision_id"])
            .values(is_current=False, superseded_at=NOW)
        )
        db.commit()
    superseded_key = f"superseded-{SUPERSEDED_MEETING:02d}"
    superseded_item = next(m for m in meetings if m["key"] == superseded_key)
    newer = _seed_view(
        tenant_a,
        f"{superseded_item['key']}-v2",
        texts=superseded_item["current_texts"],
        revision_number=2,
        meeting_id=old_setup["meeting_id"],
        reference_hours=1.0,
    )
    _index(newer["text_view_id"], workspace_a)

    # Pair the entries the indexer wrote with the fixture's metadata and
    # write one clustered vector per entry, directly.
    entries: list[CorpusEntry] = []
    with inspection_session_factory()() as db:
        rows = list(
            db.execute(
                select(
                    RetrievalEntry.id,
                    RetrievalEntry.meeting_id,
                    RetrievalEntry.workspace_id,
                    RetrievalEntry.text,
                    RetrievalEntry.superseded_at,
                )
                .where(
                    RetrievalEntry.workspace_id.in_([workspace_a, workspace_b]),
                    RetrievalEntry.index_version == INDEX_VERSION,
                )
                .order_by(RetrievalEntry.meeting_id, RetrievalEntry.sequence, RetrievalEntry.id)
            )
        )
        meeting_meta = {seeded[item["key"]]["meeting_id"]: item for item in meetings}
        embeddings: list[RetrievalEmbedding] = []
        text_clusters = {f"tok{cluster}": cluster for cluster in range(CLUSTERS)}
        for row in rows:
            item = meeting_meta[row.meeting_id]
            words = row.text.split()
            cluster = next(text_clusters[word] for word in words if word in text_clusters)
            number = int(next(word for word in words if word.startswith("e"))[1:])
            vector = _as_stored(_noisy(CENTRES[cluster], f"entry-{number}", 0.02))
            entries.append(
                CorpusEntry(
                    entry_id=row.id,
                    meeting_id=row.meeting_id,
                    workspace=item["workspace"],
                    cluster=cluster,
                    team=item["team"] if item["workspace"] == "A" else None,
                    agent_user_id=personas["agent2"].user_id if item["agent"] == "agent2" else None,
                    created_by_user_id=(
                        personas["member"].user_id if item["creator"] == "member" else None
                    ),
                    deleted=item["deleted"],
                    expired=item["expired"],
                    historical=row.superseded_at is not None,
                    old_policy=item["old_policy"],
                    text=row.text,
                    vector=vector,
                )
            )
            embeddings.append(
                RetrievalEmbedding(
                    workspace_id=row.workspace_id,
                    meeting_id=row.meeting_id,
                    entry_id=row.id,
                    embedding_version=EMBEDDING_VERSION,
                    provider=EMBEDDING_PROVIDER,
                    model=EMBEDDING_MODEL,
                    dimensions=DIMENSIONS,
                    vector=pack_vector(vector),
                    text_sha256=hashlib.sha256(row.text.encode("utf-8")).hexdigest(),
                    created_at=NOW,
                )
            )
        db.add_all(embeddings)
        db.commit()
    entries = _write_filter_metadata(meetings, seeded, entries)

    corpus = Corpus(
        workspace_a=workspace_a,
        workspace_b=workspace_b,
        team_ids=team_ids,
        entries=tuple(entries),
        personas=personas,
        embedding_version=EMBEDDING_VERSION,
    )
    _assert_shape(corpus)
    _assert_filter_shape(corpus)
    return corpus


def _assert_shape(corpus: Corpus) -> None:
    """The corpus the harness promises: sizes, spread and a usable control."""
    a = [e for e in corpus.entries if e.workspace == "A"]
    b = [e for e in corpus.entries if e.workspace == "B"]
    assert len(a) >= 200 and len({e.meeting_id for e in a}) >= 20
    assert len(b) >= 40
    assert any(e.deleted for e in a) and any(e.expired for e in a)
    assert any(e.historical for e in a) and any(e.old_policy for e in a)
    assert all(not (e.historical and e.old_policy) for e in a)
    for key in ("admin", "supervisor1", "agent2", "member", "admin_b"):
        persona = corpus.persona(key)
        allowed = [e for e in corpus.entries if authorized(persona, e)]
        assert len(allowed) >= 10, key
        # The persona can read entries in at least four clusters.
        assert len({e.cluster for e in allowed}) >= 4, key
    # The post-filter control must be able to fail: inside every cluster the
    # restricted personas' share of live entries stays below 9/10.
    live = [e for e in a if _alive(e)]
    for key in ("supervisor1", "agent2"):
        persona = corpus.persona(key)
        for cluster in range(CLUSTERS):
            pool = [e for e in live if e.cluster == cluster]
            allowed = [e for e in pool if _acl_allowed(persona, e)]
            assert pool and len(allowed) / len(pool) <= 0.9, (key, cluster)


def persona_context(corpus: Corpus, key: str) -> WorkspaceContext:
    """A real ``WorkspaceContext`` for a persona, loaded from live rows."""
    persona = corpus.persona(key)
    workspace_id = corpus.workspace_a if persona.workspace == "A" else corpus.workspace_b
    with inspection_session_factory()() as db:
        user = db.get(User, persona.user_id)
        workspace = db.get(Workspace, workspace_id)
        membership = db.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == persona.user_id,
            )
        )
        access = resolve_record_access(db, workspace=workspace, membership=membership, now=NOW)
        for obj in (user, workspace, membership):
            db.expunge(obj)
    actor = Actor(user=user, session=None, token_source="cookie")  # noqa: S106 - transport label
    return WorkspaceContext(
        actor=actor, workspace=workspace, membership=membership, record_access=access
    )


def recall_fraction(returned: list[str], truth: list[str], authorized_size: int) -> Decimal:
    """recall@k = |returned ∩ truth| / min(k, |authorized|), exact decimal."""
    hit = len(set(returned) & set(truth))
    return Decimal(hit) / Decimal(min(10, authorized_size))
