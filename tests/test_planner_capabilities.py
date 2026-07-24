"""Deterministic coverage for the create-intent / hallucinated-path defects.

These reproduce the live demo failure where "create a new excel doc" produced a
single `open_file` action targeting a fabricated
`C:\\Users\\User\\Desktop\\new_excel.docx`, and lock in the intended behaviour:
a spreadsheet create is planned and executed for real, while a request no visible
action can satisfy becomes an explicit refusal instead of a doomed plan.
"""

# ruff: noqa: I001

import json
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pydantic import ValidationError

from app.api.routes import get_plan_repository, get_planner
from app.config import Settings
from app.execution.hybrid import HybridExecutor
from app.planning.capabilities import (
    detect_unsupported_request,
    find_fabricated_user_profile_path,
)
from app.planning.exceptions import InvalidPlannerResponseError
from app.planning.normalizer import build_action_plan
from app.planning.planner import (
    MAX_PLANNING_ATTEMPTS,
    SYSTEM_PROMPT,
    WORKED_EXAMPLE,
    OllamaPlanner,
    minimum_action_count,
)
from app.planning.repository import PlanRepository
from app.planning.service import PlanningService
from app.main import app
from app.schemas import (
    PLANNER_VISIBLE_STRUCTURED_ACTIONS,
    ActionType,
    DraftAction,
    DraftPlan,
    PlanningResponse,
    TaskRequest,
)
from app.structured_actions import (
    PLANNER_VISIBLE_ACTION_TYPES,
    STRUCTURED_ACTION_TYPES,
)


class _ExplodingPlanner:
    """Fails the test if the LLM is consulted for an impossible request."""

    def __init__(self) -> None:
        self.calls = 0

    async def create_draft(self, request: TaskRequest) -> DraftPlan:
        self.calls += 1
        raise AssertionError("The planner must not be called for a refused request")


class _FixedPlanner:
    def __init__(self, draft: DraftPlan) -> None:
        self._draft = draft

    async def create_draft(self, request: TaskRequest) -> DraftPlan:
        return self._draft


def _create_workbook_draft(workbook_path: Path) -> DraftPlan:
    """The plan a correctly-guided planner produces for a spreadsheet create."""
    return DraftPlan(
        summary="I'll start a new workbook for you.",
        actions=[
            DraftAction(
                step_key="new_book",
                type="spreadsheet.write_cell",
                target=str(workbook_path),
                parameters={"sheet": "Sheet1", "cell": "A1", "value": "Untitled"},
                description="Start a new spreadsheet.",
                expected_result={"cell": "Sheet1!A1", "value": "Untitled"},
            )
        ],
    )


# ── the capability actually exists: a spreadsheet create is real work ──────────
@pytest.mark.asyncio
async def test_create_intent_for_spreadsheet_creates_and_verifies_new_workbook(
    tmp_path: Path,
) -> None:
    workbook_path = tmp_path / "new_workbook.xlsx"
    assert not workbook_path.exists()
    plan = build_action_plan(
        TaskRequest(text="create a new excel doc"),
        _create_workbook_draft(workbook_path),
    )

    response = await HybridExecutor().execute_plan(plan, set())

    assert response.status == "completed"
    assert response.results[0].verification is not None
    assert response.results[0].verification.passed is True
    assert response.results[0].evidence["created"] is True
    workbook = load_workbook(workbook_path, data_only=False)
    try:
        assert workbook["Sheet1"]["A1"].value == "Untitled"
    finally:
        workbook.close()


@pytest.mark.asyncio
async def test_spreadsheet_create_request_is_not_refused(tmp_path: Path) -> None:
    """A supported create must reach the planner, never the refusal path."""
    workbook_path = tmp_path / "new_workbook.xlsx"
    service = PlanningService(
        _FixedPlanner(_create_workbook_draft(workbook_path)),
        PlanRepository(tmp_path / "runtime.db"),
    )

    result = await service.plan(TaskRequest(text="create a new excel doc"))

    assert result.refusal is None
    assert result.plan is not None
    assert [str(action.type) for action in result.plan.actions] == [
        "spreadsheet.write_cell"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "create a new excel doc",
        "make me a spreadsheet",
        "create a folder on Desktop for invoices",
        "create a text file with my notes",
        "put North revenue from Downloads\\report.pdf into Documents\\revenue.xlsx",
        "open the latest PDF in Downloads",
        "replace the headline in the presentation template and save a new copy",
    ],
)
def test_supported_requests_are_never_refused(text: str) -> None:
    assert detect_unsupported_request(text) is None


