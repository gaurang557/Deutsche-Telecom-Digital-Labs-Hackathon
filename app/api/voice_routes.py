"""Voice subsystem endpoints (Dev 1)."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import get_settings
from app.schemas import TaskRequest
from app.voice.schemas import VoiceHealthResponse

router = APIRouter(prefix="/voice", tags=["voice"])


@router.get("/health", response_model=VoiceHealthResponse)
async def voice_health() -> VoiceHealthResponse:
    """Report the voice subsystem's readiness, including whether STT is warmed up."""
    settings = get_settings()
    if settings.demo_mode:
        return VoiceHealthResponse(
            status="ok",
            model="Browser speech",
            model_loaded=True,
            environment="demo",
        )
    from app.voice.stt import is_model_loaded

    return VoiceHealthResponse(
        status="ok",
        model=settings.whisper_model,
        model_loaded=is_model_loaded(),
        environment="local",
    )


@router.post("/transcribe", response_model=TaskRequest)
async def transcribe(file: Annotated[UploadFile, File()]) -> TaskRequest:
    """Transcribe an uploaded audio clip into a TaskRequest.

    A fresh ``request_id`` is minted here, at the microphone boundary, and threads
    the entire downstream task; ``source`` is always ``speech`` for mic input.
    """
    if get_settings().demo_mode:
        raise HTTPException(
            status_code=409,
            detail=(
                "Use live browser speech recognition in the public demo, "
                "or type your request."
            ),
        )
    from av.error import FFmpegError

    from app.voice.stt import transcribe_audio

    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty audio upload.")

    try:
        transcript = transcribe_audio(audio)
    except FFmpegError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "The audio recording was incomplete or unreadable. "
                "Please hold the microphone button, speak, and release it again."
            ),
        ) from exc
    if not transcript.text:
        raise HTTPException(
            status_code=422,
            detail="No speech was detected. Please record the request again.",
        )
    return TaskRequest(
        request_id=str(uuid4()),
        text=transcript.text,
        source="speech",
        confidence=transcript.confidence,
        received_at=datetime.now(UTC),
    )
