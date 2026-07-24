"""Local speech-to-text via faster-whisper.

Chunk 1 provides only load-state tracking; ``load_model`` and ``transcribe_audio``
are implemented in the STT chunk. The model is a process-wide singleton so it is
loaded once (at startup) rather than per request.
"""

_model: object | None = None


def is_model_loaded() -> bool:
    """Whether the whisper model has been loaded into memory."""
    return _model is not None
