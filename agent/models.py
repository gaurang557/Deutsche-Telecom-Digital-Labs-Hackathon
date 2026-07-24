"""Shared contract for every object that crosses a component boundary.

This is `agent/models.py` as specified by ADR-001 and
docs/models-reference.md. Per that ADR it is meant to be written once,
pushed to `main`, and treated as read-only by all four workstreams --
nobody redefines these types locally.

As of this commit, nobody had pushed it to `main` yet, so this file was
generated directly from docs/models-reference.md (the agreed spec) so
the verification/audit slice isn't blocked. IMPORTANT: this still needs
to be reconciled with whatever the other three devs land on `main` --
treat this file as a proposal to merge in, not as the final word, until
the team confirms it.

All models are plain pydantic.BaseModel with JSON-serialisable fields,
because they cross a SQLite boundary (state store + audit log).
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel


class RiskLevel(str, Enum):
    NONE = "none"  # read-only, no side effects
    LOW = "low"  # local, reversible (type into unsaved draft)
    MEDIUM = "medium"  # creates state (new file)
    HIGH = "high"  # destructive but local (overwrite, delete, bulk rename)
    CONSEQUENTIAL = "consequential"  # leaves the machine (send, submit, publish, purchase)
    FORBIDDEN = "forbidden"   # never executable, regardless of confirmation


class PolicyOutcome(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    CLARIFY = "clarify"
    DENY = "deny"


class TaskStatus(str, Enum):
    PLANNING = "planning"
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ActionStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"  # started, outcome unknown -- do NOT collapse this to a bool
    SKIPPED = "skipped"
    DENIED             = "denied"              # policy blocked it; never executed
    NEEDS_CONFIRMATION = "needs_confirmation"  # held pending user confirmation
    CLARIFY            = "clarify"             # held pending user clarification


class TaskRequest(BaseModel):
    """Produced by Dev 1 (voice), consumed by Dev 2 (planner)."""

    request_id: str  # the correlation key for the ENTIRE task; generate once, never regenerate
    text: str  # transcript
    source: Literal["speech", "text", "test"]
    confidence: float | None = None  # ASR confidence; low -> clarify before planning
    received_at: datetime


class Locator(BaseModel):
    # strategy is ordered by preference; "coordinates" is the fallback of
    # last resort -- when confidence is low the executor should fail
    # safely rather than click blind.
    strategy: Literal["accessibility", "role_name", "selector", "coordinates"]
    value: str
    app: str
    confidence: float | None = None


class Action(BaseModel):
    """Produced by Dev 2, consumed by Dev 3 (execute) and Dev 4 (authorize + verify)."""

    id: str  # REQUIRED -- joins attempt -> verification -> recovery in the audit log
    type: str  # must be in ACTION_VOCABULARY below
    target: str  # app or file the action operates on
    parameters: dict[str, Any]  # type-specific; see ACTION_VOCABULARY
    # The LLM proposes risk; the policy engine may escalate it but must
    # never accept a downgrade -- a downgrade is the injection attack surface.
    risk: RiskLevel
    expected_result: str  # human-readable, for the confirmation prompt
    step_index: int


# Allow-list of action types. A plan-schema validator (owned by Dev 2)
# rejects any Action whose `type` isn't in this set. Deliberately no
# "run_command" / "eval" -- the absence of shell execution here is a
# safety property, not an oversight.
ACTION_VOCABULARY: frozenset[str] = frozenset(
    {
        "read_document",
        "inspect_ui",
        "focus_application",
        "click_element",
        "type_text",
        "update_spreadsheet",
        "save_file",
        "copy_file",
        "move_file",
        "delete_file",
        "submit_form",
        "send_message",
    }
)


class PolicyDecision(BaseModel):
    """Produced by Dev 4 (policy engine), consumed by Dev 2's loop."""

    action_id: str
    outcome: PolicyOutcome
    rule_id: str  # stable across runs, e.g. "R-210"; appears verbatim in the audit log
    reason: str  # human-readable, spoken aloud on deny/clarify
    # Single-use, bound to one action_id. Reusing a token, or applying it
    # to a different action, must fail validate_confirmation.
    confirmation_token: str | None = None
    decided_at: datetime


