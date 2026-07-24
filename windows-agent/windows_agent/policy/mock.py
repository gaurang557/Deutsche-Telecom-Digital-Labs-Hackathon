"""Mock policies + the internal action-hash helper (Milestone 1).

These stand in for the real deterministic policy engine (Milestone 12) so the
pipeline can be exercised now:

  * AllowAllPolicy    — always ALLOW (happy-path wiring / demos).
  * ConfigurablePolicy — returns a fixed outcome, so tests can force
                         DENY / CONFIRM / CLARIFY paths deterministically.

`action_hash()` is an INTERNAL helper (never exposed to the planner/LLM). It
produces a stable fingerprint of the action's type/target/parameters so a
confirmation can be bound to the exact action (see PolicyDecision.action_hash).
"""

from __future__ import annotations

import uuid
from typing import Any

from ..contracts import Action, PolicyDecision, PolicyOutcome, RiskLevel
from .base import Policy

# `action_hash` now lives with the confirmation primitives (M12) as the single
# source of truth; re-exported here so existing imports keep working.
from .confirmation import action_hash  # noqa: F401  (re-exported for back-compat)


class AllowAllPolicy(Policy):
    """Always allows. For wiring/demos only — never for production safety."""

    def authorize(self, action: Action, context: Any = None) -> PolicyDecision:
        return PolicyDecision(
            decision_id=uuid.uuid4().hex,
            task_id=action.task_id,
            action_id=action.action_id,
            outcome=PolicyOutcome.ALLOW,
            risk_level=RiskLevel.NONE,
            rule_id="MOCK-ALLOW-ALL",
            reason="Mock policy: allow all.",
            action_hash=action_hash(action),
        )


class ConfigurablePolicy(Policy):
    """Returns a preset outcome so tests can drive each branch deterministically."""

    def __init__(
        self,
        outcome: PolicyOutcome = PolicyOutcome.ALLOW,
        risk_level: RiskLevel = RiskLevel.NONE,
        rule_id: str = "MOCK-CONFIGURABLE",
        reason: str = "Mock configurable policy.",
    ) -> None:
        self._outcome = outcome
        self._risk_level = risk_level
        self._rule_id = rule_id
        self._reason = reason

    def authorize(self, action: Action, context: Any = None) -> PolicyDecision:
        # A confirmation token is issued only when confirmation is required.
        token = uuid.uuid4().hex if self._outcome is PolicyOutcome.CONFIRM else None
        return PolicyDecision(
            decision_id=uuid.uuid4().hex,
            task_id=action.task_id,
            action_id=action.action_id,
            outcome=self._outcome,
            risk_level=self._risk_level,
            rule_id=self._rule_id,
            reason=self._reason,
            confirmation_token=token,
            action_hash=action_hash(action),
        )
