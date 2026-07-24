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
    """Deterministic risk classes.

    Set by deterministic code (our policy engine / pipeline), never proposed by
    the LLM — the LLM only *ingests* the resulting risk. Values mirror the shared
    team vocabulary (agent/models.py) PLUS `FORBIDDEN`, which we add for requests
    that must always be denied (there is no safe/confirmable version of them).
    """

    NONE = "none"                    # read-only, no side effects
    LOW = "low"                      # local, reversible (e.g. type into an unsaved draft)
    MEDIUM = "medium"                # creates state (e.g. a new file)
    HIGH = "high"                    # destructive but local (overwrite, delete, bulk rename)
    CONSEQUENTIAL = "consequential"  # leaves the machine (send, submit, publish, purchase)
    FORBIDDEN = "forbidden"          # never allowed (shell/registry/commands from untrusted content)


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
    VERIFIER_MISSING = "verifier_missing"        # required verifier absent before execution
    VERIFICATION_FAILED = "verification_failed"  # executed but re-observation disagreed
    CANCELLED = "cancelled"                    # cancelled before start
