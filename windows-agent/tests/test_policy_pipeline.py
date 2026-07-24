"""Milestone 12 — end-to-end policy gating through the Dispatcher.

Wires the REAL pipeline the agent uses — ActionRegistry (+ FileExecutor) →
Dispatcher (+ DeterministicPolicy, VerificationRegistry (+ file verifiers),
InMemoryAuditSink) — and proves the safety behaviour on the live filesystem:

  * a read is ALLOWED and executes;
  * a delete / overwrite is BLOCKED (needs_confirmation) and does NOT execute;
  * re-dispatching with the valid single-use token executes it;
  * a reused or mismatched token never executes;
  * a denied action never executes and is audited.
"""

from windows_agent.audit import InMemoryAuditSink
from windows_agent.contracts import (
    Action,
    ActionStatus,
    AuditEventType,
    ErrorCode,
    ExecutorResult,
)
from windows_agent.execution import ActionRegistry, Dispatcher
from windows_agent.executors import register_file_executor
from windows_agent.executors.base import BaseExecutor
from windows_agent.policy import DeterministicPolicy
from windows_agent.verification import VerificationRegistry, register_file_verifiers

ET = AuditEventType


class SpyExecutor(BaseExecutor):
    """Records whether it ran — proves the executor is/ isn't reached."""

    name = "spy"

    def __init__(self) -> None:
        self.called = False

    async def execute(self, action: Action) -> ExecutorResult:
        self.called = True
        return ExecutorResult(success=True, evidence={"ran": True})


def _build(*, extra_types: dict[str, BaseExecutor] | None = None):
    registry = ActionRegistry()
    register_file_executor(registry)
    for type_, handler in (extra_types or {}).items():
        registry.register_action(type_, handler, requires_verification=False)
    verification = VerificationRegistry()
    register_file_verifiers(verification)
    sink = InMemoryAuditSink()
    dispatcher = Dispatcher(registry, DeterministicPolicy(), verification=verification, audit=sink)
    return dispatcher, sink


def _action(type_: str, target, task_id: str = "t1", **params) -> Action:
    return Action(
        action_id="a1", task_id=task_id, sequence=0, type=type_,
        target=str(target), parameters=params, reason="test",
    )


async def test_read_allows_and_executes(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("hello", encoding="utf-8")
    dispatcher, sink = _build()

    result = await dispatcher.dispatch(_action("file.read_text", target))

    assert result.status is ActionStatus.SUCCESS
    assert result.evidence["content"] == "hello"
    assert ET.POLICY_ALLOWED in sink.event_types()


async def test_delete_needs_confirmation_then_executes(tmp_path):
    target = tmp_path / "report.tmp"
    target.write_text("payload", encoding="utf-8")
    dispatcher, sink = _build()
    delete = _action("file.delete", target)

    # 1) No token → blocked; the executor never runs.
    blocked = await dispatcher.dispatch(delete)
    assert blocked.status is ActionStatus.NEEDS_CONFIRMATION
    assert blocked.error.code == ErrorCode.CONFIRMATION_REQUIRED.value
    assert blocked.evidence["rule_id"] == "R-DELETE-CONFIRM"
    assert blocked.evidence["risk_level"] == "high"
    token = blocked.evidence["confirmation_token"]
    assert token
    assert target.exists()  # nothing was deleted
    assert ET.POLICY_CONFIRMATION_REQUIRED in sink.event_types()

    # 2) Valid single-use token → executes + verifies.
    confirmed = await dispatcher.dispatch(delete, confirmation_token=token)
    assert confirmed.status is ActionStatus.SUCCESS
    assert not target.exists()
    assert ET.CONFIRMATION_ACCEPTED in sink.event_types()
    assert ET.ACTION_COMPLETED in sink.event_types()


async def test_overwrite_needs_confirmation_then_executes(tmp_path):
    target = tmp_path / "data.txt"
    target.write_text("v1", encoding="utf-8")
    dispatcher, _ = _build()
    overwrite = _action("file.write_text", target, content="v2", overwrite=True)

    blocked = await dispatcher.dispatch(overwrite)
    assert blocked.status is ActionStatus.NEEDS_CONFIRMATION
    assert blocked.evidence["rule_id"] == "R-OVERWRITE-CONFIRM"
    assert target.read_text(encoding="utf-8") == "v1"  # unchanged — executor did not run

    confirmed = await dispatcher.dispatch(
        overwrite, confirmation_token=blocked.evidence["confirmation_token"]
    )
    assert confirmed.status is ActionStatus.SUCCESS
    assert target.read_text(encoding="utf-8") == "v2"


async def test_reused_token_does_not_execute(tmp_path):
    target = tmp_path / "once.tmp"
    target.write_text("x", encoding="utf-8")
    dispatcher, _ = _build()
    delete = _action("file.delete", target)

    token = (await dispatcher.dispatch(delete)).evidence["confirmation_token"]
    ok = await dispatcher.dispatch(delete, confirmation_token=token)
    assert ok.status is ActionStatus.SUCCESS
    assert not target.exists()

    # Recreate the file and replay the now-consumed token.
    target.write_text("x", encoding="utf-8")
    replay = await dispatcher.dispatch(delete, confirmation_token=token)
    assert replay.status is ActionStatus.NEEDS_CONFIRMATION
    assert target.exists()  # replay did NOT delete


async def test_mismatched_token_does_not_execute(tmp_path):
    file_a = tmp_path / "a.tmp"
    file_a.write_text("x", encoding="utf-8")
    file_b = tmp_path / "b.tmp"
    file_b.write_text("x", encoding="utf-8")
    dispatcher, sink = _build()

    token_a = (await dispatcher.dispatch(_action("file.delete", file_a))).evidence["confirmation_token"]

    # Present A's token while asking to delete B (the confused-deputy attack).
    result = await dispatcher.dispatch(_action("file.delete", file_b), confirmation_token=token_a)
    assert result.status is ActionStatus.NEEDS_CONFIRMATION
    assert file_b.exists()  # B was NOT deleted
    assert ET.CONFIRMATION_REJECTED in sink.event_types()


async def test_denied_action_not_executed_and_audited(tmp_path):
    spy = SpyExecutor()
    dispatcher, sink = _build(extra_types={"shell.exec": spy})

    result = await dispatcher.dispatch(_action("shell.exec", "rm -rf /"))

    assert result.status is ActionStatus.DENIED
    assert result.error.code == ErrorCode.POLICY_DENIED.value
    assert spy.called is False
    assert ET.POLICY_DENIED in sink.event_types()


async def test_unknown_action_clarify_not_executed(tmp_path):
    spy = SpyExecutor()
    dispatcher, sink = _build(extra_types={"made.up.action": spy})

    result = await dispatcher.dispatch(_action("made.up.action", "x"))

    assert result.status is ActionStatus.CLARIFY
    assert result.error.code == ErrorCode.CLARIFICATION_REQUIRED.value
    assert spy.called is False
    assert ET.POLICY_CLARIFICATION_REQUIRED in sink.event_types()
