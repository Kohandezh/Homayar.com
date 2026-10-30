"""The stored trend judgment of each analysis run (HOM-290; ADR 0047).

What the HOM-79 rules decide from a run's stored extraction row (``JudgedContent``: steps 6 to
9 and the per-issue states) depends only on that row, which never changes, and on the rules:
the trend definition (its digest) and ``JUDGMENT_VERSION``.  So it is stored once per run and
rules version in ``trend_extraction_judgments``, as ``content_key``: the hex SHA-256 of the
canonical JSON of the content.  Equal contents have equal keys.

* **Write.** The analysis and the HOM-63 recompute write the row in the same transaction as
  the extraction row (``jobs/processor.py``, ``persist_analysis``).  ``backfill_judgments``
  writes the rows that are missing: after a deploy that changes the trend definition or
  ``JUDGMENT_VERSION``, and for the runs analysed before this table.
* **Read.** Statement P of the trend routes (``issue_trend_service``) joins
  ``current_judgments``, the workspace's rows of the current rules read as one range of
  ``ix_trend_judgment_current``, and gets each run's key instead of its JSON.
  ``stored_contents`` then judges one representative extraction per distinct key with
  ``SignatureJudge.content`` (the only rules source) and gives that content to every run of
  the key: the key is trusted.  A run without a row of the current rules is judged as before
  (the HOM-312 cache and Python), so the answers are the same with rows, without rows and with
  a mix.
* **Invariant.** A row's run has the row's extraction (the writers take both from the same
  extraction row).  A row whose run has no extraction row (only a manual SQL change makes one)
  is read as no extraction row, and logged.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from enum import Enum
from typing import Any

from sqlalchemy import CTE, and_, insert, select
from sqlalchemy.orm import Session, sessionmaker

from backend.database.saas_models import (
    AnalysisExtraction,
    AnalysisRun,
    ProcessingJob,
    TrendExtractionJudgment,
)
from backend.database.tenant_context import (
    SERVICE_SCOPE_RECOVERY,
    bind_service_scope,
    bind_tenant_context,
)
from backend.issue_trends.counting import JUDGMENT_VERSION, JudgedContent, SignatureJudge
from backend.issue_trends.definition import TrendDefinition
from backend.services.coverage_service import among_ids
from backend.services.logging_service import logger

BATCH_SIZE = 500

J = TrendExtractionJudgment


def _definition(definition: TrendDefinition | None) -> TrendDefinition:
    """``definition``, or the one pinned definition of every trend answer: the one the read path
    filters on (``issue_trend_service.trend_definition``; imported at call time, because that
    module imports this one)."""
    if definition is not None:
        return definition
    from backend.services.issue_trend_service import trend_definition

    return trend_definition()


# --- the key ----------------------------------------------------------------------------------


def _plain(value: object) -> object:
    """``dataclasses.asdict`` with enum values as strings, without its deep copies: every field
    of every dataclass, so a new field of ``JudgedContent`` is in the key by itself."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


