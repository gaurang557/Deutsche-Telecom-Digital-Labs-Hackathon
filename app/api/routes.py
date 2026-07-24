from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import get_settings
from app.planning.exceptions import (
    InvalidPlannerResponseError,
    PlannerUnavailableError,
)
from app.planning.planner import OllamaPlanner, Planner
from app.planning.service import PlanningService
from app.schemas import HealthResponse, PlanningResponse, TaskRequest

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check() -> HealthResponse:
    """Return the API's readiness status."""
    return HealthResponse(status="ok")


@lru_cache
def get_planner() -> Planner:
    return OllamaPlanner(get_settings())


def get_planning_service(
    planner: Annotated[Planner, Depends(get_planner)],
) -> PlanningService:
    return PlanningService(planner)


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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
