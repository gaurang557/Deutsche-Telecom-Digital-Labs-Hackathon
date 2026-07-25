"""Voice-component request/response schemas (Dev 1)."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class ControlIntent(StrEnum):
    """Control words detected before normal planning.

    These short-circuit the pipeline: only ``CORRECT`` reaches the planner (as a
    correction against the same request_id); ``PAUSE``/``RESUME``/``CANCEL`` act on
    task state directly.
    """

    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    CORRECT = "correct"


class Transcript(BaseModel):
    """Raw result of local speech-to-text, before it becomes a TaskRequest."""

    text: str
    confidence: float | None = None
    language: str | None = None
    duration_ms: int | None = None


class VoiceHealthResponse(BaseModel):
    """Readiness of the voice subsystem; ``model_loaded`` drives a UI warming state."""

    status: Literal["ok"]
    model: str
    model_loaded: bool
    environment: Literal["local", "demo"] = "local"
