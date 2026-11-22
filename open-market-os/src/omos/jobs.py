"""Background jobs with progress (downloads, model pulls, long analyses), so the UI never blocks.

Progress is published on the event bus as topic ``job`` and can be polled via the API.
Jobs live in memory: a host restart forgets finished jobs (their effects are persisted elsewhere).
"""

from __future__ import annotations

import logging
import threading
import traceback
from collections.abc import Callable
from typing import Any

from omos.errors import NotFound, OmosError
from omos.util import new_id, now_ms

log = logging.getLogger(__name__)


class Job:
    def __init__(self, kind: str, title: str, publish: Callable[[str, dict], None] | None):
        self.id = new_id("job_")
        self.kind, self.title = kind, title
        self.status = "running"
        self.progress = 0.0
        self.message = "starting"
        self.result: Any = None
        self.error: str | None = None
        self.started_at = now_ms()
        self.finished_at: int | None = None
        self.cancel_requested = False
        self._publish = publish

    def update(self, progress: float | None = None, message: str | None = None) -> None:
        if progress is not None:
            self.progress = max(0.0, min(100.0, float(progress)))
        if message is not None:
            self.message = message[:300]
        self._emit()

    def check_cancel(self) -> None:
        if self.cancel_requested:
            raise OmosError("cancelled by the user", code="cancelled")

    def _emit(self) -> None:
        if self._publish:
            try:
                self._publish("job", self.to_dict())
            except Exception:  # noqa: BLE001 - progress events are best effort
                log.debug("job progress event dropped", exc_info=True)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "title": self.title, "status": self.status,
                "progress": round(self.progress, 1), "message": self.message, "result": self.result,
                "error": self.error, "started_at": self.started_at, "finished_at": self.finished_at}


class JobRegistry:
    def __init__(self, publish: Callable[[str, dict], None] | None = None, keep: int = 100):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._publish = publish
        self._keep = keep

    def start(self, kind: str, title: str, fn: Callable[[Job], Any], *, unique: bool = True) -> Job:
        with self._lock:
            if unique:
                for j in self._jobs.values():
                    if j.kind == kind and j.status == "running":
                        return j  # one job of this kind at a time (e.g. one model download)
            job = Job(kind, title, self._publish)
            self._jobs[job.id] = job
            if len(self._jobs) > self._keep:
                for old in sorted(self._jobs.values(), key=lambda j: j.started_at)[: len(self._jobs) - self._keep]:
                    if old.status != "running":
                        self._jobs.pop(old.id, None)

        def run() -> None:
            try:
                job.result = fn(job)
                job.status, job.progress, job.message = "succeeded", 100.0, "done"
            except OmosError as exc:
                job.status = "cancelled" if exc.code == "cancelled" else "failed"
                job.error = exc.message
            except Exception as exc:  # noqa: BLE001 - a job must never take the host down
                log.error("job %s failed: %s", job.id, traceback.format_exc())
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"[:500]
            job.finished_at = now_ms()
            job._emit()

        threading.Thread(target=run, name=f"job-{kind}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job:
        j = self._jobs.get(job_id)
        if not j:
            raise NotFound(f"job {job_id} not found")
        return j

    def list(self) -> list[dict[str, Any]]:
        return [j.to_dict() for j in sorted(self._jobs.values(), key=lambda j: -j.started_at)]

    def cancel(self, job_id: str) -> dict[str, Any]:
        j = self.get(job_id)
        j.cancel_requested = True
        return j.to_dict()
