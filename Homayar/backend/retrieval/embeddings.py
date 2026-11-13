"""Versioned embeddings of the retrieval index (HOM-93, ADR 0068 D3, D4, D5).

``postgres:17-alpine`` has no pgvector, so a vector is stored as portable
bytes: float32 little-endian, exactly ``4 * dimensions`` of them.  The database
checks that length.

:func:`embedding_version` names one embedding space: the sha256 of a canonical
JSON of the provider, the model, the dimensions and the index version.
``ProviderIdentity.params`` is not hashed.  For the shipped adapter it is the
endpoint URL, and moving the embedding server must not invalidate every vector
(SPEC Revision 2 R1).  A new model or index version never overwrites an old
vector; it writes new rows.

:func:`embed_entries` embeds live entries through an injected
:class:`~backend.providers.base.EmbeddingProvider`.  There is no cloud
fallback.  A provider fault is reported in the outcome, never raised.  An
embedding row is only ever inserted, never updated (the ORM refuses updates).
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from backend.database.saas_models import RetrievalEmbedding, RetrievalEntry
from backend.providers.base import EmbeddingProvider, ProviderError, ProviderIdentity
from backend.retrieval.index import INDEX_VERSION
from backend.services.logging_service import logger

MAX_DIMENSIONS = 4096
# The largest finite float32.  A larger value would overflow when packed.
FLOAT32_MAX = struct.unpack("<f", b"\xff\xff\x7f\x7f")[0]
REASON_DIMENSIONS_MISMATCH = "embedding_dimensions_mismatch"
# The provider answered, but not with one valid float32 vector per text.
REASON_RESPONSE_INVALID = "embedding_response_invalid"


def embedding_version(identity: ProviderIdentity, *, dimensions: int, index_version: str) -> str:
    """The sha256 hex that names one embedding space (no ``params``, see above)."""
    payload = {
        "provider": identity.provider,
        "model": identity.model,
        "dimensions": dimensions,
        "index_version": index_version,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def finite_values(values: Sequence[float]) -> list[float]:
    """The values as floats.  Every refusal is a ValueError.

    Refused: an empty vector, more than 4096 values, a bool, a non-number, a
    non-finite value, and a value outside the float32 range (it would
    overflow in :func:`pack_vector`).
    """
    if len(values) == 0:
        raise ValueError("a vector needs at least one value")
    if len(values) > MAX_DIMENSIONS:
        raise ValueError(f"a vector holds at most {MAX_DIMENSIONS} values")
    numbers: list[float] = []
    for value in values:
        # bool is a subclass of int, so it is refused by name.
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("a vector value must be a number")
        try:
            number = float(value)
        except OverflowError:  # an int too large for a float, for example 10**400
            raise ValueError("a vector value must fit in float32") from None
        if not math.isfinite(number):
            raise ValueError("a vector value must be finite")
        if abs(number) > FLOAT32_MAX:
            raise ValueError("a vector value must fit in float32")
        numbers.append(number)
    return numbers


def pack_vector(values: Sequence[float]) -> bytes:
    """float32 little-endian bytes of ``values`` (refusals as :func:`finite_values`)."""
    numbers = finite_values(values)
    return struct.pack(f"<{len(numbers)}f", *numbers)


def unpack_vector(blob: bytes, dimensions: int) -> list[float]:
    """The float values of a stored vector.  Refuses a wrong byte length."""
    if not 1 <= dimensions <= MAX_DIMENSIONS or len(blob) != 4 * dimensions:
        raise ValueError("the blob does not hold exactly `dimensions` float32 values")
    return list(struct.unpack(f"<{dimensions}f", blob))


@dataclass(frozen=True)
class EmbedOutcome:
    """What the embedding writer reports (states and counts only)."""

    state: str  # "embedded" (no batch failed), "failed"
    reason_code: str | None
    embedding_version: str
    written: int  # rows inserted by this call
    skipped: int  # entries that already had a row for this version


async def embed_entries(
    db: Session,
    *,
    provider: EmbeddingProvider,
    workspace_id: str,
    entry_ids: Sequence[str],
    dimensions: int,
    now: datetime,
    batch_size: int = 32,
) -> EmbedOutcome:
    """Embed the live entries of ``entry_ids`` that this version still lacks.

    Only entries of ``workspace_id``, in ``entry_ids``, with ``superseded_at``
    NULL and without a row for this version are embedded.  The text sent is
    ``entry.text``.  A failing batch (a ``ProviderError``, a wrong vector
    length or an invalid answer) writes nothing for itself and ends the run
    with ``state="failed"``.  Rows of earlier batches stay in the caller's
    transaction.  ``workspace_id`` and ``meeting_id`` of a row are copied from
    its entry, never from the arguments.
    """
    if not 1 <= dimensions <= MAX_DIMENSIONS:
        raise ValueError(f"dimensions must be 1..{MAX_DIMENSIONS}")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    identity = provider.identity()
    version = embedding_version(identity, dimensions=dimensions, index_version=INDEX_VERSION)
    has_row = exists().where(
        RetrievalEmbedding.entry_id == RetrievalEntry.id,
        RetrievalEmbedding.embedding_version == version,
    )
    wanted = (
        RetrievalEntry.workspace_id == workspace_id,
        RetrievalEntry.id.in_(list(entry_ids)),
        RetrievalEntry.superseded_at.is_(None),
    )
    skipped = db.scalar(select(func.count()).select_from(RetrievalEntry).where(*wanted, has_row))
    pending = list(
        db.scalars(select(RetrievalEntry).where(*wanted, ~has_row).order_by(RetrievalEntry.id))
    )
    skipped = skipped or 0
    written = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        try:
            vectors = await provider.embed([entry.text for entry in batch])
        except ProviderError as exc:
            logger.warning("RETRIEVAL_EMBED_PROVIDER_FAILED code=%s written=%s", exc.code, written)
            return EmbedOutcome("failed", exc.code, version, written, skipped)
        if (
            not isinstance(vectors, list)
            or len(vectors) != len(batch)
            or not all(isinstance(vector, list | tuple) for vector in vectors)
        ):
            logger.warning("RETRIEVAL_EMBED_RESPONSE_INVALID batch=%s", len(batch))
            return EmbedOutcome("failed", REASON_RESPONSE_INVALID, version, written, skipped)
        if any(len(vector) != dimensions for vector in vectors):
            logger.warning(
                "RETRIEVAL_EMBED_DIMENSIONS_MISMATCH dimensions=%s batch=%s",
                dimensions,
                len(batch),
            )
            return EmbedOutcome("failed", REASON_DIMENSIONS_MISMATCH, version, written, skipped)
        # Pack the whole batch first, so a bad value adds no row of it.
        try:
            blobs = [pack_vector(vector) for vector in vectors]
        except ValueError:
            logger.warning("RETRIEVAL_EMBED_RESPONSE_INVALID batch=%s", len(batch))
            return EmbedOutcome("failed", REASON_RESPONSE_INVALID, version, written, skipped)
        for entry, blob in zip(batch, blobs, strict=True):
            db.add(
                RetrievalEmbedding(
                    workspace_id=entry.workspace_id,
                    meeting_id=entry.meeting_id,
                    entry_id=entry.id,
                    embedding_version=version,
                    provider=identity.provider,
                    model=identity.model,
                    dimensions=dimensions,
                    vector=blob,
                    text_sha256=entry.text_sha256,
                    created_at=now,
                )
            )
        db.flush()  # the batch is written before the next provider call
        written += len(batch)
    return EmbedOutcome("embedded", None, version, written, skipped)
