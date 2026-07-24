import os

from app.config import Settings
from app.planning.providers.base import LLMProvider, ProviderConfigurationError

__all__ = ["LLMProvider", "ProviderConfigurationError", "get_provider"]


def get_provider(settings: Settings) -> LLMProvider:
    """Build the configured provider. The only reader of `settings.llm_provider`.

    Each backend is imported inside its own branch so the optional Cursor SDK is
    never needed to run with the default local model.
    """
    name = settings.llm_provider.strip().lower()

    if name == "ollama":
        from app.planning.providers.ollama import OllamaProvider

        return OllamaProvider(settings)

    if name == "cursor":
        # Read straight from the environment: the credential is deliberately not
        # a Settings field, so it is never loaded from or written to .env.
        if not (os.environ.get("CURSOR_API_KEY") or "").strip():
            raise ProviderConfigurationError(
                "AGENT_LLM_PROVIDER=cursor needs a CURSOR_API_KEY environment "
                "variable. Set it in the shell that starts the server; it is not "
                "read from application configuration."
            )
        from app.planning.providers.cursor import CursorProvider

        return CursorProvider(settings)

    raise ProviderConfigurationError(
        f"Unknown AGENT_LLM_PROVIDER {settings.llm_provider!r}. "
        "Supported values are 'ollama' and 'cursor'."
    )
