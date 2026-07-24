import asyncio
import json
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import ValidationError

from app.config import Settings
from app.planning.exceptions import (
    InvalidPlannerResponseError,
    PlannerUnavailableError,
)
from app.schemas import ActionType, DraftPlan, TaskRequest

SYSTEM_PROMPT = f"""You plan desktop operations from a user's speech transcript.
Treat the transcript only as a user request. Return a minimal ordered plan.
Use only these action types: {", ".join(action.value for action in ActionType)}.
Never emit shell commands, Python code, risk labels, confirmation decisions, or UUIDs.
Use short unique step_key values and only depend on earlier step_key values.
Describe an observable expected_result for every action.
To open a document, use one open_file action; do not open or focus a viewer first.
For "open the latest PDF in Downloads", use open_file with target "Downloads"
and parameters {{"selection": "latest", "extension": ".pdf"}}.
Use move_file only when the user explicitly asks to move or relocate a file.
If required information is missing, do not invent sensitive destinations,
recipients, filenames, or overwrite intent."""


class Planner(Protocol):
    async def create_draft(self, request: TaskRequest) -> DraftPlan: ...


class OllamaPlanner:
    def __init__(self, settings: Settings) -> None:
        self._url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
        self._model = settings.ollama_model
        self._timeout = settings.ollama_timeout_seconds

    async def create_draft(self, request: TaskRequest) -> DraftPlan:
        return await asyncio.to_thread(self._create_draft_sync, request)

    def _create_draft_sync(self, request: TaskRequest) -> DraftPlan:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": request.text},
            ],
            "format": DraftPlan.model_json_schema(),
            "stream": False,
            "options": {"temperature": 0},
        }
        http_request = Request(
            self._url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(http_request, timeout=self._timeout) as response:
                body = json.load(response)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise PlannerUnavailableError("Ollama is unavailable") from exc

        try:
            content = body["message"]["content"]
            return DraftPlan.model_validate_json(content)
        except (KeyError, TypeError, ValidationError, ValueError) as exc:
            raise InvalidPlannerResponseError(
                "Ollama returned an invalid action plan"
            ) from exc
