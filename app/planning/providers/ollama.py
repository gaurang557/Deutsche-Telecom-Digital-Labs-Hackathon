import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import Settings
from app.planning.exceptions import (
    InvalidPlannerResponseError,
    PlannerUnavailableError,
)


class OllamaProvider:
    """The local Ollama `/api/chat` transport, moved out of the planner verbatim.

    `format` is Ollama's constrained-decoding hook: passing the plan's JSON
    schema is what keeps a 3B model's output syntactically valid, so it is sent
    whenever the caller supplies a schema rather than being downgraded to a
    prompt-level request.
    """

    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        self._url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
        self._model = settings.ollama_model
        self._timeout = settings.ollama_timeout_seconds

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        json_schema: dict | None = None,
        timeout: float | None = None,
    ) -> str:
        payload = {
            "model": self._model,
            "messages": messages,
            "format": json_schema,
            "stream": False,
            "options": {"temperature": 0},
        }
        if json_schema is None:
            del payload["format"]
        http_request = Request(
            self._url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(
                http_request, timeout=self._timeout if timeout is None else timeout
            ) as response:
                body = json.load(response)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise PlannerUnavailableError("Ollama is unavailable") from exc

        try:
            return body["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise InvalidPlannerResponseError(
                "Ollama returned an incomplete response"
            ) from exc
