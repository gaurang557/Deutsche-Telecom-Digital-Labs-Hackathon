from datetime import UTC, datetime

from agent import audit_view, store
from agent.models import AuditEvent

NOW = datetime.now(UTC)


def test_long_detail_value_is_truncated_with_ellipsis(tmp_path, capsys):
    db_path = str(tmp_path / "audit.db")
    store.connect(db_path)
    store.append_audit_event(
        AuditEvent(
            timestamp=NOW,
            request_id="req1",
            event_type="verification_result",
            details_redacted={"reason": "x" * 200},
        )
    )

    audit_view.main(["req1", "--db-path", db_path])

    out = capsys.readouterr().out
    assert "…" in out
    assert "x" * 200 not in out


def test_redacted_value_survives_through_the_cli(tmp_path, capsys):
    db_path = str(tmp_path / "audit.db")
    store.connect(db_path)
    # Simulate a caller that forgot to pre-redact -- append_audit_event's
    # second line of defence should still catch it before it's ever
    # printed here.
    store.append_audit_event(
        AuditEvent(
            timestamp=NOW,
            request_id="req1",
            event_type="action_attempted",
            details_redacted={"password": "hunter2"},
        )
    )

    audit_view.main(["req1", "--db-path", db_path])

    out = capsys.readouterr().out
    assert "hunter2" not in out
    assert "<SECRET:" in out
