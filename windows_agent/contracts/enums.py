"""Frozen enums for the execution contract.

Values are chosen by this component. If another component uses different
labels, add a thin translation layer at that boundary rather than mutating
these — keeping them stable protects every downstream consumer.
"""

from __future__ import annotations

from enum import Enum


class ActionStatus(str, Enum):
    """Outcome of dispatching a single Action back to the planner.

    Only SUCCESS/FAILED are exercised in Milestone 0; the rest are reserved so
    later milestones (policy, confirmation, clarification, cancellation) don't
    have to churn this enum.
    """

    SUCCESS = "success"
    FAILED = "failed"
    DENIED = "denied"                      # policy engine (later)
    NEEDS_CONFIRMATION = "needs_confirmation"  # confirmation flow (later)
    CLARIFY = "clarify"                    # ambiguous request (later)
    CANCELLED = "cancelled"               # user interruption (later)


class VerificationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"                    # M0: nothing to verify yet


class ErrorCode(str, Enum):
    """Common, stable error codes. `ActionError.code` is a free string so
    executors may add domain-specific codes, but prefer these where they fit.
    """

    UNKNOWN_ACTION = "unknown_action"      # no handler registered for type
    VALIDATION_ERROR = "validation_error"  # malformed Action
    EXECUTOR_ERROR = "executor_error"      # handler raised / reported failure
    NOT_IMPLEMENTED = "not_implemented"
