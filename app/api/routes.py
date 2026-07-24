from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.voice_routes import router as voice_router
from app.config import get_settings
from app.execution.executor import DesktopExecutor
from app.planning.exceptions import (
    InvalidPlannerResponseError,
    PlannerUnavailableError,
)
from app.planning.planner import OllamaPlanner, Planner
from app.planning.repository import PlanRepository
from app.planning.service import PlanningService
from app.schemas import (
    ActionResult,
    ActionStatus,
    ExecutePlanRequest,
    ExecutionStatus,
    HealthResponse,
    PlanControlRequest,
    PlanExecutionResponse,
    PlanningResponse,
    TaskDetail,
    TaskRequest,
    TaskSummary,
)

router = APIRouter()
router.include_router(voice_router)


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check() -> HealthResponse:
    """Return the API's readiness status."""
    return HealthResponse(status="ok")


@lru_cache
def get_planner() -> Planner:
    return OllamaPlanner(get_settings())


@lru_cache
def get_plan_repository() -> PlanRepository:
    return PlanRepository()


@lru_cache
def get_desktop_executor() -> Any:
    settings = get_settings()
    if not settings.enable_structured_actions:
        return DesktopExecutor()
    from app.execution.hybrid import HybridExecutor, StoreAuditSink

    repository = get_plan_repository()
    audit = StoreAuditSink(repository)
    return HybridExecutor(audit=audit, structured_enabled=True)


def get_planning_service(
    planner: Annotated[Planner, Depends(get_planner)],
    repository: Annotated[PlanRepository, Depends(get_plan_repository)],
) -> PlanningService:
    return PlanningService(planner, repository)


@router.post(
    "/plans",
    response_model=PlanningResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["planning"],
)
async def create_plan(
    request: TaskRequest,
    service: Annotated[PlanningService, Depends(get_planning_service)],
) -> PlanningResponse:
    """Convert a speech transcript into a validated, execution-ready plan."""
    try:
        return await service.plan(request)
    except PlannerUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except InvalidPlannerResponseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


@router.post(
    "/plans/{plan_id}/execute",
    response_model=PlanExecutionResponse,
    tags=["execution"],
)
async def execute_plan(
    plan_id: UUID,
    request: ExecutePlanRequest,
    repository: Annotated[PlanRepository, Depends(get_plan_repository)],
    executor: Annotated[Any, Depends(get_desktop_executor)],
) -> PlanExecutionResponse:
    """Execute a stored plan after explicit approval from the local user."""
    plan = repository.get(plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found or the API process was restarted",
        )
    for action in plan.actions:
        if not action.requires_confirmation or "." not in str(action.type):
            continue
        provided = request.approved_action_hashes.get(action.action_id)
        if not action.confirmation_hash or provided != action.confirmation_hash:
            repository.log_event(
                plan_id,
                "confirmation_rejected",
                f"Confirmation rejected for step {action.sequence}; no action ran",
            )
            return PlanExecutionResponse(
                plan_id=plan.plan_id,
                status=ExecutionStatus.BLOCKED,
                results=[
                    ActionResult(
                        action_id=action.action_id,
                        status=ActionStatus.BLOCKED,
                        error="Exact action-bound confirmation is required",
                    )
                ],
            )
        repository.log_event(
            plan_id,
            "confirmation_accepted",
            f"Exact confirmation accepted for step {action.sequence}",
        )

    if not repository.claim_execution(plan_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This plan has already been submitted for execution",
        )
    from app.execution.hybrid import HybridExecutor

    if isinstance(executor, HybridExecutor):
        response = await executor.execute_plan(
            plan,
            request.approved_action_ids,
            control_state=lambda: repository.status(plan_id),
            approved_action_hashes=request.approved_action_hashes,
        )
    else:
        response = await executor.execute_plan(
            plan,
            request.approved_action_ids,
            control_state=lambda: repository.status(plan_id),
        )
    repository.complete(response)
    return response


@router.post(
    "/plans/{plan_id}/control",
    response_model=TaskDetail,
    tags=["execution"],
)
async def control_plan(
    plan_id: UUID,
    request: PlanControlRequest,
    repository: Annotated[PlanRepository, Depends(get_plan_repository)],
) -> TaskDetail:
    try:
        next_status = repository.control(plan_id, request.intent)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if next_status is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    detail = repository.detail(plan_id)
    assert detail is not None
    return detail


@router.get("/tasks", response_model=list[TaskSummary], tags=["tasks"])
async def list_tasks(
    repository: Annotated[PlanRepository, Depends(get_plan_repository)],
) -> list[TaskSummary]:
    return repository.list()


@router.get(
    "/tasks/{plan_id}",
    response_model=TaskDetail,
    tags=["tasks"],
)
async def get_task(
    plan_id: UUID,
    repository: Annotated[PlanRepository, Depends(get_plan_repository)],
) -> TaskDetail:
    detail = repository.detail(plan_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return detail
