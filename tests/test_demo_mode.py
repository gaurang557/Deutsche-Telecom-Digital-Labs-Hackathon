from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.execution.demo import DemoDesktopExecutor
from app.planning.hosted import DemoFallbackPlanner
from app.schemas import Action, ActionType, RiskLevel, TaskRequest


def make_action(action_type: ActionType, target: str) -> Action:
    return Action(
        action_id=uuid4(),
        sequence=1,
        type=action_type,
        target=target,
        parameters={},
        depends_on=[],
        risk=RiskLevel.LOW,
        requires_confirmation=False,
        expected_result={},
    )


async def test_demo_planner_creates_directory_listing() -> None:
    planner = DemoFallbackPlanner()

    draft = await planner.create_draft(
        TaskRequest(text="List the files in Downloads")
    )

    assert len(draft.actions) == 1
    assert draft.actions[0].type is ActionType.LIST_DIRECTORY


def test_demo_executor_lists_safe_sample_files(tmp_path: Path) -> None:
    settings = Settings(demo_sandbox_dir=str(tmp_path), demo_mode=True)
    executor = DemoDesktopExecutor(settings)
    action = make_action(ActionType.LIST_DIRECTORY, "/Users/someone/Downloads")

    result = executor._execute_action(action)

    assert result.status == "succeeded"
    assert result.evidence["environment"] == "demo"
    assert result.evidence["simulated"] is True
    assert result.evidence["count"] == 3
    assert result.verification is not None
    assert result.verification.passed is True
