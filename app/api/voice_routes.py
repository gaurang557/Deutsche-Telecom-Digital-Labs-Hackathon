"""Voice subsystem endpoints (Dev 1)."""

from fastapi import APIRouter

from app.config import get_settings
from app.voice.schemas import VoiceHealthResponse
from app.voice.stt import is_model_loaded

router = APIRouter(prefix="/voice", tags=["voice"])


@router.get("/health", response_model=VoiceHealthResponse)
async def voice_health() -> VoiceHealthResponse:
    """Report the voice subsystem's readiness, including whether STT is warmed up."""
    settings = get_settings()
    return VoiceHealthResponse(
        status="ok",
        model=settings.whisper_model,
        model_loaded=is_model_loaded(),
    )
