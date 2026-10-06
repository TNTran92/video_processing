"""JobStore — in-memory job state + queue-full signal + idempotency.

THE ONLY component that must be swapped to add durability later
(spec §7). Behind this interface: status, result, error, TTL reap,
idempotency window, queue size.

Status machine:
  queued -> extracting -> analyzing -> summarizing -> completed
                                                     \-> failed
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    ANALYZING = "analyzing"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED}


class QueueFull(Exception):
    """main.py maps this to 429 queue_full + Retry-After."""


@dataclass
class JobRecord:
    job_id: str
    status: JobStatus = JobStatus.QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    # idempotency bookkeeping
    idempotency_key: str | None = None


class JobStore:
    """Thread-safe in-memory store. Bounded by TTL reaping."""

    def __init__(self, *, ttl_seconds: int, idempotency_window_seconds: int,
                 max_queue_size: int):
        self._ttl = ttl_seconds
        self._idem_window = idempotency_window_seconds
        self._max_queue = max_queue_size
        self._jobs: dict[str, JobRecord] = {}
        self._active = 0            # jobs currently in-flight (reserved slot)
        self._queue_depth = 0       # reserved but not yet started
        self._lock = threading.Lock()

    # -- submission ---------------------------------------------------------
    def reserve_slot(self) -> None:
        """Call before enqueuing a job. Raises QueueFull when the budget
        (active + queued > max_queue_size) would be exceeded. 429 signal."""
        # pseudocode: with self._lock:  check + increment _queue_depth
        ...

    def release_queued_slot(self, job_id: str) -> None:
        # called if submission is rolled back after reserve_slot()
        ...

    def create(
        self,
        *,
        idempotency_key: str | None,
    ) -> JobRecord:
        """Create (or, within the window, REUSE) a job record.

        Contract: if idempotency_key was already used within
        idempotency_window_seconds, return the existing record — no
        duplicate job. Otherwise create with new job_id = uuid4().
        """
        # pseudocode: with self._lock: scan recent records for same key
        #   -> return existing if fresh; else new JobRecord + store
        ...

    def start(self, job_id: str) -> None:
        """queued -> extracting (worker picked the job up). Records started_at."""
        # pseudocode: transition check + _active += 1, _queue_depth -= 1
        ...

    # -- state transitions ---------------------------------------------------
    def set_stage(self, job_id: str, status: JobStatus) -> None:
        """Worker progression: extracting->analyzing->summarizing.
        Raises JobStateError on illegal jumps (e.g. out of order)."""
        # pseudocode: validate allowed transition, update record
        ...

    def complete(self, job_id: str, result: dict[str, Any]) -> None:
        # set COMPLETED, finished_at, result; release_active()
        ...

    def fail(self, job_id: str, *, code: str, message: str) -> None:
        # set FAILED, finished_at, error fields; release_active()
        ...

    def release_active(self, job_id: str) -> None:
        # pseudocode: _active -= 1
        ...

    # -- reads ---------------------------------------------------------------
    def get(self, job_id: str) -> JobRecord | None:
        """Returns record or None (unknown OR TTL-expired — caller 404s).
        Trigger lazy reap before lookup."""
        # pseudocode: self._reap(); return self._jobs.get(job_id)
        ...

    def _reap(self) -> None:
        """Drop records where finished_at + ttl < now (terminal only)."""
        # pseudocode: with self._lock: filter + delete expired
        ...

    def to_public_dict(self, rec: JobRecord) -> dict[str, Any]:
        """Shape the record into the API response body (spec §4):
        {job_id, status, created_at, started_at?, finished_at?,
         result?|error?} — ISO-8601 timestamps, no internal fields."""
        # pseudocode
        ...


class JobStateError(Exception):
    """Illegal transition — should not happen; if it does, log hard."""
