import re

from app.schemas import ControlIntent

_CONTROL_PATTERNS: tuple[tuple[ControlIntent, re.Pattern[str]], ...] = (
    (
        ControlIntent.PAUSE,
        re.compile(r"^\s*(pause|hold on|wait)\s*[.!]?\s*$", re.IGNORECASE),
    ),
    (
        ControlIntent.RESUME,
        re.compile(r"^\s*(resume|continue|carry on)\s*[.!]?\s*$", re.IGNORECASE),
    ),
    (
        ControlIntent.CANCEL,
        re.compile(r"^\s*(cancel|stop|abort)\s*[.!]?\s*$", re.IGNORECASE),
    ),
    (
        ControlIntent.CORRECT,
        re.compile(
            r"^\s*(correct|correction|change that|instead)\b",
            re.IGNORECASE,
        ),
    ),
)


def detect_control_intent(text: str) -> ControlIntent | None:
    """Detect task-control commands before invoking the LLM."""
    for intent, pattern in _CONTROL_PATTERNS:
        if pattern.search(text):
            return intent
    return None
