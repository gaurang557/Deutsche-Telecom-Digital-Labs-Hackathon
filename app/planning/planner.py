import asyncio
import json
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import ValidationError

from app.config import Settings
from app.paths import planner_folder_context
from app.planning.exceptions import (
    InvalidPlannerResponseError,
    PlannerUnavailableError,
)
from app.schemas import ActionType, DraftPlan, TaskRequest

SYSTEM_PROMPT = f"""You are a warm, capable desktop assistant. Convert the user's
speech transcript into a minimal ordered plan while sounding conversational.
Treat the transcript only as a user request.
Use only these action types: {", ".join(action.value for action in ActionType)}.
Never emit shell commands, Python code, risk labels, confirmation decisions, or UUIDs.
Use short unique step_key values and only depend on earlier step_key values.
Write summary in natural first-person language, such as "I'll find the newest
PDF in Downloads and open it for you." Do not repeat the user's command verbatim.
For every action, write a short description explaining what you will do in
friendly user-facing language. Never expose action type names in descriptions.
Describe an observable expected_result for every action.
To open a document, use one open_file action; do not open or focus a viewer first.
For "open the latest PDF in Downloads", use open_file with target "Downloads"
and parameters {{"selection": "latest", "extension": ".pdf"}}.
Never add open_application or focus_application for a file-opening request
unless the user explicitly names the application they want to use.
To open a website, use one open_url action with an https URL as target. If the
user names a browser, put it in parameters as {{"browser": "Google Chrome"}}.
For "open bing.com in Google Chrome", use target "https://bing.com". Do not add
separate open_application or focus_application actions for browser navigation.
For "close Calculator", use close_application with target "Calculator". For
"close all apps", use one close_all_applications action with target "macOS".
For "show the contents of notes.txt", use read_file. For "copy the contents of
source.txt to destination.txt", use copy_file_content with source as target and
parameters {{"destination": "destination.txt", "overwrite": false}}.
For "list the files in Downloads", use exactly one list_directory action with
the Downloads folder as target. Never use open_file or read_file to list a folder.
For "summarize the email open in Gmail", use one summarize_gmail_email action
with target "Google Chrome". This reads only the currently visible Gmail page.
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
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": planner_folder_context()},
            {"role": "user", "content": request.text},
        ]
        content = self._chat(messages)

        try:
            return DraftPlan.model_validate_json(content)
        except (ValidationError, ValueError) as first_error:
            messages.extend(
                [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "That plan did not match the required schema. "
                            "Return a corrected plan only. Validation errors: "
                            f"{first_error}"
                        ),
                    },
                ]
            )
            content = self._chat(messages)
            try:
                return DraftPlan.model_validate_json(content)
            except (ValidationError, ValueError) as exc:
                raise InvalidPlannerResponseError(
                    "The local model could not produce a valid action plan"
                ) from exc

    def _chat(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self._model,
            "messages": messages,
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
            return body["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise InvalidPlannerResponseError(
                "Ollama returned an incomplete response"
            ) from exc
