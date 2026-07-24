"""Frozen enums for the execution + safety contracts.

Values are chosen by this component. If another component uses different
labels, add a thin translation layer at that boundary rather than mutating
these — keeping them stable protects every downstream consumer.
"""

from __future__ import annotations

from enum import Enum


class ActionStatus(str, Enum):
    """Outcome of dispatching a single Action, reported back to the planner."""

    SUCCESS = "success"
    FAILED = "failed"
    DENIED = "denied"                          # policy engine refused
    NEEDS_CONFIRMATION = "needs_confirmation"  # policy requires user confirmation
    CLARIFY = "clarify"                        # ambiguous request; ask the user
    CANCELLED = "cancelled"                    # user interruption / cancelled context


class VerificationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"                        # no verifier registered for this action


class PolicyOutcome(str, Enum):
    """The four deterministic authorization outcomes."""

    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"        # allowed only after explicit user confirmation
    CLARIFY = "clarify"        # under-specified; need clarification before deciding


class RiskLevel(str, Enum):
    """Deterministic risk classes (computed by the policy engine, never the LLM)."""

    READ = "read"
    NAVIGATE = "navigate"
    MODIFY = "modify"
    CONSEQUENTIAL = "consequential"
    FORBIDDEN = "forbidden"


class ErrorCode(str, Enum):
    """Common, stable error codes. `ActionError.code` is a free string so
    executors may add domain-specific codes, but prefer these where they fit.
    """

    UNKNOWN_ACTION = "unknown_action"          # no handler registered for type
    VALIDATION_ERROR = "validation_error"      # malformed Action
    EXECUTOR_ERROR = "executor_error"          # handler raised / reported failure
    NOT_IMPLEMENTED = "not_implemented"
    # Pipeline / safety outcomes surfaced as errors on the ActionResult:
    POLICY_DENIED = "policy_denied"            # policy outcome DENY
    CONFIRMATION_REQUIRED = "confirmation_required"  # policy outcome CONFIRM
    CLARIFICATION_REQUIRED = "clarification_required"  # policy outcome CLARIFY
    VERIFICATION_FAILED = "verification_failed"  # executed but re-observation disagreed
    CANCELLED = "cancelled"                    # cancelled before start
