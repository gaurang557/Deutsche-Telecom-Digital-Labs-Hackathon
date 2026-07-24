from pathlib import Path
from uuid import uuid4

from app.planning.repository import PlanRepository
from app.schemas import ActionPlan, ExecutionStatus, RequestSource, TaskRequest


def make_plan() -> ActionPlan:
    return ActionPlan(
        plan_id=uuid4(),
        request_id=uuid4(),
        summary="Test plan",
        actions=[],
    )


def test_plan_repository_persists_tasks_across_instances(tmp_path: Path) -> None:
    database = tmp_path / "tasks.db"
    plan = make_plan()
    request = TaskRequest(text="Open Calculator", source=RequestSource.TEXT)

    PlanRepository(database).save(plan, request)
    restored = PlanRepository(database).detail(plan.plan_id)

    assert restored is not None
    assert restored.request_text == "Open Calculator"
    assert restored.status is ExecutionStatus.PLANNED
    assert restored.events[0].event_type == "plan_created"


def test_plan_repository_enforces_control_transitions(tmp_path: Path) -> None:
    repository = PlanRepository(tmp_path / "tasks.db")
    plan = make_plan()
    repository.save(plan)

    assert repository.claim_execution(plan.plan_id)
    assert repository.control(plan.plan_id, "pause") is ExecutionStatus.PAUSED
    assert repository.control(plan.plan_id, "resume") is ExecutionStatus.RUNNING
    assert repository.control(plan.plan_id, "cancel") is ExecutionStatus.CANCELLED
