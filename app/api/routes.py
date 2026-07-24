from functools import lru_cache
from typing import Annotated
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
    ExecutePlanRequest,
    HealthResponse,
    PlanExecutionResponse,
    PlanningResponse,
    TaskRequest,
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
def get_desktop_executor() -> DesktopExecutor:
    return DesktopExecutor()


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
    executor: Annotated[DesktopExecutor, Depends(get_desktop_executor)],
) -> PlanExecutionResponse:
    """Execute a stored plan after explicit approval from the local user."""
    plan = repository.get(plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found or the API process was restarted",
        )
    if not repository.claim_execution(plan_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This plan has already been submitted for execution",
        )
    return await executor.execute_plan(plan, request.approved_action_ids)
