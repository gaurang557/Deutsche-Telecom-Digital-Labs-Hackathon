"""`ActionRegistry` — maps a semantic action type to the executor that runs it.

WHY A REGISTRY (instead of one giant execute_action with dozens of if/elif)?
---------------------------------------------------------------------------
The system will support ~55 action types. A single function full of conditionals
would be unmaintainable and hard to test. A registry gives us:
  * Extensibility — adding a capability = register a handler, no core edits.
  * Testability   — each executor is registered/tested in isolation.
  * A single execution path — the Dispatcher looks handlers up here, so all the
    cross-cutting safety logic (policy/confirmation/verification/audit) lives in
    ONE place and can never be bypassed by an individual action.

SAFETY BEHAVIOUR
----------------
  * `get()` returns None for unknown types, so the Dispatcher can fail closed.
  * `register()` raises on a duplicate (unless override=True) to catch a handler
    being clobbered by accident during wiring.
"""

from __future__ import annotations

from ..executors.base import BaseExecutor


class ActionRegistry:
    def __init__(self) -> None:
        # action type string -> executor instance
        self._handlers: dict[str, BaseExecutor] = {}

    def register(self, action_type: str, handler: BaseExecutor, *, override: bool = False) -> None:
        """Register an executor for an action type.

        Raises ValueError on an empty type, or on a duplicate registration unless
        override=True (the explicit opt-in guards against accidental clobbering).
        """
        if not action_type:
            raise ValueError("action_type must be a non-empty string")
        if action_type in self._handlers and not override:
            raise ValueError(f"Action type already registered: {action_type!r}")
        self._handlers[action_type] = handler

    def get(self, action_type: str) -> BaseExecutor | None:
        # None (not an exception) for unknown types -> caller decides how to fail.
        return self._handlers.get(action_type)

    def __contains__(self, action_type: object) -> bool:
        return action_type in self._handlers

    def types(self) -> list[str]:
        # Sorted for stable, predictable output (e.g. in error details/tests).
        return sorted(self._handlers)