# ── genuinely unsupported creates refuse instead of guessing ──────────────────
@pytest.mark.parametrize(
    ("text", "expected_phrase"),
    [
        ("create a new powerpoint", "PowerPoint"),
        ("make me a slide deck about revenue", "PowerPoint"),
        ("generate a presentation for Monday", "PowerPoint"),
        ("create a new word document", "Word"),
        ("make a docx for the summary", "Word"),
        ("create a PDF of the report", "PDF"),
    ],
)
def test_unsupported_creates_produce_a_legible_refusal(
    text: str,
    expected_phrase: str,
) -> None:
    refusal = detect_unsupported_request(text)
    assert refusal is not None
    assert expected_phrase in refusal
    # A refusal must say what the user *can* do next, not just decline.
    assert "I can" in refusal


@pytest.mark.asyncio
async def test_refused_request_never_reaches_the_planner(tmp_path: Path) -> None:
    planner = _ExplodingPlanner()
    service = PlanningService(planner, PlanRepository(tmp_path / "runtime.db"))

    result = await service.plan(TaskRequest(text="create a new powerpoint"))

    assert planner.calls == 0
    assert result.plan is None
    assert result.control_intent is None
    assert result.refusal is not None
    assert "PowerPoint" in result.refusal


def test_refusal_is_surfaced_to_the_ui_through_the_planning_api(
    tmp_path: Path,
) -> None:
    app.dependency_overrides[get_planner] = _ExplodingPlanner
    app.dependency_overrides[get_plan_repository] = lambda: PlanRepository(
        tmp_path / "runtime.db"
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/plans",
                json={"text": "create a new powerpoint"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["plan"] is None
    assert body["control_intent"] is None
    assert "PowerPoint" in body["refusal"]


def test_planning_response_still_requires_exactly_one_outcome() -> None:
    with pytest.raises(ValueError):
        PlanningResponse(request_id=uuid4())
    with pytest.raises(ValueError):
        PlanningResponse(
            request_id=uuid4(),
            control_intent="cancel",
            refusal="I can't do that.",
        )


# ── hallucinated paths are rejected deterministically ─────────────────────────
def test_fabricated_user_profile_path_is_rejected() -> None:
    """The exact path from the live failure must never become a plan."""
    draft = DraftPlan(
        summary="I'll open the new workbook.",
        actions=[
            DraftAction(
                step_key="open_it",
                type=ActionType.OPEN_FILE,
                target="C:\\Users\\User\\Desktop\\new_excel.docx",
                expected_result={},
            )
        ],
    )

    with pytest.raises(InvalidPlannerResponseError) as excinfo:
        build_action_plan(TaskRequest(text="create a new excel doc"), draft)

    assert "invented an absolute user-profile path" in str(excinfo.value)
    assert "Downloads" in str(excinfo.value)


def test_fabricated_user_profile_path_is_rejected_in_destination_parameter() -> None:
    draft = DraftPlan(
        summary="I'll copy that file.",
        actions=[
            DraftAction(
                step_key="copy_it",
                type="file.copy",
                target="Documents\\notes.txt",
                parameters={"destination": "C:/Users/SomeoneElse/Desktop/notes.txt"},
                expected_result={},
            )
        ],
    )

    with pytest.raises(InvalidPlannerResponseError):
        build_action_plan(TaskRequest(text="copy my notes to the desktop"), draft)


def test_windows_short_name_profile_path_is_not_mistaken_for_a_fabrication() -> None:
    """`C:\\Users\\ANIKET~1\\...` is the user's own profile, spelled 8.3-style.

    Comparing the profile segment as text rejected every path under the user's
    own temp directory, which broke real work while the pytest `tmp_path`
    fixture (which hands out the long form) kept the suite green.
    """
    own_temp = Path(tempfile.mkdtemp(prefix="ps2_shortname_"))
    try:
        assert find_fabricated_user_profile_path(str(own_temp / "book.xlsx")) is None
    finally:
        own_temp.rmdir()


def test_foreign_profile_is_still_rejected_after_the_short_name_fix() -> None:
    assert (
        find_fabricated_user_profile_path("C:\\Users\\User\\Desktop\\new_excel.docx")
        == "C:\\Users\\User\\Desktop\\new_excel.docx"
    )


def test_real_home_absolute_path_is_still_accepted(tmp_path: Path) -> None:
    """The guard must only reject *foreign* profiles, not legitimate paths."""
    workbook_path = tmp_path / "book.xlsx"
    plan = build_action_plan(
        TaskRequest(text="create a new excel doc"),
        _create_workbook_draft(workbook_path),
    )
    assert plan.actions[0].target == str(workbook_path)


def test_spreadsheet_action_may_not_target_a_word_document() -> None:
    draft = DraftPlan(
        summary="I'll write into the spreadsheet.",
        actions=[
            DraftAction(
                step_key="write_it",
                type="spreadsheet.write_cell",
                target="Desktop\\new_excel.docx",
                parameters={"sheet": "Sheet1", "cell": "A1", "value": "hi"},
                expected_result={},
            )
        ],
    )

    with pytest.raises(InvalidPlannerResponseError) as excinfo:
        build_action_plan(TaskRequest(text="create a new excel doc"), draft)

    assert "mismatched file type" in str(excinfo.value)
    assert ".xlsx" in str(excinfo.value)


@pytest.mark.parametrize(
    ("action_type", "target", "parameters"),
    [
        # A planner was observed reaching for the plain-text actions on binary
        # office files; write_text on an .xlsx would replace the workbook.
        ("file.write_text", "Documents/catalogue.xlsx", {"content": "27.4"}),
        ("file.read_text", "Documents/catalogue.xlsx", {}),
        ("file.read_text", "Downloads/report.pdf", {}),
        ("file.write_text", "Desktop/deck.pptx", {"content": "x"}),
        ("pdf.read_text", "Downloads\\report.xlsx", {}),
        ("document.read_text", "Documents\\brief.pptx", {}),
        ("presentation.read_text", "Desktop\\deck.docx", {}),
        (
            "presentation.replace_text",
            "Desktop\\deck.pptx",
            {"find": "{{X}}", "save_as": "Desktop\\out.docx"},
        ),
    ],
)
def test_action_family_and_extension_must_agree(
    action_type: str,
    target: str,
    parameters: dict,
) -> None:
    draft = DraftPlan(
        summary="I'll read that file.",
        actions=[
            DraftAction(
                step_key="step",
                type=action_type,
                target=target,
                parameters=parameters,
                expected_result={},
            )
        ],
    )

    with pytest.raises(InvalidPlannerResponseError):
        build_action_plan(TaskRequest(text="do the thing"), draft)


# ── planner robustness against real captured model output ─────────────────────
#: Verbatim shapes captured from live llama3.2 while diagnosing the failure.
#: Each is realistic-but-malformed, so the repair path is exercised without a
#: live model.
_GOOD_PLAN = json.dumps(
    {
        "summary": "I'll copy that figure across for you.",
        "actions": [
            {
                "step_key": "read_source",
                "type": "pdf.read_text",
                "target": "Documents/source.pdf",
                "description": "Read the figure.",
                "parameters": {"max_chars": 4000},
                "depends_on": [],
                "expected_result": {"contains": "total"},
            },
            {
                "step_key": "write_value",
                "type": "spreadsheet.write_cell",
                "target": "Documents/target.xlsx",
                "description": "Write the figure.",
                "parameters": {
                    "sheet": "Data",
                    "cell": "C5",
                    "value": {
                        "$ref": "read_source.evidence.text",
                        "regex": r"total:\s*([0-9.]+)",
                        "group": 1,
                        "coerce": "number",
                    },
                },
                "depends_on": ["read_source"],
                "expected_result": {"written": True},
            },
        ],
    }
)

#: Captured failure 1: a conversational preamble before the JSON.
_PREAMBLE_PLAN = f"Here is the plan:\n\n{_GOOD_PLAN}"
#: Captured failure 2: the whole object wrapped in a markdown fence.
_FENCED_PLAN = f"```json\n{_GOOD_PLAN}\n```"
#: Captured failure 3: parameter names that belong to a different action.
_WRONG_PARAMS_PLAN = json.dumps(
    {
        "summary": "I'll find that in the document.",
        "actions": [
            {
                "step_key": "find_it",
                "type": "document.read_text",
                "target": "Documents/source.docx",
                "parameters": {"page": "1", "regex": "x", "group": 1},
                "expected_result": {},
            }
        ],
    }
)


class _ScriptedPlanner(OllamaPlanner):
    """An OllamaPlanner whose transport is replaced by a fixed script."""

    def __init__(self, replies: list[str]) -> None:
        super().__init__(Settings())
        self._replies = replies
        self.prompts: list[list[dict[str, str]]] = []

    def _chat(self, messages, minimum_actions=1):  # type: ignore[override]
        self.prompts.append([dict(message) for message in messages])
        return self._replies[len(self.prompts) - 1]


@pytest.mark.parametrize(
    "reply",
    [_GOOD_PLAN, _PREAMBLE_PLAN, _FENCED_PLAN],
    ids=["bare", "preamble", "markdown_fence"],
)
def test_planner_recovers_a_plan_wrapped_in_prose_or_fences(reply: str) -> None:
    planner = _ScriptedPlanner([reply])

    draft = planner._create_draft_sync(TaskRequest(text="copy the total across"))

    assert [str(action.type) for action in draft.actions] == [
        "pdf.read_text",
        "spreadsheet.write_cell",
    ]
    assert len(planner.prompts) == 1


def test_planner_repairs_a_malformed_plan_by_returning_the_error() -> None:
    planner = _ScriptedPlanner([_WRONG_PARAMS_PLAN, _GOOD_PLAN])

    draft = planner._create_draft_sync(TaskRequest(text="copy the total across"))

    assert len(draft.actions) == 2
    assert len(planner.prompts) == 2
    repair_turn = planner.prompts[1][-1]
    assert repair_turn["role"] == "user"
    # The repair must actually name the problem, not blindly resample.
    assert "Unknown parameters" in repair_turn["content"]
    assert "document.read_text" in repair_turn["content"]


def test_planner_fails_closed_after_exhausting_repair_attempts() -> None:
    planner = _ScriptedPlanner([_WRONG_PARAMS_PLAN] * MAX_PLANNING_ATTEMPTS)

    with pytest.raises(InvalidPlannerResponseError):
        planner._create_draft_sync(TaskRequest(text="copy the total across"))

    assert len(planner.prompts) == MAX_PLANNING_ATTEMPTS


def test_planner_never_retries_more_than_the_attempt_cap() -> None:
    planner = _ScriptedPlanner(["not json at all"] * (MAX_PLANNING_ATTEMPTS + 2))

    with pytest.raises(InvalidPlannerResponseError):
        planner._create_draft_sync(TaskRequest(text="do something"))

    assert len(planner.prompts) == MAX_PLANNING_ATTEMPTS


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("open Calculator", 1),
        ("create a new spreadsheet on the desktop", 1),
        ("put the total from a/source.pdf into b/target.xlsx", 2),
        ("read notes.docx and update deck.pptx", 2),
    ],
)
def test_action_floor_reflects_how_many_files_the_request_names(
    text: str,
    expected: int,
) -> None:
    """Constrained decoding closes the actions array early; this is the floor."""
    assert minimum_action_count(text) == expected


