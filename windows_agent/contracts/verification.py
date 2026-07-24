"""`VerificationResult` — the outcome of INDEPENDENTLY checking an action.

CORE PRINCIPLE
--------------
A function returning without an exception is NOT proof that the action
succeeded. Every modifying action must be verified by re-observing real state
(e.g. reopen the file and read the cell back) and comparing expected vs
observed. This model captures that check's outcome.

Milestone 0 does not verify anything yet (it emits status=SKIPPED); the contract
is defined now so the verification stage (a later milestone) can attach results
to an ActionResult without any contract change.

FIELDS
------
- status    PASSED / FAILED / SKIPPED.
- method    How the check was performed, e.g. "re-read cell A1".
- expected  What we expected to observe (often derived from Action.expected_result).
- observed  What was actually observed on re-inspection.
- message   Human-readable summary for audit.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .enums import VerificationStatus


class VerificationResult(BaseModel):
    status: VerificationStatus
    method: str = Field(..., description="How the check was performed, e.g. 're-read cell'")
    expected: Any = Field(default=None, description="What we expected to observe")
    observed: Any = Field(default=None, description="What was actually observed")
    message: str = Field(default="", description="Human-readable summary of the check")
