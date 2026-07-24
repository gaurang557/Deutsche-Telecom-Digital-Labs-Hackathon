"""Native audit-log access: AuditLogReader.fetch filtering + JSON return shape."""

import json
from datetime import datetime, timedelta, timezone

from windows_agent.audit import AuditLogReader, InMemoryAuditSink, redact
from windows_agent.contracts import AuditEvent, AuditEventType

ET = AuditEventType

_BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _emit(sink, **overrides):
    base = dict(
        task_id="t1",
        action_id="a1",
        sequence=0,
        event_type=ET.ACTION_PROPOSED,
        component="dispatcher",
        outcome=None,
        summary="",
        details={},
    )
    base.update(overrides)
    sink.emit(AuditEvent(**base))


def _populated_sink() -> InMemoryAuditSink:
    sink = InMemoryAuditSink()
    _emit(sink, task_id="t1", action_id="a1", event_type=ET.ACTION_PROPOSED, timestamp=_BASE)
    _emit(sink, task_id="t1", action_id="a1", event_type=ET.POLICY_ALLOWED, timestamp=_BASE + timedelta(seconds=1))
    _emit(sink, task_id="t1", action_id="a2", event_type=ET.ACTION_STARTED, timestamp=_BASE + timedelta(seconds=2))
    _emit(sink, task_id="t2", action_id="b1", event_type=ET.ACTION_FAILED, timestamp=_BASE + timedelta(seconds=3))
    return sink


def test_fetch_all_preserves_order_and_is_serializable():
    reader = AuditLogReader(_populated_sink())
    rows = reader.fetch()
    assert [r["event_type"] for r in rows] == [
        "action_proposed",
        "policy_allowed",
        "action_started",
        "action_failed",
    ]
    # JSON-serialisable end to end.
    json.dumps(rows)


def test_filter_by_task_id():
    reader = AuditLogReader(_populated_sink())
    rows = reader.fetch(task_id="t1")
    assert len(rows) == 3
    assert all(r["task_id"] == "t1" for r in rows)


def test_filter_by_action_id():
    reader = AuditLogReader(_populated_sink())
    rows = reader.fetch(action_id="a1")
    assert len(rows) == 2
    assert all(r["action_id"] == "a1" for r in rows)


def test_filter_by_event_types_enum_form():
    reader = AuditLogReader(_populated_sink())
    rows = reader.fetch(event_types=[ET.ACTION_PROPOSED, ET.ACTION_FAILED])
    assert {r["event_type"] for r in rows} == {"action_proposed", "action_failed"}


def test_filter_by_event_types_string_form():
    reader = AuditLogReader(_populated_sink())
    rows = reader.fetch(event_types=["policy_allowed"])
    assert [r["event_type"] for r in rows] == ["policy_allowed"]


def test_filter_by_event_types_mixed_enum_and_string():
    reader = AuditLogReader(_populated_sink())
    rows = reader.fetch(event_types=[ET.ACTION_STARTED, "action_failed"])
    assert {r["event_type"] for r in rows} == {"action_started", "action_failed"}


def test_filter_by_since_until_inclusive():
    reader = AuditLogReader(_populated_sink())
    rows = reader.fetch(since=_BASE + timedelta(seconds=1), until=_BASE + timedelta(seconds=2))
    assert [r["event_type"] for r in rows] == ["policy_allowed", "action_started"]


def test_limit_caps_and_keeps_chronological_order():
    reader = AuditLogReader(_populated_sink())
    rows = reader.fetch(limit=2)
    assert [r["event_type"] for r in rows] == ["action_proposed", "policy_allowed"]


def test_combined_filters():
    reader = AuditLogReader(_populated_sink())
    rows = reader.fetch(task_id="t1", action_id="a1", event_types=["policy_allowed"])
    assert len(rows) == 1
    assert rows[0]["event_type"] == "policy_allowed"


def test_timestamps_are_iso_strings():
    reader = AuditLogReader(_populated_sink())
    row = reader.fetch(limit=1)[0]
    assert isinstance(row["timestamp"], str)
    parsed = datetime.fromisoformat(row["timestamp"])
    assert parsed == _BASE


def test_details_pass_through_redact():
    sink = InMemoryAuditSink()
    _emit(sink, details={"rule_id": "R-9", "note": "keep"})
    row = AuditLogReader(sink).fetch()[0]
    assert row["details"] == {"rule_id": "R-9", "note": "keep"}


def test_fetch_json_returns_string():
    reader = AuditLogReader(_populated_sink())
    payload = reader.fetch_json(task_id="t2")
    assert isinstance(payload, str)
    decoded = json.loads(payload)
    assert len(decoded) == 1
    assert decoded[0]["task_id"] == "t2"


def test_redact_is_currently_identity_copy():
    src = {"rule_id": "R-1", "secret": "keep-for-now"}
    out = redact(src)
    assert out == src  # no-op passthrough until M11
    assert out is not src  # but a fresh copy (single seam for future masking)


def test_redact_handles_empty():
    assert redact({}) == {}