def test_hidden_structured_actions_are_rejected_even_though_the_registry_has_them(
) -> None:
    """Trimming the planner surface must be enforced, not merely advertised."""
    for hidden in ("file.delete", "document.find", "presentation.slide_count"):
        with pytest.raises(ValidationError):
            DraftAction.model_validate(
                {
                    "step_key": "hidden",
                    "type": hidden,
                    "target": "Documents/thing.txt",
                    "expected_result": {},
                }
            )


def test_planner_visible_surface_excludes_deletion_entirely() -> None:
    assert "file.delete" not in PLANNER_VISIBLE_ACTION_TYPES
    assert "file.delete" not in PLANNER_VISIBLE_STRUCTURED_ACTIONS
    assert PLANNER_VISIBLE_STRUCTURED_ACTIONS < STRUCTURED_ACTION_TYPES


def test_worked_example_in_the_prompt_is_itself_a_valid_plan() -> None:
    """A typo in the example would teach the model an invalid shape."""
    body = WORKED_EXAMPLE[WORKED_EXAMPLE.index("{") : WORKED_EXAMPLE.rindex("}") + 1]
    draft = DraftPlan.model_validate_json(body)
    assert len(draft.actions) >= 2
    assert any(
        isinstance(action.parameters.get("value"), dict)
        and "$ref" in action.parameters["value"]
        for action in draft.actions
    )


