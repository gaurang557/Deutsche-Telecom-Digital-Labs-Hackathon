"""Local speech-to-text via faster-whisper.

The model is a process-wide singleton, loaded once (ideally at startup) rather
than per request. Audio bytes from the browser (WebM/Opus, OGG, WAV, ...) are
decoded by faster-whisper via PyAV, so no separate ffmpeg binary is required.
"""

import io
import math
import re

from faster_whisper import WhisperModel

from app.config import get_settings
from app.voice.schemas import Transcript

_model: WhisperModel | None = None


def collapse_repeated_sentences(text: str) -> str:
    """Remove consecutive duplicate sentences produced at audio boundaries."""
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    collapsed: list[str] = []
    previous = ""
    for sentence in sentences:
        cleaned = sentence.strip()
        normalized = re.sub(r"\W+", " ", cleaned).strip().casefold()
        if normalized and normalized == previous and len(normalized) >= 10:
            continue
        if cleaned:
            collapsed.append(cleaned)
        previous = normalized
    return " ".join(collapsed)


def is_model_loaded() -> bool:
    """Whether the whisper model has been loaded into memory."""
    return _model is not None


def load_model() -> WhisperModel:
    """Load (once) and return the shared whisper model.

    Runs on CPU with int8 compute so behaviour is identical on macOS and Windows.
    On first ever run this downloads the model weights; afterwards it is cached.
    """
    global _model
    if _model is None:
        settings = get_settings()
        _model = WhisperModel(
            settings.whisper_model,
            device="cpu",
            compute_type=settings.whisper_compute_type,
            download_root=settings.whisper_model_dir,
        )
    return _model


def transcribe_audio(audio: bytes) -> Transcript:
    """Transcribe raw audio bytes into text with a derived confidence score.

    Confidence is ``exp(avg_logprob)`` aggregated across segments, weighted by
    segment duration. It is ``None`` when no speech is found — the caller should
    treat that (and low values) as a cue to clarify rather than to plan.
    """
    model = load_model()
    segments, info = model.transcribe(io.BytesIO(audio))
    segments = list(segments)

    text = collapse_repeated_sentences(
        "".join(segment.text for segment in segments).strip()
    )

    confidence: float | None = None
    total_duration = sum(segment.end - segment.start for segment in segments)
    if total_duration > 0:
        weighted = sum(
            math.exp(segment.avg_logprob) * (segment.end - segment.start)
            for segment in segments
        )
        confidence = round(weighted / total_duration, 4)

    duration_ms = int(info.duration * 1000) if info.duration else None
    return Transcript(
        text=text,
        confidence=confidence,
        language=info.language,
        duration_ms=duration_ms,
    )
