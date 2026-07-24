"""Shared, JSON-serialisable contracts every component agrees on.

These are the stable integration surface. Changing them is a cross-component
decision. Milestone 0 defines the execution-path contracts only:
Action, ActionError, VerificationResult, ExecutorResult, ActionResult.
(TaskRequest, PolicyDecision, Confirmation, AuditEvent arrive in later
milestones.)
"""

from .enums import (
    ActionStatus,
    VerificationStatus,
    PolicyOutcome,
    RiskLevel,
    ErrorCode,
)
from .action import Action
from .error import ActionError
from .verification import VerificationResult
from .results import ExecutorResult, ActionResult
from .policy import PolicyDecision
from .audit import AuditEvent, AuditEventType

__all__ = [
    "ActionStatus",
    "VerificationStatus",
    "PolicyOutcome",
    "RiskLevel",
    "ErrorCode",
    "Action",
    "ActionError",
    "VerificationResult",
    "ExecutorResult",
    "ActionResult",
    "PolicyDecision",
    "AuditEvent",
    "AuditEventType",
]
