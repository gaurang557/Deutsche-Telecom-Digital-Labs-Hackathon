"""Milestone 1 — full pipeline behaviour (policy → executor → verification)."""

from windows_agent.contracts import (
    Action,
    ActionStatus,
    ErrorCode,
    ExecutorResult,
    PolicyOutcome,
    VerificationResult,
    VerificationStatus,
)
from windows_agent.execution import ActionRegistry, Dispatcher, ExecutionContext
from windows_agent.executors.base import BaseExecutor
from windows_agent.executors.common.mock import EchoExecutor, FailingExecutor
from windows_agent.policy import AllowAllPolicy, ConfigurablePolicy
from windows_agent.verification import VerificationRegistry, Verifier


def _action(type_: str = "mock.echo") -> Action:
    return Action(action_id="a1", task_id="t1", sequence=0, type=type_, parameters={"n": 1}, reason="r")


class SpyExecutor(BaseExecutor):
    """Records whether it was called — proves the executor is/ isn't reached."""

    def __init__(self) -> None:
        self.called = False

    async def execute(self, action):
        self.called = True
        return ExecutorResult(success=True, evidence={"ok": True})


class PassVerifier(Verifier):
    async def verify(self, action, result, context=None):
        return VerificationResult(status=VerificationStatus.PASSED, method="test")


class FailVerifier(Verifier):
    async def verify(self, action, result, context=None):
        return VerificationResult(status=VerificationStatus.FAILED, method="test", message="mismatch")


class SkipVerifier(Verifier):
    async def verify(self, action, result, context=None):
        return VerificationResult(status=VerificationStatus.SKIPPED, method="test", message="unexpected skip")


class RaiseVerifier(Verifier):
    async def verify(self, action, result, context=None):
        raise RuntimeError("verification boom")


def _dispatcher(policy, *, handler=None, verification=None, requires_verification=False):
    reg = ActionRegistry()
    reg.register_action(
        "mock.echo",
        handler or EchoExecutor(),
        requires_verification=requires_verification,
    )
    return Dispatcher(reg, policy, verification=verification)


async def test_invalid_action_dict_rejected():
    disp = _dispatcher(AllowAllPolicy())
    # Missing required `sequence` and `reason`.
    result = await disp.dispatch({"action_id": "a", "task_id": "t", "type": "mock.echo"})
    assert result.status == ActionStatus.FAILED
    assert result.error.code == ErrorCode.VALIDATION_ERROR.value


async def test_policy_allow_calls_executor():
    spy = SpyExecutor()
    disp = _dispatcher(AllowAllPolicy(), handler=spy)
    result = await disp.dispatch(_action())
    assert result.status == ActionStatus.SUCCESS
    assert spy.called is True
    assert result.verification.status == VerificationStatus.SKIPPED


async def test_read_missing_verifier_succeeds_with_skipped():
    spy = SpyExecutor()
    reg = ActionRegistry()
    reg.register_action(
        "mock.read",
        spy,
        requires_verification=False,
    )
    disp = Dispatcher(reg, AllowAllPolicy())
    result = await disp.dispatch(_action("mock.read"))
    assert result.status == ActionStatus.SUCCESS
    assert result.verification.status == VerificationStatus.SKIPPED
    assert spy.called is True


async def test_policy_deny_blocks_executor():
    spy = SpyExecutor()
    disp = _dispatcher(ConfigurablePolicy(outcome=PolicyOutcome.DENY), handler=spy)
    result = await disp.dispatch(_action())
    assert result.status == ActionStatus.DENIED
    assert spy.called is False
    assert result.error.code == ErrorCode.POLICY_DENIED.value


async def test_policy_confirm_blocks_executor():
    spy = SpyExecutor()
    disp = _dispatcher(ConfigurablePolicy(outcome=PolicyOutcome.CONFIRM), handler=spy)
    result = await disp.dispatch(_action())
    assert result.status == ActionStatus.NEEDS_CONFIRMATION
    assert spy.called is False


async def test_policy_clarify_blocks_executor():
    spy = SpyExecutor()
    disp = _dispatcher(ConfigurablePolicy(outcome=PolicyOutcome.CLARIFY), handler=spy)
    result = await disp.dispatch(_action())
    assert result.status == ActionStatus.CLARIFY
    assert spy.called is False


async def test_executor_failure_reported():
    disp = _dispatcher(AllowAllPolicy(), handler=FailingExecutor(message="nope"))
    result = await disp.dispatch(_action())
    assert result.status == ActionStatus.FAILED
    assert result.error.code == ErrorCode.EXECUTOR_ERROR.value


async def test_verifier_pass_is_success():
    vr = VerificationRegistry()
    vr.register_verifier("mock.echo", PassVerifier())
    disp = _dispatcher(AllowAllPolicy(), verification=vr, requires_verification=True)
    result = await disp.dispatch(_action())
    assert result.status == ActionStatus.SUCCESS
    assert result.verification.status == VerificationStatus.PASSED


async def test_verifier_fail_not_reported_successful():
    vr = VerificationRegistry()
    vr.register_verifier("mock.echo", FailVerifier())
    disp = _dispatcher(AllowAllPolicy(), verification=vr, requires_verification=True)
    result = await disp.dispatch(_action())
    assert result.status == ActionStatus.FAILED
    assert result.verification.status == VerificationStatus.FAILED
    assert result.error.code == ErrorCode.VERIFICATION_FAILED.value


async def test_required_missing_verifier_fails_before_executor():
    spy = SpyExecutor()
    disp = _dispatcher(
        AllowAllPolicy(),
        handler=spy,
        requires_verification=True,
    )
    result = await disp.dispatch(_action())
    assert result.status == ActionStatus.FAILED
    assert result.error.code == ErrorCode.VERIFIER_MISSING.value
    assert result.verification is None
    assert spy.called is False


async def test_required_verifier_skipped_is_failed():
    vr = VerificationRegistry()
    vr.register_verifier("mock.echo", SkipVerifier())
    disp = _dispatcher(AllowAllPolicy(), verification=vr, requires_verification=True)
    result = await disp.dispatch(_action())
    assert result.status == ActionStatus.FAILED
    assert result.verification.status == VerificationStatus.FAILED
    assert "returned SKIPPED" in result.verification.message
    assert result.error.code == ErrorCode.VERIFICATION_FAILED.value


async def test_verifier_exception_is_contained():
    vr = VerificationRegistry()
    vr.register_verifier("mock.echo", RaiseVerifier())
    disp = _dispatcher(AllowAllPolicy(), verification=vr, requires_verification=True)
    result = await disp.dispatch(_action())
    assert result.status == ActionStatus.FAILED
    assert result.verification.status == VerificationStatus.FAILED
    assert "RuntimeError" in result.verification.method
    assert "RuntimeError" in result.verification.message
    assert result.error.code == ErrorCode.VERIFICATION_FAILED.value


async def test_cancelled_context_does_not_start():
    spy = SpyExecutor()
    disp = _dispatcher(AllowAllPolicy(), handler=spy)
    ctx = ExecutionContext(cancelled=True)
    result = await disp.dispatch(_action(), ctx)
    assert result.status == ActionStatus.CANCELLED
    assert spy.called is False


async def test_unknown_action_rejected():
    disp = _dispatcher(AllowAllPolicy())
    result = await disp.dispatch(_action("does.not.exist"))
    assert result.status == ActionStatus.FAILED
    assert result.error.code == ErrorCode.UNKNOWN_ACTION.value
