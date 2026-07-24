import json
import os
import shutil
import tempfile
from types import ModuleType

from app.config import Settings
from app.planning.exceptions import (
    InvalidPlannerResponseError,
    PlannerUnavailableError,
)
from app.planning.providers.base import ProviderConfigurationError

#: The Cursor SDK drives a coding agent, not a completions endpoint, so the
#: prompt has to say plainly that no action is wanted. Combined with an empty
#: throwaway cwd and no MCP servers, there is nothing for it to act on.
_BACKEND_INSTRUCTION = """You are being used purely as a text-generation backend.
Do not read, create, modify, or delete any file. Do not run any command, search
the web, or take any action other than replying with the requested text. The
working directory is an empty scratch folder and holds nothing relevant to the
request. Reply with the requested JSON object and nothing else: no commentary,
no explanation, and no markdown code fence."""


def _import_sdk() -> ModuleType:
    """Import `cursor_sdk` on demand.

    Kept out of module scope so importing this module — and therefore collecting
    the test suite — never requires the optional SDK to be installed.
    """
    try:
        import cursor_sdk
    except ImportError as exc:
        raise ProviderConfigurationError(
            "AGENT_LLM_PROVIDER=cursor needs the Cursor SDK, which is an optional "
            'dependency. Install it with: pip install -e ".[cursor]"'
        ) from exc
    return cursor_sdk


def _build_prompt(messages: list[dict[str, str]], json_schema: dict | None) -> str:
    """Flatten a chat exchange into the single prompt the agent SDK accepts."""
    parts = [_BACKEND_INSTRUCTION]
    if json_schema is not None:
        parts.append(
            "Your reply must be one JSON object that validates against this JSON "
            f"Schema:\n{json.dumps(json_schema)}"
        )
    parts.extend(
        f"[{message.get('role', 'user')}]\n{message.get('content', '')}"
        for message in messages
    )
    parts.append("Reply now with the JSON object only.")
    return "\n\n".join(parts)


class CursorProvider:
    """Text generation through the official Cursor agent SDK.

    Cursor has no constrained decoding, so the schema can only be asked for in
    the prompt; the planner's existing extraction, validation, and repair loop
    stays the authority on whether the reply is usable.
    """

    name = "cursor"

    def __init__(self, settings: Settings) -> None:
        self._model = settings.cursor_model
        self._sdk = _import_sdk()

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        json_schema: dict | None = None,
        timeout: float | None = None,
    ) -> str:
        sdk = self._sdk
        prompt = _build_prompt(messages, json_schema)
        # A fresh empty directory per call: never the repository, so repository
        # files are not exposed to the agent even if it ignores the instruction.
        workdir = tempfile.mkdtemp(prefix="voicedesk-plan-")
        try:
            try:
                run = sdk.Agent.prompt(
                    prompt,
                    sdk.AgentOptions(
                        api_key=os.environ.get("CURSOR_API_KEY"),
                        model=self._model,
                        local=sdk.LocalAgentOptions(cwd=workdir),
                    ),
                )
            except sdk.CursorAgentError as exc:
                # Raised means the run never started: auth, config, or network.
                # The SDK message is not echoed, so a credential cannot leak out.
                raise PlannerUnavailableError(
                    "The configured model service is unavailable"
                ) from exc

            if getattr(run, "status", None) == "error":
                # Returned means the run executed and failed on its own terms.
                raise InvalidPlannerResponseError(
                    "The configured model could not produce a valid action plan."
                )

            text = getattr(run, "result", None)
            if not isinstance(text, str):
                reader = getattr(run, "text", None)
                text = reader() if callable(reader) else None
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        if not isinstance(text, str) or not text.strip():
            raise InvalidPlannerResponseError(
                "The configured model returned an empty response"
            )
        return text
