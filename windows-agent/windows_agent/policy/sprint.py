"""Deterministic MVP policy for the enabled structured action vocabulary."""

from __future__ import annotations

import uuid
from typing import Any

from ..contracts import Action, PolicyDecision, PolicyOutcome, RiskLevel
from .base import Policy
from .mock import action_hash

_READ_PREFIXES = {
    "file.exists",
    "file.list",
    "file.read_text",
    "pdf.page_count",
    "pdf.get_metadata",
    "pdf.read_text",
    "pdf.search",
    "spreadsheet.list_sheets",
    "spreadsheet.dimensions",
    "spreadsheet.read_cell",
    "spreadsheet.read_range",
    "document.read_text",
    "document.get_metadata",
    "document.find",
    "presentation.slide_count",
    "presentation.get_metadata",
    "presentation.read_text",
    "presentation.find",
}
_PERMANENT_DENY = {"file.delete"}
_FORBIDDEN_TOKENS = ("shell", "powershell", "cmd", "command", "eval", "exec")


class SprintPolicy(Policy):
    """Allow reads and verified writes, confirm overwrite, deny unsafe actions."""

    def authorize(self, action: Action, context: Any = None) -> PolicyDecision:
        fingerprint = action_hash(action)
        action_type = action.type.casefold()
        if action_type in _PERMANENT_DENY or any(
            token in action_type for token in _FORBIDDEN_TOKENS
        ):
            return self._decision(
                action,
                PolicyOutcome.DENY,
                RiskLevel.FORBIDDEN,
                "SPRINT-DENY-UNSAFE",
                "Permanent delete, shell, and command execution are not supported.",
                fingerprint,
            )

        confirmation_required = bool(action.parameters.get("overwrite"))
        if action_type in {
            "document.replace_text",
            "presentation.replace_text",
        } and not action.parameters.get("save_as"):
            confirmation_required = True

        if confirmation_required:
            approvals = getattr(context, "approved_action_hashes", {}) if context else {}
            if approvals.get(action.action_id) != fingerprint:
                return self._decision(
                    action,
                    PolicyOutcome.CONFIRM,
                    RiskLevel.HIGH,
                    "SPRINT-CONFIRM-OVERWRITE",
                    "This action would overwrite existing local content.",
                    fingerprint,
                    confirmation_token=fingerprint,
                )

        if action_type in _READ_PREFIXES:
            return self._decision(
                action,
                PolicyOutcome.ALLOW,
                RiskLevel.NONE,
                "SPRINT-ALLOW-READ",
                "Bounded local read is allowed.",
                fingerprint,
            )

        return self._decision(
            action,
            PolicyOutcome.ALLOW,
            RiskLevel.MEDIUM,
            "SPRINT-ALLOW-VERIFIED-WRITE",
            "Local modification is allowed because independent verification is required.",
            fingerprint,
        )

    @staticmethod
    def _decision(
        action: Action,
        outcome: PolicyOutcome,
        risk: RiskLevel,
        rule_id: str,
        reason: str,
        fingerprint: str,
        *,
        confirmation_token: str | None = None,
    ) -> PolicyDecision:
        return PolicyDecision(
            decision_id=uuid.uuid4().hex,
            task_id=action.task_id,
            action_id=action.action_id,
            outcome=outcome,
            risk_level=risk,
            rule_id=rule_id,
            reason=reason,
            confirmation_token=confirmation_token,
            action_hash=fingerprint,
        )
