"""Native audit-log access — the read/query side of the audit trail.

WHY THIS EXISTS
---------------
The LLM "accesses logs" by calling a tool whose RETURN VALUE is the log data.
This module is that return path: it reads OUR native `AuditEvent`s from a sink
and returns them as plain, JSON-serialisable dicts. We do NOT translate to any
external/shared vocabulary — the LLM performs whatever translation it needs.

Because the returned data is what we hand to the LLM, `redact()` is applied on
READ (not on write): it is the single seam where M11 sensitive-data masking will
plug in. Today it is a no-op passthrough.

The reader is synchronous and dependency-free (only stdlib + our contracts), so
it can back a simple tool call without touching the async pipeline.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable, Optional, Union

from ..contracts import AuditEvent, AuditEventType

EventTypeLike = Union[AuditEventType, str]


def redact(details: dict) -> dict:
    """Redaction seam — the SINGLE place M11 sensitive-data masking will plug in.

    TODO(M11): mask/scrub sensitive values here before the log data reaches the
    LLM. Until then this is a NO-OP passthrough that returns a shallow copy of
    ``details`` unchanged. It is applied on READ (see module docstring) because
    the returned dict is exactly what we surface to the model.
    """
    return dict(details or {})


class AuditLogReader:
    """Read/query helper over any sink exposing ``.events: list[AuditEvent]``.

    Wraps e.g. our `InMemoryAuditSink`. ``fetch`` returns native events as
    JSON-serialisable dicts (this return value is what a log-access tool call
    surfaces to the LLM). Emission order is always preserved.
    """

    def __init__(self, sink: Any) -> None:
        # Duck-typed on purpose: anything with an ``events`` list works.
        self._sink = sink

    def fetch(
        self,
        *,
        task_id: Optional[str] = None,
        action_id: Optional[str] = None,
        event_types: Optional[Iterable[EventTypeLike]] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Return matching events as JSON-serialisable dicts, in emission order.

        Filters (all optional, combined with AND):
          * ``task_id``     — exact match on ``event.task_id``.
          * ``action_id``   — exact match on ``event.action_id``.
          * ``event_types`` — keep events whose type is in this set/list. Accepts
            `AuditEventType` members and/or their string values, mixed freely.
          * ``since`` / ``until`` — inclusive datetime bounds on ``event.timestamp``.
          * ``limit``       — cap the number of results (kept chronological).

        Each event is serialised with ``model_dump(mode="json")`` (ISO-8601
        timestamps) and its ``details`` field is passed through :func:`redact`.
        """
        wanted_types = _normalize_event_types(event_types)

        out: list[dict[str, Any]] = []
        for event in self._sink.events:
            if task_id is not None and event.task_id != task_id:
                continue
            if action_id is not None and event.action_id != action_id:
                continue
            if wanted_types is not None and event.event_type.value not in wanted_types:
                continue
            if since is not None and event.timestamp < since:
                continue
            if until is not None and event.timestamp > until:
                continue

            record = event.model_dump(mode="json")
            record["details"] = redact(record.get("details") or {})
            out.append(record)

            if limit is not None and len(out) >= limit:
                break

        return out

    def fetch_json(self, **kwargs: Any) -> str:
        """Convenience: ``json.dumps`` of :meth:`fetch` (same keyword filters)."""
        return json.dumps(self.fetch(**kwargs))


def _normalize_event_types(
    event_types: Optional[Iterable[EventTypeLike]],
) -> Optional[set[str]]:
    """Reduce an enum/string mix to a set of string values, or None if unfiltered."""
    if event_types is None:
        return None
    normalized: set[str] = set()
    for et in event_types:
        normalized.add(et.value if isinstance(et, AuditEventType) else str(et))
    return normalized
