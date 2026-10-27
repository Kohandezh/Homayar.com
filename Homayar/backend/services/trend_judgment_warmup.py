"""The trend judgment warm-up of one API process (HOM-312; ADR 0043).

Right after startup, a background thread fills the app's ``JudgmentCache`` with the judgments
the trend routes will need, so the first request of a workspace after a deploy, a restart or a
``JUDGMENT_VERSION`` bump (which always comes with a deploy) is not cold.

* **What.** For each workspace, over the conversations received in the last ``WARM_DAYS``
  days (35: it covers the default windows, the last 30 days of the workspace routes and the 4
  closed weeks of the organization routes): first the judgments, through the same statement P
  a request runs, the most recent conversations first; then the coverage verdicts (U2),
  through the fact statements ``compute_coverage`` runs.  Each time only what is not cached
  yet is read and judged.  The judgments go first because they cost the most to build again;
  a verdict also expires after ``trend_coverage_verdict_ttl_seconds``.  With a time limit of 0
  no verdict is warmed.
* **Order and cap.** Workspaces in the order of their latest processing job, most recent
  first.  The warm-up **never evicts**: it inserts with ``evict=False``, so it adds entries
  only while the cache has room (``JudgmentCache.room``), and it stops when the cache is full.
  So a less active workspace never pushes out what a more active one got.  Requests still
  evict as usual (LRU).
* **Which workspaces.** The list comes from ``processing_jobs`` under the read-only
  ``recovery`` service scope (the same discovery scope the lease sweeper and the metrics use,
  ``database/rls.py``): workspace ids and times only.  ``meeting_service.py`` creates a job
  for every upload, and no product code deletes a job row.  **A workspace whose conversations
  have no job row** (for example rows inserted outside the pipeline) **is not warmed**: its
  first request is cold, and its answer is correct.
* **Tenancy.** Every workspace is read in its own session with that workspace bound as the
  tenant, as a request would be.
* **Low priority.** It never blocks startup or ``/readyz`` (a daemon thread started after
  the app is configured).  The judgments go to the cache in batches of at most
  ``YIELD_EVERY`` runs; before each batch, and before the verdicts, it waits while any request
  is using the cache (polling every ``POLL_SECONDS``), then yields the interpreter.  It waits
  only between batches, when it holds no claim on a run, so a request that needs a run the
  warm-up is judging waits at most for one batch.  A request during the warm-up is answered
  correctly, cold where needed.
* **Stop.** ``stop`` sets an event the thread checks between workspaces and before every
  batch, then joins the thread (bounded).  A statement already running in the database
  finishes first.
* **Off.** ``TREND_JUDGMENT_WARMUP_ENABLED=false`` (``trend_judgment_warmup_enabled``): no
  thread; ``app.state.trend_judgment_warmup`` is ``None``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.services.trend_judgment_cache import JudgmentCache

WARM_DAYS = 35
POLL_SECONDS = 0.05
YIELD_EVERY = 256  # runs per batch; the warm-up waits for requests only between batches
STOP_SECONDS = 10.0


class _Stopped(Exception):
    pass


class JudgmentWarmup:
    """See the module docstring.  ``start`` once; ``wait``, ``stop`` and ``status`` from any
    thread."""

    def __init__(
        self,
        cache: JudgmentCache,
        session_factory: Callable[[], Session],
        *,
        days: int = WARM_DAYS,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.cache = cache
        self.session_factory = session_factory
        self.days = days
        self.now = now
        self._stop = threading.Event()
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._state = "running"
        self._workspaces_found = 0
        self._workspaces_warmed = 0
        self._entries_built = 0
        self._error: str | None = None
        self._thread = threading.Thread(target=self._run, name="trend-judgment-warmup", daemon=True)

    # --- the interface --------------------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def wait(self, timeout: float) -> bool:
        """True when the warm-up has finished (``done``, ``stopped`` or ``failed``)."""
        return self._done.wait(timeout)

    def stop(self, timeout: float = STOP_SECONDS) -> None:
        """Ask the thread to stop and wait for it, at most ``timeout`` seconds.  Idempotent."""
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "workspaces_found": self._workspaces_found,
                "workspaces_warmed": self._workspaces_warmed,
                "entries_built": self._entries_built,
                "error": self._error,
            }

    # --- the thread -------------------------------------------------------------------------

    def _run(self) -> None:
        from backend.services.logging_service import logger

        state = "done"
        warmed: list[tuple[Any, ...]] = []  # the maps of each warmed workspace, most active first
        try:
            for workspace_id in self._active_workspaces():
                if self._stop.is_set():
                    state = "stopped"
                    break
                if self.cache.room() == 0:
                    break  # full: the warm-up never evicts
                self._wait_for_requests()
                built = self._warm(workspace_id, warmed)
                with self._lock:
                    self._workspaces_warmed += 1
                    self._entries_built += built
        except _Stopped:
            state = "stopped"
        except Exception as exc:  # the warm-up never takes the app down
            state = "failed"
            with self._lock:
                self._error = type(exc).__name__
            logger.warning("TREND_JUDGMENT_WARMUP_FAILED error=%s", type(exc).__name__)
        finally:
            # Leave the order a request would leave: the least active workspace's maps first,
            # the most active last (the verdicts before the judgments of each), so a request
            # that must evict takes the least active and oldest first.
            for keys in reversed(warmed):
                for key in keys:
                    self.cache.touch(key)
            with self._lock:
                self._state = "stopped" if self._stop.is_set() and state == "done" else state
            self._done.set()

    def _active_workspaces(self) -> list[str]:
        """Workspace ids by their latest processing job, most recent first (the read-only
        ``recovery`` discovery scope)."""
        from backend.database.saas_models import ProcessingJob
        from backend.database.tenant_context import SERVICE_SCOPE_RECOVERY, bind_service_scope

        with self.session_factory() as db:
            bind_service_scope(db, SERVICE_SCOPE_RECOVERY)
            latest = func.max(ProcessingJob.created_at)
            ids = [
                workspace_id
                for workspace_id, _latest in db.execute(
                    select(ProcessingJob.workspace_id, latest)
                    .group_by(ProcessingJob.workspace_id)
                    .order_by(latest.desc(), ProcessingJob.workspace_id)
                )
            ]
        with self._lock:
            self._workspaces_found = len(ids)
        return ids

    def _wait_for_requests(self) -> None:
        while self.cache.active_requests() and not self._stop.is_set():
            self._stop.wait(POLL_SECONDS)
        if self._stop.is_set():
            raise _Stopped

    def _between_batches(self) -> None:
        """Before each batch: wait while a request uses the cache, then let it run."""
        self._wait_for_requests()
        time.sleep(0)

    def _warm(self, workspace_id: str, warmed: list[tuple[Any, ...]]) -> int:
        """Warm one workspace; returns the number of entries added.  Appends its map keys
        (verdicts, then judgments) to ``warmed``."""
        from backend.database.saas_models import Workspace
        from backend.database.tenant_context import bind_tenant_context
        from backend.services.coverage_service import VERDICT_VERSION, CoverageWindow, warm_verdicts
        from backend.services.issue_trend_service import trend_definition, warm_judgments
        from backend.services.trend_judgment_cache import VerdictKey, judgment_key

        end = self.now()
        start = end - timedelta(days=self.days)
        with self.session_factory() as db:
            bind_tenant_context(db, workspace_id=workspace_id)
            organization_id = db.scalar(
                select(Workspace.organization_id).where(Workspace.id == workspace_id)
            )
            warmed.append(
                (
                    VerdictKey(organization_id, workspace_id, VERDICT_VERSION),
                    judgment_key(organization_id, workspace_id, trend_definition()),
                )
            )
            added = warm_judgments(
                db,
                cache=self.cache,
                workspace_id=workspace_id,
                organization_id=organization_id,
                start=start,
                end=end,
                batch=YIELD_EVERY,
                between=self._between_batches,
            )
            if self.cache.verdict_ttl_seconds > 0 and self.cache.room() > 0:
                added += warm_verdicts(
                    db,
                    cache=self.cache,
                    workspace_id=workspace_id,
                    organization_id=organization_id,
                    window=CoverageWindow(start=start, end=end),
                    between=self._between_batches,
                )
        return added
