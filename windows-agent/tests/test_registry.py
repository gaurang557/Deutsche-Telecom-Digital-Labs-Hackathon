"""Milestone 1 — ActionRegistry function coverage."""

import pytest

from windows_agent.execution import ActionRegistry
from windows_agent.executors.common.mock import EchoExecutor


def test_register_and_lookup():
    reg = ActionRegistry()
    handler = EchoExecutor()
    reg.register_action("file.copy", handler)
    assert reg.get_action_handler("file.copy") is handler
    assert reg.has_action("file.copy")
    assert reg.list_registered_actions() == ["file.copy"]


def test_unknown_lookup_returns_none():
    reg = ActionRegistry()
    assert reg.get_action_handler("does.not.exist") is None
    assert reg.has_action("does.not.exist") is False


def test_duplicate_registration_is_deterministic():
    reg = ActionRegistry()
    reg.register_action("file.copy", EchoExecutor())
    with pytest.raises(ValueError):
        reg.register_action("file.copy", EchoExecutor())
    # explicit override is allowed
    reg.register_action("file.copy", EchoExecutor(), override=True)


def test_empty_type_rejected():
    with pytest.raises(ValueError):
        ActionRegistry().register_action("", EchoExecutor())


def test_unregister():
    reg = ActionRegistry()
    reg.register_action("file.copy", EchoExecutor())
    assert reg.unregister_action("file.copy") is True
    assert reg.unregister_action("file.copy") is False  # already gone
    assert reg.has_action("file.copy") is False


def test_list_is_sorted():
    reg = ActionRegistry()
    reg.register_action("file.move", EchoExecutor())
    reg.register_action("file.copy", EchoExecutor())
    assert reg.list_registered_actions() == ["file.copy", "file.move"]


def test_m0_aliases_still_work():
    reg = ActionRegistry()
    handler = EchoExecutor()
    reg.register("file.copy", handler)
    assert reg.get("file.copy") is handler
    assert "file.copy" in reg
    assert reg.types() == ["file.copy"]
