"""Milestone 1 — ActionRegistry function coverage."""

import pytest

from windows_agent.execution import ActionRegistry
from windows_agent.executors.common.mock import EchoExecutor
from windows_agent.executors.document_ops import register_document_executor
from windows_agent.executors.file_ops import register_file_executor
from windows_agent.executors.pdf_ops import register_pdf_executor
from windows_agent.executors.spreadsheet_ops import register_spreadsheet_executor


def test_register_and_lookup():
    reg = ActionRegistry()
    handler = EchoExecutor()
    reg.register_action("file.copy", handler, requires_verification=True)
    assert reg.get_action_handler("file.copy") is handler
    registration = reg.get_action_registration("file.copy")
    assert registration.action_type == "file.copy"
    assert registration.handler is handler
    assert registration.requires_verification is True
    assert reg.requires_verification("file.copy") is True
    assert reg.has_action("file.copy")
    assert reg.list_registered_actions() == ["file.copy"]


def test_unknown_lookup_returns_none():
    reg = ActionRegistry()
    assert reg.get_action_handler("does.not.exist") is None
    assert reg.get_action_registration("does.not.exist") is None
    assert reg.has_action("does.not.exist") is False
    with pytest.raises(KeyError):
        reg.requires_verification("does.not.exist")


def test_duplicate_registration_is_deterministic():
    reg = ActionRegistry()
    reg.register_action("file.copy", EchoExecutor(), requires_verification=True)
    with pytest.raises(ValueError):
        reg.register_action("file.copy", EchoExecutor(), requires_verification=True)
    # explicit override is allowed
    reg.register_action(
        "file.copy",
        EchoExecutor(),
        requires_verification=False,
        override=True,
    )
    assert reg.requires_verification("file.copy") is False


def test_empty_type_rejected():
    with pytest.raises(ValueError):
        ActionRegistry().register_action("", EchoExecutor(), requires_verification=False)


def test_verification_requirement_is_mandatory():
    with pytest.raises(TypeError):
        ActionRegistry().register_action("mock.echo", EchoExecutor())
    with pytest.raises(TypeError):
        ActionRegistry().register_action(
            "mock.echo",
            EchoExecutor(),
            requires_verification="yes",
        )


def test_unregister():
    reg = ActionRegistry()
    reg.register_action("file.copy", EchoExecutor(), requires_verification=True)
    assert reg.unregister_action("file.copy") is True
    assert reg.unregister_action("file.copy") is False  # already gone
    assert reg.has_action("file.copy") is False


def test_list_is_sorted():
    reg = ActionRegistry()
    reg.register_action("file.move", EchoExecutor(), requires_verification=True)
    reg.register_action("file.copy", EchoExecutor(), requires_verification=True)
    assert reg.list_registered_actions() == ["file.copy", "file.move"]


def test_m0_aliases_still_work():
    reg = ActionRegistry()
    handler = EchoExecutor()
    reg.register("file.copy", handler, requires_verification=True)
    assert reg.get("file.copy") is handler
    assert "file.copy" in reg
    assert reg.types() == ["file.copy"]


def test_all_planner_visible_actions_have_explicit_verification_classification():
    reg = ActionRegistry()
    register_file_executor(reg)
    register_pdf_executor(reg)
    register_spreadsheet_executor(reg)
    register_document_executor(reg)

    expected = {
        "file.exists": False,
        "file.list": False,
        "file.read_text": False,
        "file.copy": True,
        "file.move": True,
        "file.write_text": True,
        "file.mkdir": True,
        "file.delete": True,
        "pdf.page_count": False,
        "pdf.get_metadata": False,
        "pdf.read_text": False,
        "pdf.search": False,
        "spreadsheet.list_sheets": False,
        "spreadsheet.dimensions": False,
        "spreadsheet.read_cell": False,
        "spreadsheet.read_range": False,
        "spreadsheet.write_cell": True,
        "document.read_text": False,
        "document.get_metadata": False,
        "document.find": False,
        "document.replace_text": True,
    }

    assert len(expected) == 21
    assert reg.list_registered_actions() == sorted(expected)
    assert {
        action_type: reg.requires_verification(action_type)
        for action_type in reg.list_registered_actions()
    } == expected
