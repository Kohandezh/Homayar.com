"""HOM-58 PR 1: the conversation columns on ``meetings`` and their one migration.

A conversation is a row of ``meetings``; the migration only adds columns, so every
id, foreign key and API stays.  The tests check:

* the five columns, three named checks and two indexes, the same in the ORM schema
  (``create_all``) and in the migrated schema;
* the backfill (``voicemail`` from ``source = 'voicemail'``, ``call`` for every other
  row) keeps every row, id and child row;
* the database refuses the deny cases by the named constraint, and the closest allow
  case passes;
* an old-shape INSERT and an ORM ``Meeting`` without the new fields get ``call``;
* downgrade and upgrade again work with rows present, and ``meetings`` keeps its
  enabled and forced row-level security;
* the backfill also works for a table owner that is not a superuser (the deployment
  shape of ADR 0005), where the forced policy hides every row.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from backend.config import Settings
from backend.database.rls import APP_ROLE
from backend.database.saas_models import (
    ConversationDirection,
    ConversationType,
    Meeting,
    MeetingStatus,
    TranscriptRevision,
    TranscriptSource,
)
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user, register_user

NEW_REVISION = "6d4ec2d2e498"
BASE_REVISION = "a7c3e9f5b2d1"
HEAD_REVISION = "5067ea773c0b"  # HOM-56: scorecard results, after HOM-89
NEW_COLUMNS = (
    "conversation_type",
    "direction",
    "external_source_system",
    "external_conversation_id",
    "started_at",
)
CONSTRAINTS = ("ck_meeting_conversation_type", "ck_meeting_direction", "ck_meeting_external_ref")
INDEXES = ("ix_meeting_workspace_external", "ix_meeting_workspace_type_created")
OWNER_PROBE_ROLE = "homayar_hom58_owner_probe"
VOICEMAIL_UPDATE = "UPDATE meetings SET conversation_type = 'voicemail' WHERE source = 'voicemail'"


def _requires_postgres() -> None:
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")


def _scalar(sql: str, **params):
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


def _rls_flags(connection: Connection | None = None) -> tuple[bool, bool]:
    """``(relrowsecurity, relforcerowsecurity)`` of ``meetings``."""
    sql = text(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relname = 'meetings' AND relkind = 'r'"
    )
    if connection is not None:
        return tuple(connection.execute(sql).one())
    with inspection_session_factory()() as db:
        return tuple(db.execute(sql).one())


def _tenant(client, email_outbox, label: str) -> dict:
    credentials = register_user(client, email_outbox, email=f"conversation-{label}@example.com")
    session = login_user(client, credentials)
    return {"user_id": session["user"]["id"], "workspace_id": session["workspaces"][0]["id"]}


def _alembic(monkeypatch, settings: Settings):
    from alembic.config import Config

    from backend.config import reset_settings_cache
    from backend.observability.release import PROJECT_ROOT

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _insert(tenant: dict, meeting_id: str, source: str = "upload", **extra: object) -> None:
    """One meeting by raw SQL, as the DBA.  With no ``extra`` it is the old INSERT
    shape that every existing writer uses (none of the new columns named)."""
    columns = {
        "id": meeting_id,
        "workspace_id": tenant["workspace_id"],
        "created_by_user_id": tenant["user_id"],
        "title": meeting_id,
        "language": "fa",
        "status": "COMPLETED",
        "duration_seconds": 5,
        "source": source,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        **extra,
    }
    names = ", ".join(columns)
    values = ", ".join(f":{name}" for name in columns)
    with inspection_session_factory()() as db:
        db.execute(text(f"INSERT INTO meetings ({names}) VALUES ({values})"), columns)  # noqa: S608
        db.commit()


def _conversation(meeting_id: str) -> tuple:
    with inspection_session_factory()() as db:
        return db.execute(
            text(f"SELECT {', '.join(NEW_COLUMNS)} FROM meetings WHERE id = :m"),  # noqa: S608
            {"m": meeting_id},
        ).one()


def _meetings() -> dict[str, str]:
    with inspection_session_factory()() as db:
        return dict(db.execute(text("SELECT id, source FROM meetings")).all())


def _shape() -> dict:
    """The new columns, checks and indexes of ``meetings`` as the catalog shows them."""
    with inspection_session_factory()() as db:
        columns = db.execute(
            text(
                "SELECT column_name, data_type, character_maximum_length, is_nullable, "
                "column_default FROM information_schema.columns "
                "WHERE table_name = 'meetings' AND column_name = ANY(:names)"
            ),
            {"names": list(NEW_COLUMNS)},
        ).all()
        checks = db.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'meetings'::regclass AND conname = ANY(:names)"
            ),
            {"names": list(CONSTRAINTS)},
        ).all()
        indexes = db.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = 'meetings' AND indexname = ANY(:names)"
            ),
            {"names": list(INDEXES)},
        ).all()
    return {
        "columns": {name: tuple(rest) for name, *rest in columns},
        "checks": dict(checks),
        "indexes": dict(indexes),
    }


EXPECTED_COLUMNS = {
    "conversation_type": ("character varying", 20, "NO", "'call'::character varying"),
    "direction": ("character varying", 12, "YES", None),
    "external_source_system": ("character varying", 60, "YES", None),
    "external_conversation_id": ("character varying", 200, "YES", None),
    "started_at": ("timestamp with time zone", None, "YES", None),
}
EXPECTED_INDEXES = {
    "ix_meeting_workspace_external": (
        "CREATE INDEX ix_meeting_workspace_external ON public.meetings USING btree "
        "(workspace_id, external_source_system, external_conversation_id)"
    ),
    "ix_meeting_workspace_type_created": (
        "CREATE INDEX ix_meeting_workspace_type_created ON public.meetings USING btree "
        "(workspace_id, conversation_type, created_at)"
    ),
}


def _assert_head_shape() -> dict:
    shape = _shape()
    assert shape["columns"] == EXPECTED_COLUMNS
    assert set(shape["checks"]) == set(CONSTRAINTS)
    assert shape["indexes"] == EXPECTED_INDEXES  # both non-unique
    return shape


# Deny cases (refused by the named constraint, or NOT NULL for an explicit NULL) and
# their closest allow cases (SPEC section 3).
DENIED = [
    ({"conversation_type": "Call"}, "ck_meeting_conversation_type"),
    ({"conversation_type": "sms"}, "ck_meeting_conversation_type"),
    ({"conversation_type": ""}, "ck_meeting_conversation_type"),
    ({"conversation_type": None}, 'null value in column "conversation_type"'),
    ({"direction": "unknown"}, "ck_meeting_direction"),
    ({"direction": "INBOUND"}, "ck_meeting_direction"),
    ({"external_source_system": "odoo"}, "ck_meeting_external_ref"),
    ({"external_conversation_id": "C-1"}, "ck_meeting_external_ref"),
]
ALLOWED = [
    {"conversation_type": "call"},
    {"conversation_type": "meeting"},
    {"conversation_type": "voicemail"},
    {"conversation_type": "other"},
    {"direction": None},
    {"direction": "inbound"},
    {"direction": "outbound"},
    {"direction": "internal"},
    {"external_source_system": "odoo", "external_conversation_id": "C-1"},
    {"external_source_system": None, "external_conversation_id": None},
    {"started_at": datetime(2026, 10, 1, 9, 30, tzinfo=UTC)},
]


def _assert_rules(tenant: dict, label: str) -> None:
    for number, (extra, error) in enumerate(DENIED):
        with pytest.raises(IntegrityError, match=error):
            _insert(tenant, f"{label}-deny-{number}", **extra)
    for number, extra in enumerate(ALLOWED):
        _insert(tenant, f"{label}-allow-{number}", **extra)
        stored = dict(zip(NEW_COLUMNS, _conversation(f"{label}-allow-{number}"), strict=True))
        assert {key: stored[key] for key in extra} == extra

    # The old INSERT shape gets the defaults.
    _insert(tenant, f"{label}-old-shape")
    assert _conversation(f"{label}-old-shape") == ("call", None, None, None, None)

    # The external reference is not unique: a re-sent conversation inserts again.
    same = {"external_source_system": "odoo", "external_conversation_id": "C-2"}
    _insert(tenant, f"{label}-twice-1", **same)
    _insert(tenant, f"{label}-twice-2", **same)
    twice = (
        "SELECT count(*) FROM meetings WHERE workspace_id = :w AND external_conversation_id = 'C-2'"
    )
    assert _scalar(twice, w=tenant["workspace_id"]) == 2

    denied = [f"{label}-deny-{number}" for number in range(len(DENIED))]
    assert _scalar("SELECT count(*) FROM meetings WHERE id = ANY(:ids)", ids=denied) == 0


# --- the ORM ---------------------------------------------------------------------------


def test_the_allowed_values_live_in_two_enums():
    assert [member.value for member in ConversationType] == [
        "call",
        "meeting",
        "voicemail",
        "other",
    ]
    assert [member.value for member in ConversationDirection] == [
        "inbound",
        "outbound",
        "internal",
    ]


def test_the_orm_declares_the_columns_checks_and_indexes():
    table = Meeting.__table__
    conversation_type = table.c.conversation_type
    assert conversation_type.type.length == 20 and not conversation_type.nullable
    assert conversation_type.default.arg == "call"
    assert conversation_type.server_default.arg == "call"
    for name, length in (
        ("direction", 12),
        ("external_source_system", 60),
        ("external_conversation_id", 200),
    ):
        assert table.c[name].type.length == length and table.c[name].nullable
        assert table.c[name].default is None and table.c[name].server_default is None
    assert table.c.started_at.type.timezone and table.c.started_at.nullable
    named = {constraint.name for constraint in table.constraints}
    assert set(CONSTRAINTS) <= named
    indexes = {index.name: index for index in table.indexes}
    assert [c.name for c in indexes["ix_meeting_workspace_external"].columns] == [
        "workspace_id",
        "external_source_system",
        "external_conversation_id",
    ]
    assert [c.name for c in indexes["ix_meeting_workspace_type_created"].columns] == [
        "workspace_id",
        "conversation_type",
        "created_at",
    ]
    assert not any(indexes[name].unique for name in INDEXES)


def test_an_orm_meeting_without_the_new_fields_is_a_call(client, email_outbox):
    tenant = _tenant(client, email_outbox, "orm")
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=tenant["workspace_id"],
            created_by_user_id=tenant["user_id"],
            title="orm",
            status=MeetingStatus.COMPLETED,
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        assert meeting.conversation_type == "call"
        assert meeting.direction is None
        assert meeting.external_source_system is None
        assert meeting.external_conversation_id is None
        assert meeting.started_at is None


# --- the ORM schema (create_all) on PostgreSQL ---------------------------------------


@pytest.mark.postgres
def test_the_orm_schema_has_the_columns_and_refuses_the_deny_cases(client, email_outbox):
    _requires_postgres()
    tenant = _tenant(client, email_outbox, "rules")
    _assert_head_shape()
    _assert_rules(tenant, "orm")


# --- the migration ---------------------------------------------------------------------


@pytest.mark.postgres
def test_the_migration_backfills_and_round_trips_with_rows_present(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    from alembic import command

    from backend.config import reset_settings_cache
    from backend.observability.release import code_migration_head

    assert code_migration_head() == HEAD_REVISION
    tenant = _tenant(client, email_outbox, "migration")
    orm_shape = _assert_head_shape()
    assert _rls_flags() == (True, True)

    # A meeting written at the head with every new field set, and a child row of it.
    _insert(
        tenant,
        "set-at-head",
        direction="inbound",
        external_source_system="odoo",
        external_conversation_id="C-9",
        started_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    with inspection_session_factory()() as db:
        db.add(
            TranscriptRevision(
                workspace_id=tenant["workspace_id"],
                meeting_id="set-at-head",
                revision_number=1,
                source=TranscriptSource.ASR,
                reason="processing",
                language="fa",
                segment_count=0,
                is_current=True,
            )
        )
        db.commit()
    child = "SELECT count(*) FROM transcript_revisions WHERE meeting_id = 'set-at-head'"

    config = _alembic(monkeypatch, settings)
    try:
        command.stamp(config, "head")
        command.downgrade(config, BASE_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        assert _shape() == {"columns": {}, "checks": {}, "indexes": {}}
        assert set(_meetings()) == {"set-at-head"}
        assert _scalar(child) == 1

        # Rows written by the old code: one voicemail, the rest not.
        _insert(tenant, "old-voicemail", source="voicemail")
        _insert(tenant, "old-upload", source="upload")
        _insert(tenant, "old-api", source="api")
        before = _meetings()
        assert len(before) == 4

        command.upgrade(config, "head")
        assert _scalar("SELECT version_num FROM alembic_version") == HEAD_REVISION
        assert _shape() == orm_shape  # the migrated schema equals the ORM schema
        assert _rls_flags() == (True, True)  # the backfill set FORCE again
        assert _meetings() == before  # every row and id kept
        assert _conversation("old-voicemail") == ("voicemail", None, None, None, None)
        for meeting_id in ("old-upload", "old-api", "set-at-head"):
            assert _conversation(meeting_id) == ("call", None, None, None, None)
        assert _scalar(child) == 1
        assert (
            _scalar("SELECT meeting_id FROM transcript_revisions WHERE meeting_id = 'set-at-head'")
            == "set-at-head"
        )

        # The migrated schema refuses the same deny cases.
        _assert_rules(tenant, "migrated")
        after_rules = _meetings()

        # Downgrade and upgrade again with rows present.
        command.downgrade(config, BASE_REVISION)
        assert _meetings() == after_rules
        command.upgrade(config, "head")
        assert _meetings() == after_rules
        assert _shape() == orm_shape
        assert _rls_flags() == (True, True)
        assert _conversation("old-voicemail")[0] == "voicemail"
    finally:
        reset_settings_cache()


def _backfill_step():
    module = importlib.import_module(
        f"backend.migrations.versions.{NEW_REVISION}_conversation_columns"
    )
    return module.backfill_voicemail_type


@pytest.mark.postgres
def test_the_backfill_works_for_a_plain_table_owner(client, email_outbox):
    """Deployment shape: the migration role owns ``meetings`` and is not a superuser."""
    _requires_postgres()
    tenant = _tenant(client, email_outbox, "owner")
    # The rows as they are right after the columns are added: all 'call'.
    _insert(tenant, "owner-voicemail", source="voicemail")
    _insert(tenant, "owner-upload", source="upload")
    assert _conversation("owner-voicemail")[0] == "call"
    owner = _scalar("SELECT tableowner FROM pg_tables WHERE tablename = 'meetings'")
    backfill = _backfill_step()

    engine = create_engine(configured_database_url(), poolclass=NullPool)
    connection = engine.connect()
    try:
        # Member of homayar_app so it can read share_links, which the policy consults.
        connection.execute(text(f"DROP ROLE IF EXISTS {OWNER_PROBE_ROLE}"))
        connection.execute(
            text(
                f"CREATE ROLE {OWNER_PROBE_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS IN ROLE {APP_ROLE}"
            )
        )
        connection.execute(text(f"ALTER TABLE meetings OWNER TO {OWNER_PROBE_ROLE}"))
        connection.commit()

        connection.execute(text(f"SET ROLE {OWNER_PROBE_ROLE}"))
        posture = connection.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        assert tuple(posture) == (False, False)
        assert _rls_flags(connection) == (True, True)
        # Control: under FORCE the owner sees no row, so the plain update changes 0 rows.
        assert connection.execute(text(VOICEMAIL_UPDATE)).rowcount == 0
        # The data step lifts FORCE for its update and sets it again.
        assert backfill(connection) == 1
        assert _rls_flags(connection) == (True, True)
        connection.commit()
    finally:
        connection.rollback()
        connection.execute(text("RESET ROLE"))
        connection.execute(text(f'ALTER TABLE meetings OWNER TO "{owner}"'))
        connection.execute(text(f"DROP ROLE IF EXISTS {OWNER_PROBE_ROLE}"))
        connection.commit()
        connection.close()
        engine.dispose()

    assert _scalar("SELECT tableowner FROM pg_tables WHERE tablename = 'meetings'") == owner
    assert _rls_flags() == (True, True)
    assert _conversation("owner-voicemail")[0] == "voicemail"
    assert _conversation("owner-upload")[0] == "call"
