"""Deterministic coverage for the create-intent / hallucinated-path defects.

These reproduce the live demo failure where "create a new excel doc" produced a
single `open_file` action targeting a fabricated
`C:\\Users\\User\\Desktop\\new_excel.docx`, and lock in the intended behaviour:
a spreadsheet create is planned and executed for real, while a request no visible
action can satisfy becomes an explicit refusal instead of a doomed plan.
"""

# ruff: noqa: I001

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.api.routes import get_plan_repository, get_planner
from app.execution.hybrid import HybridExecutor
from app.planning.capabilities import detect_unsupported_request
from app.planning.exceptions import InvalidPlannerResponseError
from app.planning.normalizer import build_action_plan
from app.planning.planner import SYSTEM_PROMPT
from app.planning.repository import PlanRepository
from app.planning.service import PlanningService
from app.main import app
from app.schemas import (
    ActionType,
    DraftAction,
    DraftPlan,
    PlanningResponse,
    TaskRequest,
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