def content_key(content: JudgedContent) -> str:
    """The 64-character lowercase hex SHA-256 of the canonical JSON of ``content``: its fields
    as plain JSON (enum values as strings, the dimension, issue and family judgments as lists
    in their order), sorted keys, no spaces, ASCII only."""
    document = json.dumps(
        _plain(content),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(document.encode("ascii")).hexdigest()


# --- writing ----------------------------------------------------------------------------------


def _values(
    extraction: AnalysisExtraction,
    definition: TrendDefinition,
    judge: SignatureJudge,
    keys: dict[JudgedContent, str] | None = None,
) -> dict[str, Any]:
    """The row of ``extraction``.  ``keys``: the keys already worked out (equal contents are
    shared objects of ``judge``, so a backfill works each key out once)."""
    content = judge.content(extraction.result)
    if keys is None:
        key = content_key(content)
    elif (key := keys.get(content)) is None:
        key = keys[content] = content_key(content)
    return {
        "workspace_id": extraction.workspace_id,
        "extraction_id": extraction.id,
        "analysis_run_id": extraction.analysis_run_id,
        "definition_id": definition.definition_id,
        "definition_version": definition.version,
        "definition_digest": definition.digest,
        "judgment_version": JUDGMENT_VERSION,
        "content_key": key,
    }


def write_judgment(
    db: Session, *, extraction: AnalysisExtraction, definition: TrendDefinition | None = None
) -> TrendExtractionJudgment | None:
    """Judge ``extraction.result`` with ``SignatureJudge(definition).content`` and add the row
    of its run for ``definition`` (the current trend definition when None) and
    ``JUDGMENT_VERSION``.  ``None`` when that row already exists.  The caller commits."""
    definition = _definition(definition)
    if extraction.id is None:
        db.flush()  # the extraction's id (a Python default) is set on flush
    exists = db.scalar(
        select(J.id).where(
            J.analysis_run_id == extraction.analysis_run_id,
            J.definition_digest == definition.digest,
            J.judgment_version == JUDGMENT_VERSION,
        )
    )
    if exists is not None:
        return None
    row = J(**_values(extraction, definition, SignatureJudge(definition)))
    db.add(row)
    return row


def _insert_missing(db: Session, rows: Sequence[Mapping[str, Any]]) -> int:
    """Insert ``rows``; a row whose (run, digest, version) exists by now is skipped, not an
    error (another writer was first).  Returns how many were inserted."""
    if not rows:
        return 0
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    else:  # pragma: no cover - PostgreSQL in production, SQLite for local runs
        return len(db.execute(insert(J.__table__).returning(J.id), list(rows)).all())
    # One cached statement, sent as an executemany (``insertmanyvalues`` batches it).
    statement = (
        dialect_insert(J.__table__)
        .on_conflict_do_nothing(
            index_elements=["analysis_run_id", "definition_digest", "judgment_version"]
        )
        .returning(J.__table__.c.id)
    )
    return len(db.execute(statement, list(rows)).all())


def backfill_judgments(db: Session, *, workspace_id: str, batch_size: int = BATCH_SIZE) -> int:
    """Write the missing rows of ``workspace_id`` for the current trend definition and
    ``JUDGMENT_VERSION``: one per extraction row of a current analysis run that has none.
    Binds the workspace as the tenant and commits after each batch of ``batch_size``.  Returns
    the number of rows written: a second run writes 0.  Safe while the application writes: a
    row written meanwhile by someone else is skipped."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    bind_tenant_context(db, workspace_id=workspace_id)
    definition = _definition(None)
    judge = SignatureJudge(definition)
    keys: dict[JudgedContent, str] = {}
    written = 0
    after = ""
    while True:
        # The ids first (keyset by extraction id), then the JSON of this batch only.
        ids = list(
            db.scalars(
                select(AnalysisExtraction.id)
                .join(
                    AnalysisRun,
                    and_(
                        AnalysisRun.id == AnalysisExtraction.analysis_run_id,
                        AnalysisRun.is_current.is_(True),
                        AnalysisRun.workspace_id == workspace_id,
                    ),
                )
                .outerjoin(
                    J,
                    and_(
                        J.analysis_run_id == AnalysisExtraction.analysis_run_id,
                        J.definition_digest == definition.digest,
                        J.judgment_version == JUDGMENT_VERSION,
                    ),
                )
                .where(
                    AnalysisExtraction.workspace_id == workspace_id,
                    AnalysisExtraction.id > after,
                    J.id.is_(None),
                )
                .order_by(AnalysisExtraction.id)
                .limit(batch_size)
            )
        )
        if not ids:
            return written
        after = ids[-1]
        extractions = db.scalars(
            select(AnalysisExtraction).where(
                AnalysisExtraction.workspace_id == workspace_id,
                among_ids(db, AnalysisExtraction.id, ids, "trend_backfill_ids"),
            )
        ).all()
        rows = [_values(extraction, definition, judge, keys) for extraction in extractions]
        written += _insert_missing(db, sorted(rows, key=lambda row: row["extraction_id"]))
        db.commit()  # the tenant is bound again at the next transaction
        db.expunge_all()


# --- reading ----------------------------------------------------------------------------------


def current_judgments(workspace_id: str, definition: TrendDefinition) -> CTE:
    """The workspace's rows of ``definition`` and ``JUDGMENT_VERSION`` (run id and key), read
    once as one range of ``ix_trend_judgment_current`` (index-only) for a hash join."""
    return (
        select(J.analysis_run_id, J.content_key)
        .where(
            J.workspace_id == workspace_id,
            J.definition_digest == definition.digest,
            J.judgment_version == JUDGMENT_VERSION,
        )
        .cte("trend_current_judgments")
        .prefix_with("MATERIALIZED", dialect="postgresql")
    )


def stored_contents(
    db: Session,
    *,
    workspace_id: str,
    keys: Mapping[str, str],
    build: Callable[[object], JudgedContent],
) -> tuple[dict[str, JudgedContent], set[str]]:
    """The content of each run of ``keys`` (run id -> its stored key): per distinct key, the
    stored extraction of one run of the key (the smallest run id) is judged with ``build``
    (``SignatureJudge.content``), and that content is every run's of the key.  One statement
    reads the representatives.

    Returns ``(contents, missing)``: ``missing`` holds the runs found to have no extraction row
    (a row against the invariant), read as no row; their key takes its next run instead (one
    more statement, only then)."""
    runs_of: dict[str, list[str]] = {}
    for run_id, key in sorted(keys.items()):
        runs_of.setdefault(key, []).append(run_id)
    contents: dict[str, JudgedContent] = {}
    missing: set[str] = set()
    tried = dict.fromkeys(runs_of, 0)  # key -> index of its next representative
    while tried:
        samples = {key: runs_of[key][index] for key, index in tried.items()}
        results = dict(
            db.execute(
                select(AnalysisExtraction.analysis_run_id, AnalysisExtraction.result).where(
                    AnalysisExtraction.workspace_id == workspace_id,
                    among_ids(
                        db,
                        AnalysisExtraction.analysis_run_id,
                        sorted(samples.values()),
                        "trend_key_samples",
                    ),
                )
            ).all()
        )
        for key, run_id in samples.items():
            if run_id in results:
                content = build(results[run_id])
                for other in runs_of[key][tried[key] :]:
                    if other not in missing:
                        contents[other] = content
                del tried[key]
                continue
            missing.add(run_id)
            tried[key] += 1
            if tried[key] >= len(runs_of[key]):
                del tried[key]
    if missing:
        logger.warning(
            "TREND_JUDGMENT_WITHOUT_EXTRACTION workspace_id=%s runs=%s",
            workspace_id,
            len(missing),
        )
    return contents, missing


# --- the command line --------------------------------------------------------------------------


def _workspaces_with_jobs(session_factory: sessionmaker[Session]) -> list[str]:
    """Every workspace with a processing job (the read-only ``recovery`` discovery scope, as
    the HOM-312 warm-up): a run with an extraction row comes from a job."""
    with session_factory() as db:
        bind_service_scope(db, SERVICE_SCOPE_RECOVERY)
        return list(
            db.scalars(
                select(ProcessingJob.workspace_id).distinct().order_by(ProcessingJob.workspace_id)
            )
        )


def run_backfill(
    session_factory: sessionmaker[Session],
    *,
    workspace_id: str | None = None,
    batch_size: int = BATCH_SIZE,
) -> dict[str, int]:
    """``backfill_judgments`` for one workspace, or for every workspace with a job.  Counts
    only."""
    workspaces = [workspace_id] if workspace_id else _workspaces_with_jobs(session_factory)
    written = 0
    for workspace in workspaces:
        with session_factory() as db:
            written += backfill_judgments(db, workspace_id=workspace, batch_size=batch_size)
    return {"workspaces": len(workspaces), "written": written}


def main(
    argv: list[str] | None = None, *, session_factory: sessionmaker[Session] | None = None
) -> int:
    """``python -m backend.services.trend_judgment_store backfill [--workspace ID]
    [--batch-size N]``: prints the counts as JSON, never content."""
    parser = argparse.ArgumentParser(prog="python -m backend.services.trend_judgment_store")
    parser.add_argument("command", choices=("backfill",))
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    if session_factory is None:  # pragma: no cover - operator entrypoint
        from backend.database.relational import configure_database, get_session_factory

        configure_database()
        session_factory = get_session_factory()
    counts = run_backfill(session_factory, workspace_id=args.workspace, batch_size=args.batch_size)
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
