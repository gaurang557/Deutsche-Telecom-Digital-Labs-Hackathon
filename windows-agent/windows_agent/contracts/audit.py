"""`AuditEvent` — one structured record in the task's audit trail.

WHY CENTRAL
-----------
Audit events are emitted by the dispatcher around the execution lifecycle, NOT
by ad-hoc logging inside executors. That keeps the trail complete, consistently
shaped, and routed through a single (later: redacting) sink.

Milestone 1 defines the full event vocabulary (used + reserved) so later
milestones don't churn this enum. Persistence (SQLite) and redaction land in
Milestone 11; here events go to an in-memory or null sink.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class AuditEventType(str, Enum):
    # ── emitted by the M1 pipeline ─────────────────────────────────────────
    ACTION_PROPOSED = "action_proposed"
    POLICY_ALLOWED = "policy_allowed"
    POLICY_DENIED = "policy_denied"
    POLICY_CONFIRMATION_REQUIRED = "policy_confirmation_required"
    POLICY_CLARIFICATION_REQUIRED = "policy_clarification_required"
    ACTION_STARTED = "action_started"
    ACTION_COMPLETED = "action_completed"
    ACTION_FAILED = "action_failed"
    ACTION_CANCELLED = "action_cancelled"
    VERIFICATION_STARTED = "verification_started"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_FAILED = "verification_failed"
    VERIFICATION_SKIPPED = "verification_skipped"
    # ── reserved for later milestones ──────────────────────────────────────
    TASK_STARTED = "task_started"
    TRANSCRIPT_RECEIVED = "transcript_received"
    PLAN_CREATED = "plan_created"
    PLAN_REVISED = "plan_revised"
    CONFIRMATION_REQUESTED = "confirmation_requested"
    CONFIRMATION_ACCEPTED = "confirmation_accepted"
    CONFIRMATION_REJECTED = "confirmation_rejected"
    CONFIRMATION_EXPIRED = "confirmation_expired"
    TASK_PAUSED = "task_paused"
    TASK_RESUMED = "task_resumed"
    TASK_CORRECTED = "task_corrected"
    TASK_CANCELLED = "task_cancelled"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    UNTRUSTED_CONTENT_DETECTED = "untrusted_content_detected"


class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    task_id: str
    action_id: Optional[str] = None
    sequence: Optional[int] = None
    event_type: AuditEventType
    component: str = Field(..., description="Emitter, e.g. 'dispatcher'")
    outcome: Optional[str] = Field(default=None, description="Short outcome tag, e.g. 'success'")
    summary: str = Field(default="", description="Human-readable one-liner")
    details: dict[str, Any] = Field(default_factory=dict, description="Bounded, redacted context")
