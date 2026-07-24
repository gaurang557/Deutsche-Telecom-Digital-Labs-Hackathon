"""`ActionRegistry` — maps an action type to executor and safety metadata.

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

from dataclasses import dataclass

from ..executors.base import BaseExecutor


@dataclass(frozen=True)
class ActionRegistration:
    """Deterministic runtime metadata for one planner-visible action type."""

    action_type: str
    handler: BaseExecutor
    requires_verification: bool


class ActionRegistry:
    def __init__(self) -> None:
        # action type string -> immutable executor/safety metadata
        self._registrations: dict[str, ActionRegistration] = {}

    # ── primary API (roadmap names) ────────────────────────────────────────
    def register_action(
        self,
        action_type: str,
        handler: BaseExecutor,
        *,
        requires_verification: bool,
        override: bool = False,
    ) -> None:
        """Register an executor for an action type.

        Raises ValueError on an empty type, or on a duplicate registration unless
        override=True (the explicit opt-in guards against accidental clobbering).
        ``requires_verification`` is intentionally required so every future
        action is classified explicitly rather than silently defaulting optional.
        """
        if not action_type:
            raise ValueError("action_type must be a non-empty string")
        if not isinstance(requires_verification, bool):
            raise TypeError("requires_verification must be a bool")
        if action_type in self._registrations and not override:
            raise ValueError(f"Action type already registered: {action_type!r}")
        self._registrations[action_type] = ActionRegistration(
            action_type=action_type,
            handler=handler,
            requires_verification=requires_verification,
        )

    def unregister_action(self, action_type: str) -> bool:
        """Remove a handler. Returns True if one was present, False otherwise."""
        return self._registrations.pop(action_type, None) is not None

    def get_action_registration(self, action_type: str) -> ActionRegistration | None:
        """Return complete registration metadata, or ``None`` when unknown."""
        return self._registrations.get(action_type)

    def get_action_handler(self, action_type: str) -> BaseExecutor | None:
        # None (not an exception) for unknown types → caller decides how to fail.
        registration = self.get_action_registration(action_type)
        return registration.handler if registration is not None else None

    def requires_verification(self, action_type: str) -> bool:
        """Return the registered verification requirement.

        Unknown types raise rather than being silently classified as optional.
        """
        registration = self.get_action_registration(action_type)
        if registration is None:
            raise KeyError(f"Action type is not registered: {action_type!r}")
        return registration.requires_verification

    def has_action(self, action_type: str) -> bool:
        return action_type in self._registrations

    def list_registered_actions(self) -> list[str]:
        # Sorted for stable, predictable output (e.g. in error details/tests).
        return sorted(self._registrations)

    # ── Milestone 0 aliases (kept for backward compatibility) ──────────────
    def register(
        self,
        action_type: str,
        handler: BaseExecutor,
        *,
        requires_verification: bool,
        override: bool = False,
    ) -> None:
        self.register_action(
            action_type,
            handler,
            requires_verification=requires_verification,
            override=override,
        )

    def get(self, action_type: str) -> BaseExecutor | None:
        return self.get_action_handler(action_type)

    def __contains__(self, action_type: object) -> bool:
        return action_type in self._registrations

    def types(self) -> list[str]:
        return self.list_registered_actions()