class ActionResult(BaseModel):
    """Produced by Dev 3, consumed by Dev 4 (verify) and Dev 2 (state)."""

    action_id: str
    # status == SUCCESS is a claim, not a fact: it means "the executor
    # raised no exception," not "the keystrokes landed where intended."
    # Verification exists because of this.
    status: ActionStatus
    evidence: dict[str, Any]  # window title, bytes written, element found -- UNREDACTED here
    error: str | None = None
    duration_ms: int
    completed_at: datetime


class VerificationResult(BaseModel):
    """Produced by Dev 4, consumed by Dev 2's loop.

    `passed` is three-valued on purpose:
        True  -> checked, correct        -> loop continues
        False -> checked, wrong          -> loop recovers or stops
        None  -> no verifier existed     -> treat as NOT verified, do not mark complete

    `if result.passed:` is correct. `if result.passed is not False:` is a
    bug -- it silently promotes an unverified action to "success".
    """

    action_id: str
    passed: bool | None
    expected: str  # e.g. "B7 == '42500'"
    actual: str  # e.g. "B7=None, B8='42500'" -- name where the value landed when findable
    evidence: dict[str, Any]
    reason: str
    checked_at: datetime


class Plan(BaseModel):
    request_id: str
    actions: list[Action]
    created_at: datetime
    model_id: str  # which local model produced it


class HistoryEntry(BaseModel):
    action: Action
    decision: PolicyDecision
    result: ActionResult | None
    verification: VerificationResult | None


class TaskState(BaseModel):
    """Owned by Dev 2, persisted by Dev 4's store, read on resume."""

    request_id: str
    status: TaskStatus
    current_step: int
    plan: Plan
    # Append-only, and must survive corrections -- this is what stops the
    # agent redoing completed work after the user changes their mind mid-task.
    history: list[HistoryEntry]
    pending_confirmation: str | None = None  # token awaiting a yes/no
    updated_at: datetime

    def attempts_for(self, action_id: str) -> int:
        """How many times this action has been attempted so far.

        recover_or_stop reads this to enforce the retry cap.
        """
        return sum(1 for h in self.history if h.action.id == action_id)


class AuditEvent(BaseModel):
    """Produced by everyone, written by Dev 4.

    Field is named `details_redacted`, not `details`, so that passing
    raw (unredacted) data reads as obviously wrong at the call site.
    """

    timestamp: datetime
    request_id: str
    event_type: str  # see EVENT_TYPES below
    details_redacted: dict[str, Any]  # ALREADY through redact_sensitive_data()
    rule_id: str | None = None
    action_id: str | None = None


# A denied action MUST produce an event -- a block that leaves no trace
# is indistinguishable from a request that never happened, and the
# safety demo depends on showing it.
EVENT_TYPES: frozenset[str] = frozenset(
    {
        "transcript_received",
        "plan_created",
        "plan_rejected",
        "policy_decision",
        "confirmation_requested",
        "confirmation_granted",
        "confirmation_denied",
        "action_attempted",
        "verification_result",
        "injection_detected",
        "recovery_decision",
        "task_paused",
        "task_resumed",
        "task_cancelled",
        "task_completed",
        "task_failed",
    }
)


class RecoveryDecision(BaseModel):
    # Never "retry" when the action's risk is CONSEQUENTIAL -- a failed
    # send might have half-sent; retrying could double-send.
    action_id: str
    outcome: Literal["retry", "stop", "ask_user"]
    reason: str
    attempts_so_far: int


class Assertion(BaseModel):
    kind: str  # "cell_equals" | "file_exists_nonempty" | ... | "none"
    params: dict[str, Any]


class Detection(BaseModel):
    # `source` records where injected text came from. That text is
    # logged as data and never authorises anything.
    is_instruction: bool
    matched_pattern: str | None
    source: str
    excerpt_redacted: str
