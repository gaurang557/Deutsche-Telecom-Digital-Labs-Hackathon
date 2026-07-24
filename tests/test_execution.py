from uuid import uuid4

from app.execution.executor import DesktopExecutor
from app.schemas import (
    Action,
    ActionPlan,
    ActionResult,
    ActionStatus,
    ActionType,
    RiskLevel,
)


class RecordingExecutor(DesktopExecutor):
    def __init__(self) -> None:
        self.executed: list = []

    def _execute_action(self, action: Action) -> ActionResult:
        self.executed.append(action.action_id)
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCEEDED,
        )


def make_action(
    *,
    action_type: ActionType = ActionType.OPEN_APPLICATION,
    confirmation: bool = False,
    dependencies: list | None = None,
) -> Action:
    return Action(
        action_id=uuid4(),
        sequence=1,
        type=action_type,
        target="Calculator",
        parameters={},
        depends_on=dependencies or [],
        risk=RiskLevel.HIGH if confirmation else RiskLevel.LOW,
        requires_confirmation=confirmation,
        expected_result={},
    )


def make_plan(actions: list[Action]) -> ActionPlan:
    return ActionPlan(
        plan_id=uuid4(),
        request_id=uuid4(),
        summary="Test plan",
        actions=actions,
    )


async def test_executes_approved_plan_in_order() -> None:
    first = make_action()
    second = make_action(dependencies=[first.action_id])
    plan = make_plan([first, second])
    executor = RecordingExecutor()

    result = await executor.execute_plan(plan, set())

    assert result.status == "completed"
    assert executor.executed == [first.action_id, second.action_id]


async def test_blocks_unconfirmed_consequential_action() -> None:
    action = make_action(action_type=ActionType.DELETE_FILE, confirmation=True)
    executor = RecordingExecutor()

    result = await executor.execute_plan(make_plan([action]), set())

    assert result.status == "blocked"
    assert result.results[0].status == "blocked"
    assert executor.executed == []


async def test_executes_consequential_action_after_confirmation() -> None:
    action = make_action(action_type=ActionType.DELETE_FILE, confirmation=True)
    executor = RecordingExecutor()

    result = await executor.execute_plan(make_plan([action]), {action.action_id})

    assert result.status == "completed"
    assert executor.executed == [action.action_id]
