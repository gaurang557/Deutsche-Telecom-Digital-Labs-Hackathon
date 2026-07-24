"""Provider seam: selection, both backends, and failure mapping.

Every test here fakes the Cursor SDK. Nothing in this file imports the real
`cursor_sdk`, opens a socket, or spends Cursor credits, and the suite collects
and passes with the optional SDK not installed at all.
"""

import importlib.util
import json
import sys
import tempfile
import types
from pathlib import Path

import pytest

import app
from app.config import Settings
from app.planning.exceptions import (
    InvalidPlannerResponseError,
    PlannerUnavailableError,
)
from app.planning.planner import OllamaPlanner
from app.planning.providers import ProviderConfigurationError, get_provider
from app.planning.providers.cursor import CursorProvider
from app.planning.providers.ollama import OllamaProvider

_CURSOR_MODULE = "cursor_sdk"


def _settings(**overrides) -> Settings:
    """Settings isolated from the developer's own .env file."""
    return Settings(_env_file=None, **overrides)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """No test may inherit a real key or a provider choice from the environment."""
    for name in (
        "AGENT_LLM_PROVIDER",
        "AGENT_CURSOR_MODEL",
        "AGENT_OLLAMA_BASE_URL",
        "AGENT_OLLAMA_MODEL",
        "AGENT_OLLAMA_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CURSOR_API_KEY", "test-key-not-a-real-credential")


