"""The trend judgment cache of one API process (HOM-312, option N; ADR 0043).

What a conversation's stored extraction row decides (``JudgedContent``: steps 6 to 9 and the
per-issue states) depends only on that row, which never changes once written
(``services/analysis_extraction.py``), and on the judging rules (``trend_definition()`` and
``JUDGMENT_VERSION``).  So it is kept here, per analysis run, and reused by the next request:
statement P then reads the JSON only for the runs not in the cache.  The conversation-level
facts (no run, stale, mock, no row, counted copy) are never cached; they are read per request.

* **Where.** One cache per app (``create_app`` stores it on ``app.state``), so per worker
  process.  Never a module global: tests reuse fixed ids across datasets.
* **Key.** A map per ``CacheKey`` (organization, workspace, definition digest, judgment
  version), and in it the run id.  A run id is a ``uuid4`` of one workspace, looked up only
  after the caller's own scoped statement P returned it.  A re-analysis or a recompute makes a
  new run, so a new key; a new rules version or definition makes a new map.
* **Cap.** ``max_entries`` entries over all maps (both kinds of entry, ``trend_judgment`` and
  ``coverage_verdict``).  Past it, the oldest entries of the least recently used map go
  first (LRU).  An evicted entry is only a miss next time.  The warm-up inserts with
  ``evict=False``: it fills only the free room (``room``) and never evicts.
* **Threads.** The routes are sync, so FastAPI runs them in its thread pool.  One
  ``threading.Lock`` guards every read, insert and eviction of the maps; no thread reads the
  database, judges or waits while holding it.  Before judging a miss, a request claims the
  run under the lock with its own ``threading.Event``; another request that needs the same run
  waits on that event (``wait_seconds``), then reads the entry, or judges the run itself from
  its own row when the first one failed.  A thread sets its own event before it waits on
  others, so two threads never wait on each other.  An entry is one immutable tuple, inserted
  only once its judgment is complete.
* **Eviction race.** ``pin`` copies a request's cached contents under the lock, and
  ``resolve`` returns the contents of the misses; the request uses only these copies, so an
  eviction in between never leaves it without a judgment, and never needs another statement.

**Coverage verdicts (U2).** The same cache keeps, per meeting, the ``unsupported_findings``
verdict of the coverage counters (``coverage_service``), under a ``VerdictKey`` (organization,
workspace, verdict rules version).  Its inputs (the summary, the AI action items and the
evidence spans of the meeting) can change, so each entry stores the fingerprint it was judged
from (the summary id, ``updated_at`` and run, the count, latest change and largest id of the AI
action items and of the counted spans), as the 128-bit digest the caller passes
(``coverage_service._digest``), and a lookup with another fingerprint is a miss.  An entry
older than ``verdict_ttl_seconds`` is not used (it is judged again): the backstop for a change
that keeps the fingerprint (a raw SQL write).  With 0, no verdict is ever reused.  Verdicts
count against the same cap and the same LRU as the judgments.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.issue_trends.counting import JUDGMENT_VERSION, JudgedContent, SignatureJudge
from backend.issue_trends.definition import TrendDefinition

WAIT_SECONDS = 30.0
VERDICT_TTL_SECONDS = 600
JUDGMENTS = "trend_judgment"  # the kind of a judgment entry
VERDICTS = "coverage_verdict"  # the kind of a coverage verdict entry


@dataclass(frozen=True)
class CacheKey:
    """One map of the cache: a workspace (and its organization) under one definition and one
    version of the judging rules."""

    organization_id: str | None
    workspace_id: str
    definition_digest: str
    judgment_version: str = JUDGMENT_VERSION


def judgment_key(
    organization_id: str | None, workspace_id: str, definition: TrendDefinition
) -> CacheKey:
    return CacheKey(organization_id, workspace_id, definition.digest)


@dataclass(frozen=True)
class VerdictKey:
    """One map of coverage verdicts: a workspace (and its organization) under one version of
    the verdict rules (``coverage_service.VERDICT_VERSION``)."""

    organization_id: str | None
    workspace_id: str
    verdict_version: str


class JudgmentCache:
    """See the module docstring.  ``stats()`` counts since creation."""

    def __init__(
        self,
        max_entries: int,
        *,
        verdict_ttl_seconds: float = VERDICT_TTL_SECONDS,
        wait_seconds: float = WAIT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_entries = max(0, int(max_entries))
        self.verdict_ttl_seconds = verdict_ttl_seconds
        self.wait_seconds = wait_seconds
        self.clock = clock
        self._lock = threading.Lock()
        # (kind, key) -> run id -> (received_at, content), or meeting id -> (fingerprint,
        # verdict, built_at); most recently used last
        self._maps: OrderedDict[
            tuple[str, CacheKey | VerdictKey], OrderedDict[str, tuple[Any, ...]]
        ] = OrderedDict()
        self._entries = 0
        self._inflight: dict[tuple[str, CacheKey, str], threading.Event] = {}
        self._judges: dict[str, SignatureJudge] = {}
        self._stats = dict.fromkeys(
            (
                "hits",
                "misses",
                "builds",
                "evictions",
                "verdict_hits",
                "verdict_misses",
                "verdict_builds",
                "verdict_expired",
            ),
            0,
        )
        self._active = 0  # requests using the cache now (the warm-up yields to them)

    # --- the interface --------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"entries": self._entries, **self._stats}

    def clear(self) -> None:
        """Drop every entry (the counts since creation stay)."""
        with self._lock:
            self._maps.clear()
            self._entries = 0
            self._judges.clear()

    @contextmanager
    def request(self) -> Iterator[JudgmentCache]:
        """Mark a request as using the cache while the block runs (the warm-up waits)."""
        with self._lock:
            self._active += 1
        try:
            yield self
        finally:
            with self._lock:
                self._active -= 1

    def active_requests(self) -> int:
        with self._lock:
            return self._active

    def judge(self, definition: TrendDefinition) -> SignatureJudge:
        """The shared ``SignatureJudge`` of ``definition``: equal judgments of every request
        share their tuples, so the cached contents stay small."""
        with self._lock:
            judge = self._judges.get(definition.digest)
            if judge is None:
                judge = self._judges[definition.digest] = SignatureJudge(definition)
            return judge

    # --- judgments --------------------------------------------------------------------------

    def pin(self, key: CacheKey, start: datetime, end: datetime) -> dict[str, JudgedContent]:
        """A copy of the cached contents of ``key`` whose conversation was received in
        ``[start, end)``: the runs statement P need not send JSON for."""
        with self._lock:
            entries = self._maps.get((JUDGMENTS, key))
            if not entries:
                return {}
            self._maps.move_to_end((JUDGMENTS, key))
            return {
                run_id: content
                for run_id, (received_at, content) in entries.items()
                if start <= received_at < end
            }

    def resolve(
        self,
        key: CacheKey,
        *,
        hits: Iterable[str],
        misses: Mapping[str, tuple[datetime, object]],
        build: Callable[[object], JudgedContent],
        evict: bool = True,
    ) -> dict[str, JudgedContent]:
        """The contents of ``misses`` (run id -> (received_at, stored row)), each judged with
        ``build`` at most once over all threads, and kept.  ``hits``: the runs the request
        took from ``pin`` (counted).  ``evict=False`` (the warm-up): kept only while there is
        room, and nothing is evicted.

        Order: the built misses are added first, then the entries this request used (its hits)
        move to the recent end, and only then does the cap evict.  So a request that needs more
        than the free room evicts its own older misses (or other maps), never its hits: the
        entries the next request of the same window needs."""
        mapkey = (JUDGMENTS, key)
        found: dict[str, JudgedContent] = {}
        waits: dict[str, threading.Event] = {}
        claimed: list[str] = []
        used = list(hits)
        mine = threading.Event()
        with self._lock:
            entries = self._maps.get(mapkey)
            hit_count = len(used)
            for run_id in misses:
                if entries is not None and (entry := entries.get(run_id)) is not None:
                    found[run_id] = entry[1]  # kept by another request since the pin
                    used.append(run_id)
                    hit_count += 1
                    continue
                self._stats["misses"] += 1
                other = self._inflight.get((JUDGMENTS, key, run_id))
                if other is not None:
                    waits[run_id] = other
                else:
                    self._inflight[(JUDGMENTS, key, run_id)] = mine
                    claimed.append(run_id)
            self._stats["hits"] += hit_count
        built: dict[str, JudgedContent] = {}
        try:
            for run_id in claimed:
                built[run_id] = build(misses[run_id][1])
        finally:
            with self._lock:
                for run_id in claimed:
                    self._inflight.pop((JUDGMENTS, key, run_id), None)
                self._insert(
                    mapkey,
                    {run_id: (misses[run_id][0], content) for run_id, content in built.items()},
                    evict=evict,
                    recent=used,
                )
                self._stats["builds"] += len(built)
            mine.set()  # before waiting on anyone else
        found.update(built)
        for run_id, other in waits.items():
            other.wait(self.wait_seconds)
            with self._lock:
                entries = self._maps.get(mapkey)
                entry = entries.get(run_id) if entries is not None else None
            found[run_id] = entry[1] if entry is not None else build(misses[run_id][1])
        return found

    # --- coverage verdicts (U2) ---------------------------------------------------------------

    def verdicts(self, key: VerdictKey, fingerprints: Mapping[str, Hashable]) -> dict[str, int]:
        """The kept verdicts of the meetings of ``fingerprints`` (meeting id -> fingerprint)
        whose entry has the same fingerprint and is younger than ``verdict_ttl_seconds``."""
        found: dict[str, int] = {}
        mapkey = (VERDICTS, key)
        now = self.clock()
        with self._lock:
            entries = self._maps.get(mapkey)
            if entries is not None:
                self._maps.move_to_end(mapkey)
            for meeting_id, fingerprint in fingerprints.items():
                entry = entries.get(meeting_id) if entries is not None else None
                if entry is None or entry[0] != fingerprint:
                    self._stats["verdict_misses"] += 1
                    continue
                _fingerprint, verdict, built_at = entry
                if now - built_at >= self.verdict_ttl_seconds:
                    self._stats["verdict_expired"] += 1
                    continue
                found[meeting_id] = verdict
                entries.move_to_end(meeting_id)  # type: ignore[union-attr]
                self._stats["verdict_hits"] += 1
        return found

    def keep_verdicts(
        self,
        key: VerdictKey,
        verdicts: Mapping[str, tuple[Hashable | None, int]],
        *,
        evict: bool = True,
        recent: Iterable[str] = (),
    ) -> int:
        """Keep ``verdicts`` (meeting id -> (fingerprint, verdict)), judged now, and return how
        many entries were added.  All are counted as built; one with the fingerprint ``None``
        (judged from rows that changed after the lookup) is not kept, and with a time limit of 0
        none is kept, since none would ever be reused.  ``evict`` as in ``resolve``; ``recent``:
        the meetings this request took from ``verdicts``, moved to the recent end after the
        new entries and before any eviction (the order of ``resolve``)."""
        if not verdicts:
            return 0
        now = self.clock()
        kept = {
            meeting_id: (fingerprint, verdict, now)
            for meeting_id, (fingerprint, verdict) in verdicts.items()
            if fingerprint is not None
        }
        with self._lock:
            self._stats["verdict_builds"] += len(verdicts)
            if kept and self.verdict_ttl_seconds > 0:
                return self._insert((VERDICTS, key), kept, evict=evict, recent=recent)
            return 0

    # --- for the warm-up ----------------------------------------------------------------------

    def room(self) -> int:
        """How many entries can still be added without an eviction."""
        with self._lock:
            return max(0, self.max_entries - self._entries)

    def touch(self, key: CacheKey | VerdictKey, ids: Iterable[str] = ()) -> None:
        """Move ``ids`` (in that order) to the recent end of the map of ``key``, then the map
        to the recent end of the LRU.  The warm-up uses it to leave the order a request would
        leave: the newest conversations last in a map, the most active workspace last of all."""
        kind = JUDGMENTS if isinstance(key, CacheKey) else VERDICTS
        with self._lock:
            entries = self._maps.get((kind, key))
            if entries is None:
                return
            for item_id in ids:
                if item_id in entries:
                    entries.move_to_end(item_id)
            self._maps.move_to_end((kind, key))

    def held(self, key: CacheKey | VerdictKey, ids: Iterable[str]) -> int:
        """How many of ``ids`` have an entry under ``key`` now."""
        kind = JUDGMENTS if isinstance(key, CacheKey) else VERDICTS
        with self._lock:
            entries = self._maps.get((kind, key))
            return 0 if entries is None else sum(1 for item_id in ids if item_id in entries)

    # --- under the lock ---------------------------------------------------------------------

    def _insert(
        self,
        mapkey: tuple[str, CacheKey | VerdictKey],
        items: Mapping[str, tuple[Any, ...]],
        *,
        evict: bool = True,
        recent: Iterable[str] = (),
    ) -> int:
        """Add or replace ``items``; returns how many entries were added.  ``evict=False``:
        a new entry is added only while there is room, and nothing is evicted.  ``recent``:
        ids already in the map, moved to the recent end after the items and before any
        eviction, so the eviction takes the items before them."""
        entries = self._maps.get(mapkey)
        if entries is None:
            entries = self._maps[mapkey] = OrderedDict()
        self._maps.move_to_end(mapkey)
        added = 0
        for item_id, value in items.items():
            if item_id not in entries:
                if not evict and self._entries >= self.max_entries:
                    continue
                self._entries += 1
                added += 1
            entries[item_id] = value
            entries.move_to_end(item_id)
        for item_id in recent:
            if item_id in entries:
                entries.move_to_end(item_id)
        if not entries:
            del self._maps[mapkey]  # nothing fitted
        if evict:
            self._evict()
        return added

    def _evict(self) -> None:
        """Down to ``max_entries``: the oldest entries of the least recently used map first."""
        while self._entries > self.max_entries and self._maps:
            mapkey, entries = next(iter(self._maps.items()))
            if entries:
                entries.popitem(last=False)
                self._entries -= 1
                self._stats["evictions"] += 1
            if not entries:
                del self._maps[mapkey]
