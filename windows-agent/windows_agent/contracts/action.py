"""The `Action` contract — the single unit of work a planner proposes.

WHAT THIS IS
------------
An `Action` is a typed, JSON-serialisable description of ONE thing to do, e.g.
"copy this file to there". The planner (an LLM, later) emits a list of these;
the dispatcher executes them one at a time.

WHY IT LOOKS LIKE THIS
----------------------
The single most important safety rule of the whole system is:

    The LLM may PROPOSE actions, but only deterministic code may AUTHORIZE them.

We enforce that rule *structurally* on this model with `extra="forbid"`. Any
field Pydantic doesn't recognise raises a ValidationError instead of being
silently accepted. That specifically blocks a planner/LLM from smuggling in
authority-like fields such as `risk`, `permission`, `trust`, `confirmation`, or
`authorization`. Those decisions belong to the (deterministic) Policy Engine,
not to whatever produced the Action — so the Action model gives them nowhere to
live.

FIELD-BY-FIELD
--------------
- action_id       Unique id for this specific action instance (for audit/trace).
- task_id         The owning task, so results/audit stay correlated.
- sequence        0-based order within the task's plan (execution ordering).
- type            Semantic action type, e.g. "file.copy". Later restricted to an
                  allow-listed vocabulary (~55 actions). A string here so the
                  registry can map it to an executor.
- target          The PRIMARY target (a file path, window id, url, ...). Kept as
                  a single optional string for the common case; anything more
                  goes in `parameters`.
- parameters      Everything else the executor needs (e.g. {"dst": "..."}).
- expected_result Structured statement of the intended outcome. The verification
                  stage (later milestone) turns this into an assertion it can
                  independently re-observe — because "no exception thrown" is NOT
                  proof the action actually succeeded.
- reason          The planner's rationale. Useful for audit and for a human
                  reviewing why an action was proposed.

NOTE: there is intentionally NO risk/permission/trust/confirmation field here.
That absence is a feature, not an omission.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class Action(BaseModel):
    # extra="forbid": reject unknown fields (this is the structural guard that
    # stops authority fields from ever appearing on an Action). See module docstring.
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(..., description="Unique id for this action instance")
    task_id: str = Field(..., description="Owning task id")
    sequence: int = Field(..., description="Order within the task's plan (0-based)")
    type: str = Field(..., description="Semantic action type, e.g. 'file.copy' (allow-listed later)")
    target: Optional[str] = Field(
        default=None,
        description="Primary target (e.g. file path, window id). Everything else goes in parameters.",
    )
    parameters: dict[str, Any] = Field(default_factory=dict, description="Action arguments")
    expected_result: Optional[dict[str, Any]] = Field(
        default=None,
        description="Structured description of the intended outcome; drives verification later.",
    )
    reason: str = Field(..., description="Planner's rationale for proposing this action")
