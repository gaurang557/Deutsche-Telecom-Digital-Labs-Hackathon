import os
import subprocess
from pathlib import Path
from typing import Any
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
    target: str = "Calculator",
    parameters: dict[str, Any] | None = None,
) -> Action:
    return Action(
        action_id=uuid4(),
        sequence=1,
        type=action_type,
        target=target,
        parameters=parameters or {},
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


def test_open_file_selects_latest_pdf(
    tmp_path: Path,
    monkeypatch,
) -> None:
    older = tmp_path / "older.pdf"
    latest = tmp_path / "latest.pdf"
    ignored = tmp_path / "newer.txt"
    older.write_bytes(b"old")
    latest.write_bytes(b"latest")
    ignored.write_text("ignored")
    os.utime(older, (1, 1))
    os.utime(latest, (2, 2))
    os.utime(ignored, (3, 3))
    launched: list[list[str]] = []

    monkeypatch.setattr("app.execution.executor.platform.system", lambda: "Darwin")
    monkeypatch.setattr(
        "app.execution.executor.subprocess.run",
        lambda command, **_: launched.append(command),
    )
    action = make_action(
        action_type=ActionType.OPEN_FILE,
        target=str(tmp_path),
        parameters={"selection": "latest", "extension": ".pdf"},
    )

    result = DesktopExecutor()._execute_action(action)

    assert result.status == "succeeded"
    assert result.evidence["path"] == str(latest)
    assert launched == [["open", str(latest)]]


def test_application_launch_error_becomes_action_failure(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr="not found")

    monkeypatch.setattr("app.execution.executor.platform.system", lambda: "Darwin")
    monkeypatch.setattr("app.execution.executor.subprocess.run", fail)

    result = DesktopExecutor()._execute_action(make_action())

    assert result.status == "failed"
    assert "non-zero exit status" in (result.error or "")
