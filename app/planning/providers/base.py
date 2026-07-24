from typing import Protocol

from app.planning.exceptions import PlannerError


class ProviderConfigurationError(PlannerError):
    """Raised when the configured LLM provider cannot be built as asked for.

    Distinct from a model that is merely unreachable: this is an operator
    mistake (unknown provider name, missing SDK, missing credential) and is
    fixed by changing the environment, not by retrying.
    """


class LLMProvider(Protocol):
    """One text completion from whichever model backend is configured.

    Deliberately narrow. Validating the generated text, repairing it, and
    retrying all stay in the planner, so a provider only has to turn messages
    into text. `json_schema` is a request, not a promise: Ollama enforces it
    through constrained decoding, Cursor can only be asked for it in the prompt.
    """

    name: str

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        json_schema: dict | None = None,
        timeout: float | None = None,
    ) -> str: ...
