"""Milestone 1 — VerificationRegistry coverage."""

import pytest

from windows_agent.contracts import Action, ExecutorResult, VerificationResult, VerificationStatus
from windows_agent.verification import VerificationRegistry, Verifier


def _action(type_: str = "file.copy") -> Action:
    return Action(action_id="a", task_id="t", sequence=0, type=type_, reason="r")


class PassVerifier(Verifier):
    async def verify(self, action, result, context=None):
        return VerificationResult(status=VerificationStatus.PASSED, method="test", message="ok")


class FailVerifier(Verifier):
    async def verify(self, action, result, context=None):
        return VerificationResult(status=VerificationStatus.FAILED, method="test", message="mismatch")


def test_register_get_has():
    reg = VerificationRegistry()
    verifier = PassVerifier()
    reg.register_verifier("file.copy", verifier)
    assert reg.get_verifier("file.copy") is verifier
    assert reg.has_verifier("file.copy") is True
    assert reg.get_verifier("file.move") is None
    assert reg.has_verifier("file.move") is False


def test_duplicate_verifier_rejected():
    reg = VerificationRegistry()
    reg.register_verifier("file.copy", PassVerifier())
    with pytest.raises(ValueError):
        reg.register_verifier("file.copy", PassVerifier())


async def test_verify_no_verifier_is_skipped():
    reg = VerificationRegistry()
    result = await reg.verify_action(_action(), ExecutorResult(success=True))
    assert result.status == VerificationStatus.SKIPPED


async def test_verify_passes():
    reg = VerificationRegistry()
    reg.register_verifier("file.copy", PassVerifier())
    result = await reg.verify_action(_action(), ExecutorResult(success=True))
    assert result.status == VerificationStatus.PASSED


async def test_verify_fails():
    reg = VerificationRegistry()
    reg.register_verifier("file.copy", FailVerifier())
    result = await reg.verify_action(_action(), ExecutorResult(success=True))
    assert result.status == VerificationStatus.FAILED
