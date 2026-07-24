from fastapi import APIRouter

from app.api.voice_routes import router as voice_router
from app.schemas import HealthResponse

router = APIRouter()
router.include_router(voice_router)


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check() -> HealthResponse:
    """Return the API's readiness status."""
    return HealthResponse(status="ok")

