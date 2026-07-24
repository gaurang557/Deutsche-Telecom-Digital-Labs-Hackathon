"""`VerificationRegistry` — maps an action type to its Verifier.

Mirrors the ActionRegistry design: register per action type, look up on demand.
When no verifier is registered for an action type, `verify_action` returns a
SKIPPED result (e.g. read-only actions need no verification).
"""

from __future__ import annotations

from typing import Any

from ..contracts import Action, ExecutorResult, VerificationResult, VerificationStatus
from .base import Verifier


class VerificationRegistry:
    def __init__(self) -> None:
        self._verifiers: dict[str, Verifier] = {}

    def register_verifier(self, action_type: str, verifier: Verifier, *, override: bool = False) -> None:
        if not action_type:
            raise ValueError("action_type must be a non-empty string")
        if action_type in self._verifiers and not override:
            raise ValueError(f"Verifier already registered: {action_type!r}")
        self._verifiers[action_type] = verifier

    def get_verifier(self, action_type: str) -> Verifier | None:
        return self._verifiers.get(action_type)

    def has_verifier(self, action_type: str) -> bool:
        return action_type in self._verifiers

    async def verify_action(
        self, action: Action, result: ExecutorResult, context: Any = None
    ) -> VerificationResult:
        verifier = self._verifiers.get(action.type)
        if verifier is None:
            return VerificationResult(
                status=VerificationStatus.SKIPPED,
                method="none",
                message=f"No verifier registered for {action.type!r}",
            )
        return await verifier.verify(action, result, context)
