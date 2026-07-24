from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class StructuredActionType(StrEnum):
    FILE_EXISTS = "file.exists"
    FILE_LIST = "file.list"
    FILE_READ_TEXT = "file.read_text"
    FILE_COPY = "file.copy"
    FILE_MOVE = "file.move"
    FILE_WRITE_TEXT = "file.write_text"
    FILE_MKDIR = "file.mkdir"
    FILE_DELETE = "file.delete"
    PDF_PAGE_COUNT = "pdf.page_count"
    PDF_GET_METADATA = "pdf.get_metadata"
    PDF_READ_TEXT = "pdf.read_text"
    PDF_SEARCH = "pdf.search"
    SPREADSHEET_LIST_SHEETS = "spreadsheet.list_sheets"
    SPREADSHEET_DIMENSIONS = "spreadsheet.dimensions"
    SPREADSHEET_READ_CELL = "spreadsheet.read_cell"
    SPREADSHEET_READ_RANGE = "spreadsheet.read_range"
    SPREADSHEET_WRITE_CELL = "spreadsheet.write_cell"
    DOCUMENT_READ_TEXT = "document.read_text"
    DOCUMENT_GET_METADATA = "document.get_metadata"
    DOCUMENT_FIND = "document.find"
    DOCUMENT_REPLACE_TEXT = "document.replace_text"
    PRESENTATION_SLIDE_COUNT = "presentation.slide_count"
    PRESENTATION_GET_METADATA = "presentation.get_metadata"
    PRESENTATION_READ_TEXT = "presentation.read_text"
    PRESENTATION_FIND = "presentation.find"
    PRESENTATION_REPLACE_TEXT = "presentation.replace_text"


#: The structured actions the planner is allowed to propose.
#:
#: The dispatcher still registers and supports every member of
#: `StructuredActionType`; this narrower set is only what the local model is
#: shown and permitted to emit. It exists for two reasons found by diagnosing a
#: live planning failure: a small model picks whichever plausible name it sees
#: first (it reached for `document.find` on a PDF), and `file.delete` must never
#: be reachable from model output at all. Anything omitted here is rejected
#: deterministically by `DraftAction`, independently of the policy engine.
PLANNER_VISIBLE_STRUCTURED_ACTIONS: frozenset[str] = frozenset(
    {
        StructuredActionType.PDF_READ_TEXT.value,
        StructuredActionType.PDF_SEARCH.value,
        StructuredActionType.SPREADSHEET_LIST_SHEETS.value,
        StructuredActionType.SPREADSHEET_DIMENSIONS.value,
        StructuredActionType.SPREADSHEET_READ_CELL.value,
        StructuredActionType.SPREADSHEET_READ_RANGE.value,
        StructuredActionType.SPREADSHEET_WRITE_CELL.value,
        StructuredActionType.DOCUMENT_READ_TEXT.value,
        StructuredActionType.DOCUMENT_REPLACE_TEXT.value,
        StructuredActionType.PRESENTATION_READ_TEXT.value,
        StructuredActionType.PRESENTATION_REPLACE_TEXT.value,
        StructuredActionType.FILE_EXISTS.value,
        StructuredActionType.FILE_READ_TEXT.value,
        StructuredActionType.FILE_WRITE_TEXT.value,
        StructuredActionType.FILE_COPY.value,
        StructuredActionType.FILE_MOVE.value,
        StructuredActionType.FILE_MKDIR.value,
    }
)

STRUCTURED_PARAMETER_KEYS: dict[StructuredActionType, frozenset[str]] = {
    StructuredActionType.FILE_EXISTS: frozenset(),
    StructuredActionType.FILE_LIST: frozenset({"pattern", "recursive"}),
    StructuredActionType.FILE_READ_TEXT: frozenset({"encoding", "max_bytes"}),
    StructuredActionType.FILE_COPY: frozenset({"destination", "overwrite"}),
    StructuredActionType.FILE_MOVE: frozenset({"destination", "overwrite"}),
    StructuredActionType.FILE_WRITE_TEXT: frozenset(
        {"content", "overwrite", "encoding"}
    ),
    StructuredActionType.FILE_MKDIR: frozenset({"parents", "exist_ok"}),
    StructuredActionType.FILE_DELETE: frozenset({"missing_ok"}),
    StructuredActionType.PDF_PAGE_COUNT: frozenset(),
    StructuredActionType.PDF_GET_METADATA: frozenset(),
    StructuredActionType.PDF_READ_TEXT: frozenset(
        {"page", "start_page", "end_page", "max_chars"}
    ),
    StructuredActionType.PDF_SEARCH: frozenset({"query", "max_results"}),
    StructuredActionType.SPREADSHEET_LIST_SHEETS: frozenset(),
    StructuredActionType.SPREADSHEET_DIMENSIONS: frozenset({"sheet"}),
    StructuredActionType.SPREADSHEET_READ_CELL: frozenset({"sheet", "cell"}),
    StructuredActionType.SPREADSHEET_READ_RANGE: frozenset({"sheet", "range"}),
    StructuredActionType.SPREADSHEET_WRITE_CELL: frozenset(
        {"sheet", "cell", "value", "overwrite"}
    ),
    StructuredActionType.DOCUMENT_READ_TEXT: frozenset({"max_chars"}),
    StructuredActionType.DOCUMENT_GET_METADATA: frozenset(),
    StructuredActionType.DOCUMENT_FIND: frozenset({"query", "max_results"}),
    StructuredActionType.DOCUMENT_REPLACE_TEXT: frozenset(
        {"find", "replace", "count", "save_as", "overwrite"}
    ),
    StructuredActionType.PRESENTATION_SLIDE_COUNT: frozenset(),
    StructuredActionType.PRESENTATION_GET_METADATA: frozenset(),
    StructuredActionType.PRESENTATION_READ_TEXT: frozenset({"slide", "max_chars"}),
    StructuredActionType.PRESENTATION_FIND: frozenset({"query", "max_results"}),
    StructuredActionType.PRESENTATION_REPLACE_TEXT: frozenset(
        {"find", "replace", "count", "save_as", "overwrite"}
    ),
}

