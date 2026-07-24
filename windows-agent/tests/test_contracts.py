"""Milestone 0 — schema validation tests."""

import pytest
from pydantic import ValidationError

from windows_agent.contracts import (
    Action,
    ActionError,
    ActionResult,
    ActionStatus,
    ExecutorResult,
    VerificationResult,
    VerificationStatus,
)


def _valid_action_kwargs():
    return dict(
        action_id="a1",
        task_id="t1",
        sequence=0,
        type="file.copy",
        target="C:/tmp/src.txt",
        parameters={"dst": "C:/tmp/dst.txt"},
        expected_result={"exists": "C:/tmp/dst.txt"},
        reason="user asked to copy the file",
    )


def test_action_valid_and_json_roundtrip():
    action = Action(**_valid_action_kwargs())
    restored = Action.model_validate_json(action.model_dump_json())
    assert restored == action


@pytest.mark.parametrize("missing", ["action_id", "task_id", "sequence", "type", "reason"])
def test_action_missing_required_field_raises(missing):
    kwargs = _valid_action_kwargs()
    kwargs.pop(missing)
    with pytest.raises(ValidationError):
        Action(**kwargs)


@pytest.mark.parametrize(
    "forbidden_field",
    ["risk", "permission", "trust", "confirmation", "authorization", "risk_level"],
)
def test_action_rejects_authority_fields(forbidden_field):
    """The planner must not be able to smuggle in authorization state."""
    kwargs = _valid_action_kwargs()
    kwargs[forbidden_field] = "high"
    with pytest.raises(ValidationError):
        Action(**kwargs)


def test_action_defaults():
    action = Action(action_id="a", task_id="t", sequence=1, type="file.list", reason="r")
    assert action.target is None
    assert action.parameters == {}
    assert action.expected_result is None


def test_result_models_roundtrip():
    err = ActionError(code="executor_error", message="nope", retryable=True, details={"x": 1})
    execr = ExecutorResult(success=False, evidence={"k": "v"}, side_effects=[{"type": "none"}], error=err)
    assert ExecutorResult.model_validate_json(execr.model_dump_json()) == execr

    ver = VerificationResult(status=VerificationStatus.SKIPPED, method="none")
    ar = ActionResult(action_id="a", task_id="t", status=ActionStatus.SUCCESS, evidence={}, verification=ver)
    assert ActionResult.model_validate_json(ar.model_dump_json()) == ar


def test_enum_rejects_invalid_value():
    with pytest.raises(ValidationError):
        VerificationResult(status="banana", method="x")
