"""`ActionError` — the structured shape of a failure.

WHY A MODEL INSTEAD OF A STRING/BOOL?
-------------------------------------
Failures need to be machine-readable (so the planner can decide what to do next)
AND human-readable (for audit and debugging). A raw string or `False` loses:
  * a stable `code` the planner can branch on,
  * whether a safe retry might help (`retryable`),
  * bounded structured `details` for diagnostics.

`code` is a free string (executors may add domain-specific codes) but prefer the
shared `ErrorCode` values where they fit, so consumers can rely on them.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ActionError(BaseModel):
    code: str = Field(..., description="Stable machine-readable code (see ErrorCode)")
    message: str = Field(..., description="Human-readable explanation")
    # retryable is a HINT to the caller. Note: the safety model forbids
    # auto-retrying consequential actions regardless of this flag.
    retryable: bool = Field(default=False, description="Whether a safe retry may help")
    details: Optional[dict[str, Any]] = Field(default=None, description="Bounded extra context")