STRUCTURED_REQUIRED_PARAMETER_KEYS: dict[
    StructuredActionType,
    frozenset[str],
] = {
    StructuredActionType.FILE_COPY: frozenset({"destination"}),
    StructuredActionType.FILE_MOVE: frozenset({"destination"}),
    StructuredActionType.FILE_WRITE_TEXT: frozenset({"content"}),
    StructuredActionType.PDF_SEARCH: frozenset({"query"}),
    StructuredActionType.SPREADSHEET_READ_CELL: frozenset({"cell"}),
    StructuredActionType.SPREADSHEET_READ_RANGE: frozenset({"range"}),
    StructuredActionType.SPREADSHEET_WRITE_CELL: frozenset({"cell", "value"}),
    StructuredActionType.DOCUMENT_FIND: frozenset({"query"}),
    StructuredActionType.DOCUMENT_REPLACE_TEXT: frozenset({"find"}),
    StructuredActionType.PRESENTATION_FIND: frozenset({"query"}),
    StructuredActionType.PRESENTATION_REPLACE_TEXT: frozenset({"find"}),
}

_PLANNER_AUTHORITY_KEYS = frozenset(
    {
        "authorization",
        "authorized",
        "cmd",
        "command",
        "confirmation",
        "confirmation_token",
        "permission",
        "permissions",
        "policy",
        "powershell",
        "requires_confirmation",
        "risk",
        "risk_level",
        "rule",
        "rule_id",
        "script",
        "shell",
        "trust",
        "trusted",
    }
)


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

    model_config = ConfigDict(extra="forbid")

    step_key: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    type: ActionType | StructuredActionType
    target: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=300)
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    expected_result: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_authority_and_malformed_parameters(self) -> "DraftAction":
        def reserved_keys(value: Any) -> set[str]:
            if isinstance(value, dict):
                found = {
                    str(key).casefold()
                    for key in value
                    if str(key).casefold() in _PLANNER_AUTHORITY_KEYS
                }
                for nested in value.values():
                    found.update(reserved_keys(nested))
                return found
            if isinstance(value, list):
                found: set[str] = set()
                for nested in value:
                    found.update(reserved_keys(nested))
                return found
            return set()

        reserved = reserved_keys(self.parameters) | reserved_keys(self.expected_result)
        if reserved:
            raise ValueError(
                f"Planner authority fields are forbidden: {sorted(reserved)}"
            )
        if isinstance(self.type, StructuredActionType):
            if self.type.value not in PLANNER_VISIBLE_STRUCTURED_ACTIONS:
                raise ValueError(
                    f"{self.type.value} is not available to the planner"
                )
            provided = set(self.parameters)
            unknown = provided - STRUCTURED_PARAMETER_KEYS[self.type]
            if unknown:
                raise ValueError(
                    f"Unknown parameters for {self.type.value}: {sorted(unknown)}"
                )
            required = STRUCTURED_REQUIRED_PARAMETER_KEYS.get(self.type, frozenset())
            missing = required - provided
            if missing:
                raise ValueError(
                    f"Missing parameters for {self.type.value}: {sorted(missing)}"
                )
        return self


class DraftPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    step_key: str | None = None
    type: ActionType | StructuredActionType | str
    target: str
    description: str = ""
    parameters: dict[str, Any]
    depends_on: list[UUID]
    risk: RiskLevel
    requires_confirmation: bool
    confirmation_hash: str | None = None
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
    refusal: str | None = Field(default=None, max_length=1_000)
    """Set when no planner-visible action can satisfy the request.

    A refusal is a deliberate outcome, not a failure: it is preferred over a
    fabricated plan that could only fail at execution time.
    """

    @model_validator(mode="after")
    def require_one_result(self) -> "PlanningResponse":
        present = sum(
            value is not None
            for value in (self.control_intent, self.plan, self.refusal)
        )
        if present != 1:
            raise ValueError(
                "exactly one of control_intent, plan, or refusal must be present"
            )
        return self


class ActionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class VerificationResult(BaseModel):
    passed: bool | None = None
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class ActionResult(BaseModel):
    action_id: UUID
    status: ActionStatus
    evidence: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    verification: VerificationResult | None = None


class ExecutePlanRequest(BaseModel):
    """Explicit user approval accompanying an execution request."""

    approved_action_ids: set[UUID] = Field(default_factory=set)
    approved_action_hashes: dict[UUID, str] = Field(default_factory=dict)


class ExecutionStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class PlanExecutionResponse(BaseModel):
    plan_id: UUID
    status: ExecutionStatus
    results: list[ActionResult]


class PlanControlRequest(BaseModel):
    intent: Literal["pause", "resume", "cancel"]


class TaskSummary(BaseModel):
    plan_id: UUID
    request_id: UUID
    request_text: str
    summary: str
    status: ExecutionStatus
    created_at: datetime
    updated_at: datetime


class TaskEvent(BaseModel):
    event_type: str
    message: str
    created_at: datetime


class TaskDetail(TaskSummary):
    plan: ActionPlan
    results: list[ActionResult] = Field(default_factory=list)
    events: list[TaskEvent] = Field(default_factory=list)