class _FakeCursorAgentError(Exception):
    """Stands in for a raised CursorAgentError: the run never started."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
        self.is_retryable = False
        self.retry_after = None


class _FakeRunResult:
    def __init__(self, status: str, result: str | None) -> None:
        self.status = status
        self.result = result


class _FakeLocalAgentOptions:
    def __init__(self, *, cwd: str) -> None:
        self.cwd = cwd


class _FakeAgentOptions:
    def __init__(self, *, api_key=None, model=None, local=None, **extra) -> None:
        self.api_key = api_key
        self.model = model
        self.local = local
        self.extra = extra


class _FakeAgent:
    """Records the one-shot prompt call instead of contacting Cursor."""

    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result if result is not None else _FakeRunResult("finished", "{}")
        self.error = error
        self.calls: list[tuple[str, _FakeAgentOptions]] = []

    def prompt(self, prompt, options):
        self.calls.append((prompt, options))
        if self.error is not None:
            raise self.error
        return self.result


def _install_fake_sdk(monkeypatch, agent: _FakeAgent) -> _FakeAgent:
    module = types.ModuleType(_CURSOR_MODULE)
    module.Agent = agent
    module.AgentOptions = _FakeAgentOptions
    module.LocalAgentOptions = _FakeLocalAgentOptions
    module.CursorAgentError = _FakeCursorAgentError
    monkeypatch.setitem(sys.modules, _CURSOR_MODULE, module)
    return agent


def _faked_provider(monkeypatch, agent: _FakeAgent) -> CursorProvider:
    _install_fake_sdk(monkeypatch, agent)
    return CursorProvider(_settings(llm_provider="cursor"))


# --- selection ---------------------------------------------------------------


def test_provider_defaults_to_ollama() -> None:
    settings = _settings()
    assert settings.llm_provider == "ollama"
    provider = get_provider(settings)
    assert isinstance(provider, OllamaProvider)
    assert provider.name == "ollama"


def test_cursor_is_selected_by_configuration(monkeypatch) -> None:
    _install_fake_sdk(monkeypatch, _FakeAgent())
    provider = get_provider(_settings(llm_provider="cursor"))
    assert isinstance(provider, CursorProvider)
    assert provider.name == "cursor"


def test_provider_name_is_case_and_space_insensitive(monkeypatch) -> None:
    _install_fake_sdk(monkeypatch, _FakeAgent())
    assert isinstance(get_provider(_settings(llm_provider=" Cursor ")), CursorProvider)


def test_unknown_provider_fails_clearly() -> None:
    with pytest.raises(ProviderConfigurationError) as raised:
        get_provider(_settings(llm_provider="gpt-9"))
    message = str(raised.value)
    assert "gpt-9" in message
    assert "ollama" in message and "cursor" in message


def test_cursor_without_api_key_fails_clearly(monkeypatch) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    with pytest.raises(ProviderConfigurationError) as raised:
        get_provider(_settings(llm_provider="cursor"))
    assert "CURSOR_API_KEY" in str(raised.value)


def test_cursor_without_the_sdk_says_how_to_install_it(monkeypatch) -> None:
    # A None entry in sys.modules makes `import cursor_sdk` fail the same way an
    # uninstalled package does, whether or not the SDK is present here.
    monkeypatch.setitem(sys.modules, _CURSOR_MODULE, None)
    with pytest.raises(ProviderConfigurationError) as raised:
        get_provider(_settings(llm_provider="cursor"))
    assert "[cursor]" in str(raised.value)


def test_cursor_module_imports_without_the_sdk_installed(monkeypatch) -> None:
    """Test collection must not depend on the optional dependency.

    Executes the provider module body from scratch with `cursor_sdk` unimportable.
    A module-level SDK import would fail here; a lazy one cannot.
    """
    monkeypatch.setitem(sys.modules, _CURSOR_MODULE, None)
    spec = importlib.util.spec_from_file_location(
        "_isolated_cursor_provider", sys.modules[CursorProvider.__module__].__file__
    )
    isolated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated)
    assert isolated.CursorProvider.name == "cursor"


# --- Cursor provider behaviour ----------------------------------------------


def test_cursor_returns_generated_text(monkeypatch) -> None:
    agent = _FakeAgent(_FakeRunResult("finished", '{"summary": "ok", "actions": []}'))
    provider = _faked_provider(monkeypatch, agent)

    text = provider.complete(
        [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}],
        json_schema={"type": "object", "title": "DraftPlan"},
    )

    assert text == '{"summary": "ok", "actions": []}'
    prompt, options = agent.calls[0]
    assert options.model == "composer-2.5"
    assert "be brief" in prompt and "hi" in prompt
    assert "DraftPlan" in prompt
    assert "Do not read, create, modify, or delete any file" in prompt


def test_cursor_runs_in_a_disposable_directory_outside_the_repository(
    monkeypatch,
) -> None:
    agent = _FakeAgent(_FakeRunResult("finished", "{}"))
    provider = _faked_provider(monkeypatch, agent)

    provider.complete([{"role": "user", "content": "hi"}])

    cwd = Path(agent.calls[0][1].local.cwd).resolve()
    repository_root = Path(app.__file__).resolve().parent.parent
    assert repository_root not in cwd.parents and cwd != repository_root
    assert cwd.is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert not cwd.exists()  # removed as soon as the call returns


def test_cursor_prompt_never_carries_the_credential(monkeypatch) -> None:
    agent = _FakeAgent(_FakeRunResult("finished", "{}"))
    provider = _faked_provider(monkeypatch, agent)

    provider.complete([{"role": "user", "content": "hi"}])

    assert "test-key-not-a-real-credential" not in agent.calls[0][0]


def test_cursor_startup_failure_is_unavailable(monkeypatch) -> None:
    """A raised SDK error means the run never executed."""
    agent = _FakeAgent(error=_FakeCursorAgentError("401 unauthorized"))
    provider = _faked_provider(monkeypatch, agent)

    with pytest.raises(PlannerUnavailableError) as raised:
        provider.complete([{"role": "user", "content": "hi"}])
    assert "401" not in str(raised.value)
    assert "Ollama" not in str(raised.value)


def test_cursor_run_failure_is_an_invalid_response(monkeypatch) -> None:
    """A returned error status means the run executed and failed."""
    provider = _faked_provider(monkeypatch, _FakeAgent(_FakeRunResult("error", None)))

    with pytest.raises(InvalidPlannerResponseError):
        provider.complete([{"role": "user", "content": "hi"}])


def test_cursor_empty_reply_is_an_invalid_response(monkeypatch) -> None:
    provider = _faked_provider(monkeypatch, _FakeAgent(_FakeRunResult("finished", "  ")))

    with pytest.raises(InvalidPlannerResponseError):
        provider.complete([{"role": "user", "content": "hi"}])


def test_cursor_failure_does_not_fall_back_to_ollama(monkeypatch) -> None:
    """A Cursor failure must stay visible rather than silently switching backend."""
    agent = _FakeAgent(error=_FakeCursorAgentError("network down"))
    planner = OllamaPlanner(_settings(llm_provider="cursor"))
    _install_fake_sdk(monkeypatch, agent)

    with pytest.raises(PlannerUnavailableError):
        planner._chat([{"role": "user", "content": "hi"}])
    assert len(agent.calls) == 1


# --- Ollama provider behaviour ----------------------------------------------


def test_ollama_sends_the_schema_as_a_decoding_constraint(monkeypatch) -> None:
    captured: dict = {}

    class _Response:
        def read(self):
            return json.dumps({"message": {"content": "{}"}}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    def _fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data)
        return _Response()

    monkeypatch.setattr("app.planning.providers.ollama.urlopen", _fake_urlopen)
    provider = OllamaProvider(_settings())
    schema = {"type": "object", "properties": {"actions": {"minItems": 2}}}

    assert provider.complete([{"role": "user", "content": "hi"}], json_schema=schema) == "{}"
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["timeout"] == 60.0
    assert captured["payload"]["format"] == schema
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["options"] == {"temperature": 0}
    assert captured["payload"]["model"] == "llama3.2"


def test_ollama_transport_failure_is_unavailable(monkeypatch) -> None:
    def _fake_urlopen(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr("app.planning.providers.ollama.urlopen", _fake_urlopen)

    with pytest.raises(PlannerUnavailableError):
        OllamaProvider(_settings()).complete([{"role": "user", "content": "hi"}])


def test_ollama_incomplete_body_is_an_invalid_response(monkeypatch) -> None:
    class _Response:
        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(
        "app.planning.providers.ollama.urlopen",
        lambda request, timeout=None: _Response(),
    )

    with pytest.raises(InvalidPlannerResponseError):
        OllamaProvider(_settings()).complete([{"role": "user", "content": "hi"}])


# --- the planner's compatibility shim ---------------------------------------


class _RecordingProvider:
    name = "recording"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete(self, messages, *, json_schema=None, timeout=None) -> str:
        self.calls.append({"messages": messages, "json_schema": json_schema})
        return '{"summary": "ok", "actions": []}'


def test_chat_delegates_to_an_injected_provider() -> None:
    provider = _RecordingProvider()
    planner = OllamaPlanner(_settings(), provider=provider)

    text = planner._chat([{"role": "user", "content": "hi"}], 2)

    assert text == '{"summary": "ok", "actions": []}'
    schema = provider.calls[0]["json_schema"]
    assert schema["properties"]["actions"]["minItems"] == 2


def test_planner_resolves_its_provider_lazily() -> None:
    """Constructing a planner must not require a usable backend."""
    planner = OllamaPlanner(_settings(llm_provider="gpt-9"))
    with pytest.raises(ProviderConfigurationError):
        planner._chat([{"role": "user", "content": "hi"}])
