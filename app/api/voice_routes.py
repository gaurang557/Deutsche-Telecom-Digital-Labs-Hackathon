"""Voice subsystem endpoints (Dev 1)."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import get_settings
from app.schemas import TaskRequest
from app.voice.schemas import VoiceHealthResponse
from app.voice.stt import is_model_loaded, transcribe_audio

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


@router.post("/transcribe", response_model=TaskRequest)
async def transcribe(file: Annotated[UploadFile, File()]) -> TaskRequest:
    """Transcribe an uploaded audio clip into a TaskRequest.

    A fresh ``request_id`` is minted here, at the microphone boundary, and threads
    the entire downstream task; ``source`` is always ``speech`` for mic input.
    """
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty audio upload.")

    transcript = transcribe_audio(audio)
    return TaskRequest(
        request_id=str(uuid4()),
        text=transcript.text,
        source="speech",
        confidence=transcript.confidence,
        received_at=datetime.now(UTC),
    )
