"""Milestone 0 — dispatcher / registry tests (async)."""

import pytest

from windows_agent.contracts import Action, ActionStatus, ErrorCode
from windows_agent.execution import ActionRegistry, Dispatcher
from windows_agent.executors.common.mock import (
    EchoExecutor,
    FailingExecutor,
    RaisingExecutor,
)


def _action(type_: str) -> Action:
    return Action(
        action_id="a1", task_id="t1", sequence=0, type=type_,
        target="x", parameters={"n": 1}, reason="test",
    )


def _dispatcher(**handlers) -> Dispatcher:
    reg = ActionRegistry()
    for type_, handler in handlers.items():
        reg.register(type_, handler)
    return Dispatcher(reg)


async def test_known_action_succeeds_and_echoes():
    disp = _dispatcher(**{"mock.echo": EchoExecutor()})
    result = await disp.dispatch(_action("mock.echo"))
    assert result.status == ActionStatus.SUCCESS
    assert result.error is None
    assert result.evidence["echoed_type"] == "mock.echo"
    assert result.evidence["echoed_parameters"] == {"n": 1}


async def test_unknown_action_fails_safely():
    disp = _dispatcher(**{"mock.echo": EchoExecutor()})
    result = await disp.dispatch(_action("does.not.exist"))
    assert result.status == ActionStatus.FAILED
    assert result.error is not None
    assert result.error.code == ErrorCode.UNKNOWN_ACTION.value
    assert result.error.retryable is False


async def test_executor_reported_failure_propagates():
    disp = _dispatcher(**{"mock.fail": FailingExecutor(message="disk full", retryable=True)})
    result = await disp.dispatch(_action("mock.fail"))
    assert result.status == ActionStatus.FAILED
    assert result.error.code == ErrorCode.EXECUTOR_ERROR.value
    assert result.error.message == "disk full"
    assert result.error.retryable is True


async def test_executor_exception_is_contained():
    disp = _dispatcher(**{"mock.raise": RaisingExecutor()})
    result = await disp.dispatch(_action("mock.raise"))
    assert result.status == ActionStatus.FAILED
    assert result.error.code == ErrorCode.EXECUTOR_ERROR.value
    assert "boom" in result.error.message


def test_registry_rejects_duplicate_and_empty():
    reg = ActionRegistry()
    reg.register("mock.echo", EchoExecutor())
    with pytest.raises(ValueError):
        reg.register("mock.echo", EchoExecutor())
    reg.register("mock.echo", EchoExecutor(), override=True)  # allowed
    with pytest.raises(ValueError):
        reg.register("", EchoExecutor())
    assert "mock.echo" in reg
    assert reg.types() == ["mock.echo"]


async def test_evidence_is_bounded():
    disp = _dispatcher(**{"mock.echo": EchoExecutor()})
    big = "z" * 5000
    action = Action(action_id="a", task_id="t", sequence=0, type="mock.echo",
                    parameters={"blob": big}, reason="r")
    result = await disp.dispatch(action)
    assert len(result.evidence["echoed_parameters"]["blob"]) < 5000
