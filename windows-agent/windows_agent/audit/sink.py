"""`AuditSink` — where audit events go.

Kept minimal on purpose: one `emit(event)` method. This lets Milestone 11 swap
in a redacting, SQLite-backed sink without touching the dispatcher. Emit is
synchronous (appending an event must not depend on the async pipeline).
"""

from __future__ import annotations

import abc

from ..contracts import AuditEvent, AuditEventType


class AuditSink(abc.ABC):
    @abc.abstractmethod
    def emit(self, event: AuditEvent) -> None: ...


class NullAuditSink(AuditSink):
    """Discards events. Default when no audit is configured."""

    def emit(self, event: AuditEvent) -> None:  # noqa: D401 - trivial
        return None


class InMemoryAuditSink(AuditSink):
    """Collects events in order — used by tests to assert ordering/association."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)

    def event_types(self) -> list[AuditEventType]:
        return [e.event_type for e in self.events]