@pytest.mark.parametrize(
    "literal",
    [
        "quarterly_report",
        "results_blank",
        "report_summary.docx",
        "summary_template",
        "RECOMMENDATION_PLACEHOLDER",
        "Summary",
        "North",
        "South",
        "27.4",
        "31.8",
    ],
)
def test_prompt_contains_no_sample_fixture_literals(literal: str) -> None:
    """Guards against tuning the prompt to the sample prompts we were given."""
    assert literal not in SYSTEM_PROMPT + WORKED_EXAMPLE


def test_prompt_requires_discovering_workbook_layout_rather_than_assuming_it(
) -> None:
    guidance = SYSTEM_PROMPT + WORKED_EXAMPLE
    assert "Never assume a workbook's layout" in guidance
    assert "spreadsheet.list_sheets" in guidance
    assert "matches more than one row" in guidance


# ── the prompt itself must carry the corrected guidance ───────────────────────
def test_prompt_maps_create_intent_to_a_create_capable_action() -> None:
    assert "create a new spreadsheet" in SYSTEM_PROMPT
    assert "it creates the workbook" in SYSTEM_PROMPT
    assert "never answer with open_file" in SYSTEM_PROMPT


def test_prompt_states_the_correct_extension_for_each_document_kind() -> None:
    assert ".xlsx is Excel" in SYSTEM_PROMPT
    assert "always .xlsx and never .docx" in SYSTEM_PROMPT


def test_prompt_forbids_inventing_a_user_profile_path() -> None:
    assert "C:\\Users\\... path that the user did not say" in SYSTEM_PROMPT
    assert "Desktop, Documents, or Downloads" in SYSTEM_PROMPT


def test_prompt_states_which_creates_are_impossible() -> None:
    assert "cannot create a new PDF, a new Word document, or a new PowerPoint" in (
        SYSTEM_PROMPT
    )
