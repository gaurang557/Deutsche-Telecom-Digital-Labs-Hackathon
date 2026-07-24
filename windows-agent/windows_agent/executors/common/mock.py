"""Mock executors — test doubles for exercising the execution contract (M0).

These perform NO real side effects. They exist so the tests can prove the
Dispatcher/Registry behave correctly for the three outcomes that matter:

  * EchoExecutor    — the happy path: success + bounded evidence.
  * FailingExecutor — a well-behaved failure: returns a structured error
                      (success=False) WITHOUT raising.
  * RaisingExecutor — a misbehaving executor: raises an exception, to prove the
                      dispatcher CONTAINS crashes and fails closed.

Real executors (files, PDF, spreadsheet, desktop, browser) arrive in later
milestones; the contract they must satisfy is exactly what these demonstrate.
"""

from __future__ import annotations

from ...contracts import Action, ActionError, ErrorCode, ExecutorResult
from ..base import BaseExecutor


class EchoExecutor(BaseExecutor):
    """Succeeds and echoes a bounded view of the action back as evidence."""

    name = "mock.echo"

    async def execute(self, action: Action) -> ExecutorResult:
        # Echo only small, relevant fields — never dump large/untrusted blobs.
        return ExecutorResult(
            success=True,
            evidence={
                "echoed_type": action.type,
                "echoed_target": action.target,
                "echoed_parameters": action.parameters,
            },
            side_effects=[],
        )


class FailingExecutor(BaseExecutor):
    """Reports a structured failure. Demonstrates the "return an error, don't
    raise" convention that real executors should follow for expected errors."""

    name = "mock.fail"

    def __init__(self, message: str = "mock failure", retryable: bool = False) -> None:
        self._message = message
        self._retryable = retryable

    async def execute(self, action: Action) -> ExecutorResult:
        return ExecutorResult(
            success=False,
            error=ActionError(
                code=ErrorCode.EXECUTOR_ERROR.value,
                message=self._message,
                retryable=self._retryable,
            ),
        )


class RaisingExecutor(BaseExecutor):
    """Raises on purpose — used to prove the Dispatcher contains exceptions and
    turns them into a safe FAILED ActionResult instead of crashing."""

    name = "mock.raise"

    async def execute(self, action: Action) -> ExecutorResult:
        raise RuntimeError("boom")
