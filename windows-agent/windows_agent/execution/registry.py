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

API NAMING
----------
The primary methods use the roadmap names (`register_action`,
`get_action_handler`, ...). The original Milestone 0 names (`register`, `get`,
`__contains__`, `types`) are kept as thin aliases so existing M0 code/tests keep
working (no regression).

SAFETY BEHAVIOUR
----------------
  * `get_action_handler()` returns None for unknown types → Dispatcher fails closed.
  * `register_action()` raises on a duplicate (unless override=True) to catch a
    handler being clobbered by accident during wiring.
"""

from __future__ import annotations

from ..executors.base import BaseExecutor


class ActionRegistry:
    def __init__(self) -> None:
        # action type string -> executor instance
        self._handlers: dict[str, BaseExecutor] = {}

    # ── primary API (roadmap names) ────────────────────────────────────────
    def register_action(self, action_type: str, handler: BaseExecutor, *, override: bool = False) -> None:
        """Register an executor for an action type.

        Raises ValueError on an empty type, or on a duplicate registration unless
        override=True (the explicit opt-in guards against accidental clobbering).
        """
        if not action_type:
            raise ValueError("action_type must be a non-empty string")
        if action_type in self._handlers and not override:
            raise ValueError(f"Action type already registered: {action_type!r}")
        self._handlers[action_type] = handler

    def unregister_action(self, action_type: str) -> bool:
        """Remove a handler. Returns True if one was present, False otherwise."""
        return self._handlers.pop(action_type, None) is not None

    def get_action_handler(self, action_type: str) -> BaseExecutor | None:
        # None (not an exception) for unknown types → caller decides how to fail.
        return self._handlers.get(action_type)

    def has_action(self, action_type: str) -> bool:
        return action_type in self._handlers

    def list_registered_actions(self) -> list[str]:
        # Sorted for stable, predictable output (e.g. in error details/tests).
        return sorted(self._handlers)

    # ── Milestone 0 aliases (kept for backward compatibility) ──────────────
    def register(self, action_type: str, handler: BaseExecutor, *, override: bool = False) -> None:
        self.register_action(action_type, handler, override=override)

    def get(self, action_type: str) -> BaseExecutor | None:
        return self.get_action_handler(action_type)

    def __contains__(self, action_type: object) -> bool:
        return action_type in self._handlers

    def types(self) -> list[str]:
        return self.list_registered_actions()
