from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.routes import get_planner
from app.main import app
from app.planning.normalizer import build_action_plan, classify_risk
from app.schemas import ActionType, DraftAction, DraftPlan, TaskRequest


class FakePlanner:
    def __init__(self, draft: DraftPlan) -> None:
        self.draft = draft
        self.calls = 0

    async def create_draft(self, request: TaskRequest) -> DraftPlan:
        self.calls += 1
        return self.draft


@pytest.fixture
def calculator_draft() -> DraftPlan:
    return DraftPlan(
        summary="Calculate 25 multiplied by 4",
        actions=[
            DraftAction(
                step_key="open",
                type="open_application",
                target="Calculator",
                description="I'll open Calculator.",
                expected_result={"application": "Calculator", "state": "focused"},
            ),
            DraftAction(
                step_key="type",
                type="type_text",
                target="Calculator",
                description="I'll enter the calculation.",
                parameters={"text": "25*4"},
                depends_on=["open"],
                expected_result={"screen_contains": "100"},
            ),
        ],
    )


def make_client(planner: Any) -> TestClient:
    app.dependency_overrides[get_planner] = lambda: planner
    return TestClient(app)


def test_create_plan_assigns_ids_and_resolves_dependencies(
    calculator_draft: DraftPlan,
) -> None:
    planner = FakePlanner(calculator_draft)

    with make_client(planner) as client:
        response = client.post(
            "/api/v1/plans",
            json={"text": "Open Calculator and calculate 25 multiplied by 4"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 201
    plan = response.json()["plan"]
    assert plan["plan_id"]
    assert [action["sequence"] for action in plan["actions"]] == [1, 2]
    assert plan["actions"][1]["depends_on"] == [plan["actions"][0]["action_id"]]
    assert plan["actions"][0]["risk"] == "low"
    assert plan["actions"][0]["requires_confirmation"] is False
    assert planner.calls == 1


def test_destructive_action_is_classified_by_application() -> None:
    planner = FakePlanner(
        DraftPlan(
            summary="Delete a file",
            actions=[
                DraftAction(
                    step_key="delete",
                    type="delete_file",
                    target="/tmp/example.txt",
                    description="I'll move the file to the Trash.",
                    expected_result={"file_exists": False},
                )
            ],
        )
    )

    with make_client(planner) as client:
        response = client.post(
            "/api/v1/plans",
            json={"text": "Delete /tmp/example.txt"},
        )
    app.dependency_overrides.clear()

    action = response.json()["plan"]["actions"][0]
    assert action["risk"] == "high"
    assert action["requires_confirmation"] is True


def test_control_intent_bypasses_ollama(calculator_draft: DraftPlan) -> None:
    planner = FakePlanner(calculator_draft)

    with make_client(planner) as client:
        response = client.post("/api/v1/plans", json={"text": "cancel"})
    app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["control_intent"] == "cancel"
    assert response.json()["plan"] is None
    assert planner.calls == 0


def test_forward_dependency_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown or forward dependencies"):
        DraftPlan(
            summary="Invalid plan",
            actions=[
                DraftAction(
                    step_key="first",
                    type="click_element",
                    target="button",
                    description="I'll select the button.",
                    depends_on=["second"],
                ),
                DraftAction(
                    step_key="second",
                    type="open_application",
                    target="Calculator",
                    description="I'll open Calculator.",
                ),
            ],
        )


def test_unrequested_viewer_step_is_removed_from_file_open_plan() -> None:
    request = TaskRequest(text="Open the latest PDF in Downloads")
    draft = DraftPlan(
        summary="I'll open the newest PDF for you.",
        actions=[
            DraftAction(
                step_key="open_file",
                type="open_file",
                target="Downloads",
                description="I'll find and open the newest PDF.",
                parameters={"selection": "latest", "extension": ".pdf"},
            ),
            DraftAction(
                step_key="focus_viewer",
                type="focus_application",
                target="Adobe Acrobat Reader",
                description="I'll bring the PDF viewer into focus.",
            ),
        ],
    )

    plan = build_action_plan(request, draft)

    assert [action.type for action in plan.actions] == ["open_file"]


def test_browser_launch_step_is_removed_from_url_plan() -> None:
    request = TaskRequest(text="Open bing.com in Google Chrome")
    draft = DraftPlan(
        summary="I'll open Bing in Chrome for you.",
        actions=[
            DraftAction(
                step_key="open_browser",
                type="open_application",
                target="Google Chrome",
            ),
            DraftAction(
                step_key="open_bing",
                type="open_url",
                target="https://bing.com",
                parameters={"browser": "Google Chrome"},
                depends_on=["open_browser"],
            ),
        ],
    )

    plan = build_action_plan(request, draft)

    assert [action.type for action in plan.actions] == ["open_url"]
    assert plan.actions[0].depends_on == []


def test_directory_listing_replaces_incorrect_open_and_read_steps() -> None:
    request = TaskRequest(text="List the files present in the Downloads folder")
    draft = DraftPlan(
        summary="I'll list the files in Downloads.",
        actions=[
            DraftAction(
                step_key="open",
                type="open_file",
                target="Downloads",
            ),
            DraftAction(
                step_key="read",
                type="read_file",
                target="Downloads",
                depends_on=["open"],
            ),
        ],
    )

    plan = build_action_plan(request, draft)

    assert len(plan.actions) == 1
    assert plan.actions[0].type is ActionType.LIST_DIRECTORY
    assert plan.actions[0].target == "Downloads"
    assert plan.actions[0].depends_on == []


def test_closing_applications_is_high_risk() -> None:
    assert classify_risk(ActionType.CLOSE_APPLICATION) == "high"
    assert classify_risk(ActionType.CLOSE_ALL_APPLICATIONS) == "high"
