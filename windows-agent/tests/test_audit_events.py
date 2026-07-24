"""Milestone 1 — audit event emission: ordering + association."""

from windows_agent.contracts import (
    Action,
    AuditEventType,
    PolicyOutcome,
    VerificationResult,
    VerificationStatus,
)
from windows_agent.execution import ActionRegistry, Dispatcher
from windows_agent.executors.common.mock import EchoExecutor, FailingExecutor
from windows_agent.policy import AllowAllPolicy, ConfigurablePolicy
from windows_agent.verification import VerificationRegistry, Verifier
from windows_agent.audit import InMemoryAuditSink

ET = AuditEventType


def _action(type_: str = "mock.echo") -> Action:
    return Action(action_id="a1", task_id="t1", sequence=0, type=type_, reason="r")


class PassVerifier(Verifier):
    async def verify(self, action, result, context=None):
        return VerificationResult(status=VerificationStatus.PASSED, method="test")


class FailVerifier(Verifier):
    async def verify(self, action, result, context=None):
        return VerificationResult(status=VerificationStatus.FAILED, method="test")


def _dispatcher(policy, *, handler=None, verification=None):
    sink = InMemoryAuditSink()
    reg = ActionRegistry()
    reg.register_action(
        "mock.echo",
        handler or EchoExecutor(),
        requires_verification=False,
    )
    return Dispatcher(reg, policy, verification=verification, audit=sink), sink


async def test_allow_success_with_verifier_order():
    vr = VerificationRegistry()
    vr.register_verifier("mock.echo", PassVerifier())
    disp, sink = _dispatcher(AllowAllPolicy(), verification=vr)
    await disp.dispatch(_action())
    assert sink.event_types() == [
        ET.ACTION_PROPOSED,
        ET.POLICY_ALLOWED,
        ET.ACTION_STARTED,
        ET.ACTION_COMPLETED,
        ET.VERIFICATION_STARTED,
        ET.VERIFICATION_PASSED,
    ]


async def test_allow_success_no_verifier_order():
    disp, sink = _dispatcher(AllowAllPolicy())
    await disp.dispatch(_action())
    assert sink.event_types() == [
        ET.ACTION_PROPOSED,
        ET.POLICY_ALLOWED,
        ET.ACTION_STARTED,
        ET.ACTION_COMPLETED,
        ET.VERIFICATION_STARTED,
        ET.VERIFICATION_SKIPPED,
    ]


async def test_deny_order():
    disp, sink = _dispatcher(ConfigurablePolicy(outcome=PolicyOutcome.DENY))
    await disp.dispatch(_action())
    assert sink.event_types() == [ET.ACTION_PROPOSED, ET.POLICY_DENIED]


async def test_confirm_order():
    disp, sink = _dispatcher(ConfigurablePolicy(outcome=PolicyOutcome.CONFIRM))
    await disp.dispatch(_action())
    assert sink.event_types() == [ET.ACTION_PROPOSED, ET.POLICY_CONFIRMATION_REQUIRED]


async def test_executor_failure_order():
    disp, sink = _dispatcher(AllowAllPolicy(), handler=FailingExecutor())
    await disp.dispatch(_action())
    # No verification events: verification only runs after a successful execution.
    assert sink.event_types() == [
        ET.ACTION_PROPOSED,
        ET.POLICY_ALLOWED,
        ET.ACTION_STARTED,
        ET.ACTION_FAILED,
    ]


async def test_verification_failure_order():
    vr = VerificationRegistry()
    vr.register_verifier("mock.echo", FailVerifier())
    disp, sink = _dispatcher(AllowAllPolicy(), verification=vr)
    await disp.dispatch(_action())
    assert sink.event_types() == [
        ET.ACTION_PROPOSED,
        ET.POLICY_ALLOWED,
        ET.ACTION_STARTED,
        ET.ACTION_COMPLETED,
        ET.VERIFICATION_STARTED,
        ET.VERIFICATION_FAILED,
    ]


async def test_events_are_associated_with_task_and_action():
    disp, sink = _dispatcher(AllowAllPolicy())
    await disp.dispatch(_action())
    assert sink.events, "expected audit events"
    for event in sink.events:
        assert event.task_id == "t1"
        assert event.action_id == "a1"
        assert event.component == "dispatcher"
