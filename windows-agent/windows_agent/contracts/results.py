"""Execution results — two distinct types, on purpose.

WHY TWO TYPES?
--------------
`ExecutorResult` and `ActionResult` describe the same event from two different
viewpoints, and keeping them separate is a deliberate separation of concerns:

    ExecutorResult  (INTERNAL)  — what an executor knows and returns.
    ActionResult    (EXTERNAL)  — what the planner/conversation layer receives.

Benefits of the split:
  * Executors stay ignorant of planner-facing concepts (the ActionStatus enum,
    verification). Their job is "do the thing, report facts", nothing more.
  * The Dispatcher is the ONE place that translates internal -> external, so it
    can centrally (a) bound the evidence size, (b) map success/failure to a rich
    status, and (c) attach a VerificationResult (in a later milestone). None of
    that logic gets duplicated across dozens of executors.

EVIDENCE MUST BE BOUNDED
------------------------
`evidence` is a small dict of relevant facts — never a whole PDF, workbook, or
DOM tree. Reasons: retrieved content is untrusted, large blobs bloat LLM context
and cost, and audit logs must stay small. The dispatcher enforces a size cap in
addition to executors keeping evidence minimal.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from .enums import ActionStatus
from .error import ActionError
from .verification import VerificationResult


class ExecutorResult(BaseModel):
    """INTERNAL result produced by `BaseExecutor.execute()`.

    Deliberately NOT a bare bool: a bool can't carry evidence, side effects, or a
    typed error, and "returned without raising" is not the same as "succeeded".
    """

    success: bool  # did the executor believe the operation completed?
    evidence: dict[str, Any] = Field(
        default_factory=dict, description="Bounded facts about what happened"
    )
    side_effects: list[dict[str, Any]] = Field(
        default_factory=list,
        description="State changes, each like {'type': 'file.created', 'target': '...'}",
    )
    error: Optional[ActionError] = None  # populated when success is False


class ActionResult(BaseModel):
    """EXTERNAL result returned to the planner/conversation layer.

    Built by the Dispatcher from an ExecutorResult (plus, later, a policy
    decision and verification). `status` is a rich enum rather than a bool so the
    planner can distinguish success from failure/denied/needs-confirmation/etc.
    """

    action_id: str
    task_id: str
    status: ActionStatus
    evidence: dict[str, Any] = Field(default_factory=dict)  # already size-bounded
    verification: Optional[VerificationResult] = None  # attached by verification stage (later)
    error: Optional[ActionError] = None
