"""`Dispatcher` — the single, safe execution path for an Action.

WHY A SINGLE PATH?
------------------
Every action flows through `dispatch()`. That makes this the one place to wire
the deterministic cross-cutting stages, so none of them can be skipped by an
individual executor. The FULL intended order (built across milestones) is:

    validate → [policy authorize] → [confirmation] → execute → [verification] → [audit]

Milestone 0 implements only the middle:

    registry lookup → execute → bound evidence → ActionResult

The bracketed stages are RESERVED extension points, called out in `dispatch()`
so future milestones slot in without re-plumbing anything.

FAIL-CLOSED GUARANTEES (both already in place)
----------------------------------------------
  * Unknown action type            -> ActionResult(FAILED, code=UNKNOWN_ACTION)
  * Executor raises an exception   -> ActionResult(FAILED, code=EXECUTOR_ERROR)
A crash never escapes the dispatcher; the planner always gets a structured result.

EVIDENCE BOUNDING
-----------------
`_bound()` caps string length and collection sizes before the evidence leaves
the dispatcher. Rationale: retrieved content is untrusted, large blobs bloat LLM
context/cost, and audit logs must stay small. We never hand back whole
PDFs/workbooks/DOM trees.
"""

from __future__ import annotations

from typing import Any

from ..contracts import (
    Action,
    ActionError,
    ActionResult,
    ActionStatus,
    ErrorCode,
    ExecutorResult,
)
from .registry import ActionRegistry

# Evidence caps. Kept conservative so nothing large ever reaches the planner/logs.
_MAX_STR = 2000   # max characters for any single string
_MAX_ITEMS = 50   # max entries kept from any dict/list


def _bound(value: Any, _depth: int = 0) -> Any:
    """Recursively shrink evidence to safe sizes.

    Truncates long strings, caps dict/list lengths, and stops at a max depth so a
    deeply nested or huge structure can't blow up context or logs.
    """
    if _depth > 6:  # guard against pathological nesting
        return "…"
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[:_MAX_STR] + "…[truncated]"
    if isinstance(value, dict):
        return {k: _bound(v, _depth + 1) for k, v in list(value.items())[:_MAX_ITEMS]}
    if isinstance(value, (list, tuple)):
        return [_bound(v, _depth + 1) for v in list(value)[:_MAX_ITEMS]]
    return value  # numbers, bools, None pass through unchanged


class Dispatcher:
    def __init__(self, registry: ActionRegistry) -> None:
        self._registry = registry

    async def dispatch(self, action: Action) -> ActionResult:
        # NOTE: the Action is already schema-validated (Pydantic) by the time it
        # reaches here. Milestone 1 will insert policy authorization + (when the
        # decision is "confirm") a confirmation check BEFORE the lookup/execute
        # below. This comment marks that reserved insertion point.

        handler = self._registry.get(action.type)
        if handler is None:
            # Unknown action type: fail closed with a helpful, bounded error.
            return ActionResult(
                action_id=action.action_id,
                task_id=action.task_id,
                status=ActionStatus.FAILED,
                error=ActionError(
                    code=ErrorCode.UNKNOWN_ACTION.value,
                    message=f"No executor registered for action type {action.type!r}",
                    retryable=False,
                    details={"known_types": self._registry.types()},
                ),
            )

        try:
            result: ExecutorResult = await handler.execute(action)
        except Exception as exc:
            # An executor bug/crash must never propagate out of the dispatcher.
            return ActionResult(
                action_id=action.action_id,
                task_id=action.task_id,
                status=ActionStatus.FAILED,
                error=ActionError(
                    code=ErrorCode.EXECUTOR_ERROR.value,
                    message=f"Executor raised: {type(exc).__name__}: {exc}",
                    retryable=False,
                ),
            )

        # Reserved: the VERIFICATION stage (later milestone) will run here and set
        # the `verification` field below by independently re-observing state.

        return ActionResult(
            action_id=action.action_id,
            task_id=action.task_id,
            status=ActionStatus.SUCCESS if result.success else ActionStatus.FAILED,
            evidence=_bound(result.evidence),  # enforce evidence bounds centrally
            verification=None,  # populated by the verification stage (later)
            error=result.error,
        )

        # Reserved: AUDIT events (TASK/ACTION/POLICY/VERIFICATION lifecycle) will
        # be emitted around this method in a later milestone, through one central
        # redaction layer — not via ad-hoc logging inside executors.
