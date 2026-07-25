import hashlib
from datetime import UTC, datetime

import pytest

from agent import store
from agent.models import (
    Action,
    ActionResult,
    ActionStatus,
    AuditEvent,
    HistoryEntry,
    Plan,
    PolicyDecision,
    PolicyOutcome,
    RiskLevel,
    TaskState,
    TaskStatus,
)

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def fresh_db():
    # A brand new in-memory database per test -- nothing leaks between tests.
    store.connect(":memory:")
    yield


def _make_state(
    request_id: str = "req1",
    current_step: int = 1,
    pending_confirmation: str | None = None,
) -> TaskState:
    action = Action(
        id=f"{request_id}-s1",
        type="update_spreadsheet",
        target="budget.xlsx",
        parameters={"path": "budget.xlsx", "cell": "B7", "value": "42500"},
        risk=RiskLevel.MEDIUM,
        expected_result="B7 becomes 42500",
        step_index=1,
    )
    decision = PolicyDecision(
        action_id=action.id,
        outcome=PolicyOutcome.ALLOW,
        rule_id="R-001",
        reason="low risk",
        decided_at=NOW,
    )
    result = ActionResult(
        action_id=action.id,
        status=ActionStatus.SUCCESS,
        evidence={"window_title": "LibreOffice Calc"},
        duration_ms=120,
        completed_at=NOW,
    )
    plan = Plan(request_id=request_id, actions=[action], created_at=NOW, model_id="local-llm-1")
    history_entry = HistoryEntry(action=action, decision=decision, result=result, verification=None)
    # Whoever sets pending_confirmation on a live state is expected to set
    # pending_confirmation_hash alongside it -- mirror that here rather
    # than relying on save_state to derive it, since save_state is
    # allowed to just trust an already-set hash.
    pending_hash = (
        hashlib.sha256(pending_confirmation.encode("utf-8")).hexdigest()
        if pending_confirmation
        else None
    )
    return TaskState(
        request_id=request_id,
        status=TaskStatus.RUNNING,
        current_step=current_step,
        plan=plan,
        history=[history_entry],
        pending_confirmation=pending_confirmation,
        pending_confirmation_hash=pending_hash,
        updated_at=NOW,
    )


def _make_event(
    request_id: str = "req1",
    event_type: str = "action_attempted",
    details: dict | None = None,
) -> AuditEvent:
    return AuditEvent(
        timestamp=NOW,
        request_id=request_id,
        event_type=event_type,
        details_redacted=details or {},
    )


def test_save_and_load_state_roundtrip():
    state = _make_state()
    store.save_state(state)
    loaded = store.load_state("req1")
    assert loaded.request_id == state.request_id
    assert loaded.status == state.status
    assert loaded.current_step == state.current_step
    assert loaded.plan == state.plan
    assert loaded.history == state.history
    assert loaded.updated_at == state.updated_at


def test_load_state_returns_none_when_missing():
    assert store.load_state("does-not-exist") is None


def test_save_state_upserts_rather_than_duplicating():
    store.save_state(_make_state(current_step=1))
    store.save_state(_make_state(current_step=2))  # same request_id, different step

    loaded = store.load_state("req1")
    assert loaded.current_step == 2

    conn = store._require_connection()
    count = conn.execute("SELECT COUNT(*) FROM tasks WHERE request_id = 'req1'").fetchone()[0]
    assert count == 1


def test_pending_confirmation_plaintext_never_reaches_disk():
    store.save_state(_make_state(pending_confirmation="super-secret-token"))

    conn = store._require_connection()
    row = conn.execute(
        "SELECT pending_confirmation_hash FROM tasks WHERE request_id = 'req1'"
    ).fetchone()
    assert row[0] is not None
    assert row[0] != "super-secret-token"  # only the hash is stored

    # And the plaintext isn't anywhere else in the row either.
    full_row = conn.execute("SELECT * FROM tasks WHERE request_id = 'req1'").fetchone()
    assert "super-secret-token" not in full_row

    # load_state can't and doesn't restore the plaintext -- that's the point.
    loaded = store.load_state("req1")
    assert loaded.pending_confirmation is None


def test_matches_pending_correct_token():
    state = _make_state(pending_confirmation="tok-abc123")
    assert store.matches_pending(state, "tok-abc123") is True


def test_matches_pending_wrong_token():
    state = _make_state(pending_confirmation="tok-abc123")
    assert store.matches_pending(state, "tok-wrong") is False


def test_matches_pending_none_pending_returns_false():
    state = _make_state(pending_confirmation=None)
    assert store.matches_pending(state, "any-token") is False


def test_matches_pending_still_works_after_a_reload():
    # This is the whole point of comparing against pending_confirmation_hash
    # instead of pending_confirmation: after a save/load round trip, the
    # plaintext token is gone, but validation must still work correctly.
    store.save_state(_make_state(pending_confirmation="tok-abc123"))

    reloaded = store.load_state("req1")
    assert reloaded.pending_confirmation is None  # plaintext really is gone
    assert reloaded.pending_confirmation_hash is not None  # hash survived

    assert store.matches_pending(reloaded, "tok-abc123") is True
    assert store.matches_pending(reloaded, "tok-wrong") is False


def test_get_audit_trail_returns_only_matching_request_in_order():
    store.append_audit_event(_make_event("req1", "plan_created"))
    store.append_audit_event(_make_event("req1", "action_attempted"))
    store.append_audit_event(_make_event("req2", "plan_created"))  # different request

    trail = store.get_audit_trail("req1")
    assert [e.event_type for e in trail] == ["plan_created", "action_attempted"]
    assert all(e.request_id == "req1" for e in trail)


def test_append_audit_event_redacts_as_a_second_line_of_defence():
    # Simulate a caller that forgot to pre-redact -- the raw secret goes
    # straight into details_redacted. The store must still not persist it.
    event = _make_event(details={"password": "hunter2", "note": "fine"})
    store.append_audit_event(event)

    trail = store.get_audit_trail("req1")
    assert trail[0].details_redacted["password"].startswith("<SECRET:")
    assert trail[0].details_redacted["note"] == "fine"

    # Also check the raw value never reached disk at all, not just that
    # get_audit_trail happens to redact on the way out.
    conn = store._require_connection()
    raw_row = conn.execute("SELECT details_json FROM audit_events").fetchone()[0]
    assert "hunter2" not in raw_row


def test_log_redacts_an_email_before_writing():
    store.log("req1", "transcript_received", {"text": "email me at bob@example.com"})

    trail = store.get_audit_trail("req1")
    assert "<EMAIL>" in trail[0].details_redacted["text"]
    assert "bob@example.com" not in trail[0].details_redacted["text"]

    conn = store._require_connection()
    raw_row = conn.execute("SELECT details_json FROM audit_events").fetchone()[0]
    assert "bob@example.com" not in raw_row
