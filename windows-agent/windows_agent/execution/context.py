"""`ExecutionContext` — minimal per-run control signals.

Milestone 1 needs only the ability to check whether a run has been cancelled or
paused; the dispatcher consults these at action boundaries. This is deliberately
NOT the global TaskState (owned elsewhere, integrated in Milestone 13) — it is a
small, injectable control object.

Backed by threading primitives so a control thread (e.g. voice) can flip the
signals while the async pipeline reads them.
"""

from __future__ import annotations

import threading


class ExecutionContext:
    def __init__(self, task_id: str = "task", *, cancelled: bool = False, paused: bool = False) -> None:
        self.task_id = task_id
        self._lock = threading.RLock()
        self._cancelled = cancelled
        self._paused = paused

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False
