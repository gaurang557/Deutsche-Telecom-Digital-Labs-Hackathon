from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class HealthResponse(BaseModel):
    status: Literal["ok"]


class RequestSource(StrEnum):
    SPEECH = "speech"
    TEXT = "text"
    TEST = "test"


class ControlIntent(StrEnum):
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    CORRECT = "correct"


class ActionType(StrEnum):
    OPEN_APPLICATION = "open_application"
    OPEN_FILE = "open_file"
    OPEN_URL = "open_url"
    FOCUS_APPLICATION = "focus_application"
    CLOSE_APPLICATION = "close_application"
    CLOSE_ALL_APPLICATIONS = "close_all_applications"
    CLICK_ELEMENT = "click_element"
    TYPE_TEXT = "type_text"
    PRESS_KEY = "press_key"
    READ_FILE = "read_file"
    COPY_FILE_CONTENT = "copy_file_content"
    CREATE_FILE = "create_file"
    MOVE_FILE = "move_file"
    OVERWRITE_FILE = "overwrite_file"
    DELETE_FILE = "delete_file"
    SEND_MESSAGE = "send_message"
    SUBMIT_FORM = "submit_form"
    PUBLISH_CONTENT = "publish_content"
    SUMMARIZE_GMAIL_EMAIL = "summarize_gmail_email"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    source: RequestSource = RequestSource.SPEECH
    request_id: UUID = Field(default_factory=uuid4)
    confidence: float | None = Field(default=None, ge=0, le=1)
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DraftAction(BaseModel):
    """An untrusted action proposed by the LLM.

    The LLM uses local step keys for dependencies. Stable IDs, risk, and
    confirmation requirements are assigned by deterministic application code.
    """

    step_key: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    type: ActionType
    target: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=300)
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    expected_result: dict[str, Any] = Field(default_factory=dict)


class DraftPlan(BaseModel):
    summary: str = Field(min_length=1, max_length=1_000)
    actions: list[DraftAction] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_dependencies(self) -> "DraftPlan":
        keys = [action.step_key for action in self.actions]
        if len(keys) != len(set(keys)):
            raise ValueError("action step_key values must be unique")

        completed: set[str] = set()
        for action in self.actions:
            unknown = set(action.depends_on) - completed
            if unknown:
                names = ", ".join(sorted(unknown))
                raise ValueError(
                    f"action {action.step_key!r} has unknown or forward dependencies: {names}"
                )
            completed.add(action.step_key)
        return self


class Action(BaseModel):
    action_id: UUID
    sequence: int = Field(ge=1)
    type: ActionType
    target: str
    description: str = ""
    parameters: dict[str, Any]
    depends_on: list[UUID]
    risk: RiskLevel
    requires_confirmation: bool
    expected_result: dict[str, Any]


class ActionPlan(BaseModel):
    plan_id: UUID
    request_id: UUID
    summary: str
    actions: list[Action]


class PlanningResponse(BaseModel):
    request_id: UUID
    control_intent: ControlIntent | None = None
    plan: ActionPlan | None = None

    @model_validator(mode="after")
    def require_one_result(self) -> "PlanningResponse":
        if (self.control_intent is None) == (self.plan is None):
            raise ValueError("exactly one of control_intent or plan must be present")
        return self


class ActionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ActionResult(BaseModel):
    action_id: UUID
    status: ActionStatus
    evidence: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class ExecutePlanRequest(BaseModel):
    """Explicit user approval accompanying an execution request."""

    approved_action_ids: set[UUID] = Field(default_factory=set)


class ExecutionStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class PlanExecutionResponse(BaseModel):
    plan_id: UUID
    status: ExecutionStatus
    results: list[ActionResult]
