"""`PolicyDecision` — the deterministic authorization verdict for one Action.

WHY THIS EXISTS
---------------
Authorization is NOT done by the LLM or by executors. A deterministic policy
engine (mocked in M1, real in M12) inspects an Action and returns exactly one
PolicyDecision. The dispatcher then obeys it. Because the decision is pure and
rule-based, the same Action always yields the same verdict, and every verdict
carries a stable `rule_id` + human-readable `reason` for auditability.

CONFIRMATION BINDING
--------------------
`action_hash` binds a confirmation to the EXACT action (its type/target/
parameters). If any of those change, a previously granted confirmation no
longer matches and must be rejected — so a user can never approve one thing and
have another executed. `confirmation_token` is issued only for CONFIRM outcomes.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .enums import PolicyOutcome, RiskLevel


class PolicyDecision(BaseModel):
    decision_id: str = Field(..., description="Unique id for this decision (audit/trace)")
    task_id: str
    action_id: str
    outcome: PolicyOutcome
    risk_level: RiskLevel
    rule_id: str = Field(..., description="Stable id of the rule that decided this")
    reason: str = Field(..., description="Human-readable explanation of the decision")
    confirmation_token: Optional[str] = Field(
        default=None, description="Issued only for CONFIRM outcomes"
    )
    action_hash: str = Field(..., description="Binds a confirmation to this exact action")
