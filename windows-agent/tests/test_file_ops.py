"""Milestone 2 — file.* executor + verifiers.

Two layers of tests:
  1. The FileExecutor in isolation (each operation's success + failure paths).
  2. The full pipeline via the Dispatcher (AllowAllPolicy + file verifiers),
     proving execution AND independent verification agree end-to-end.

`tmp_path` (pytest builtin) gives each test its own throwaway directory, so the
real filesystem operations are exercised safely.
"""

from pathlib import Path

from windows_agent.contracts import Action, ActionStatus, ExecutorResult, VerificationStatus
from windows_agent.execution import ActionRegistry, Dispatcher
from windows_agent.executors.file_ops import FileExecutor, register_file_executor
from windows_agent.policy import AllowAllPolicy
from windows_agent.verification import (
    FileCopyVerifier,
    FileDeleteVerifier,
    FileMkdirVerifier,
    FileMoveVerifier,
    FileWriteVerifier,
    VerificationRegistry,
    register_file_verifiers,
)


def _action(type_: str, target=None, parameters=None) -> Action:
    return Action(
        action_id="a1",
        task_id="t1",
        sequence=0,
        type=type_,
        target=str(target) if target is not None else None,
        parameters=parameters or {},
        reason="test",
    )


# ── FileExecutor unit tests ────────────────────────────────────────────────
async def test_write_text_creates_file(tmp_path):
    ex = FileExecutor()
    dst = tmp_path / "note.txt"
    res = await ex.execute(_action("file.write_text", dst, {"content": "hello"}))
    assert res.success is True
    assert dst.read_text() == "hello"
    assert res.side_effects[0]["type"] == "file.written"


async def test_write_text_no_overwrite(tmp_path):
    ex = FileExecutor()
    dst = tmp_path / "note.txt"
    dst.write_text("original")
    res = await ex.execute(_action("file.write_text", dst, {"content": "new"}))
    assert res.success is False
    assert res.error.code == "destination_exists"
    assert dst.read_text() == "original"


async def test_copy_success_and_evidence(tmp_path):
    ex = FileExecutor()
    src = tmp_path / "src.txt"
    src.write_text("data")
    dst = tmp_path / "dst.txt"
    res = await ex.execute(_action("file.copy", src, {"destination": dst}))
    assert res.success is True
    assert dst.read_text() == "data"
    assert src.exists()  # copy leaves the source in place
    assert "sha256" in res.evidence


async def test_copy_missing_source(tmp_path):
    ex = FileExecutor()
    res = await ex.execute(
        _action("file.copy", tmp_path / "nope.txt", {"destination": tmp_path / "d.txt"})
    )
    assert res.success is False
    assert res.error.code == "file_not_found"


async def test_move_removes_source(tmp_path):
    ex = FileExecutor()
    src = tmp_path / "src.txt"
    src.write_text("data")
    dst = tmp_path / "moved.txt"
    res = await ex.execute(_action("file.move", src, {"destination": dst}))
    assert res.success is True
    assert dst.read_text() == "data"
    assert not src.exists()


async def test_mkdir_and_exist_ok(tmp_path):
    ex = FileExecutor()
    d = tmp_path / "sub" / "deep"
    res = await ex.execute(_action("file.mkdir", d, {"parents": True}))
    assert res.success is True and d.is_dir()
    # Second mkdir without exist_ok should fail.
    res2 = await ex.execute(_action("file.mkdir", d, {"parents": True}))
    assert res2.success is False
    assert res2.error.code == "destination_exists"


async def test_delete_file(tmp_path):
    ex = FileExecutor()
    f = tmp_path / "gone.txt"
    f.write_text("x")
    res = await ex.execute(_action("file.delete", f))
    assert res.success is True and not f.exists()


async def test_delete_missing_not_ok(tmp_path):
    ex = FileExecutor()
    res = await ex.execute(_action("file.delete", tmp_path / "ghost.txt"))
    assert res.success is False
    assert res.error.code == "file_not_found"


async def test_delete_refuses_directory(tmp_path):
    ex = FileExecutor()
    d = tmp_path / "adir"
    d.mkdir()
    res = await ex.execute(_action("file.delete", d))
    assert res.success is False
    assert res.error.code == "not_a_file"


async def test_read_text_bounded(tmp_path):
    ex = FileExecutor()
    f = tmp_path / "big.txt"
    f.write_text("A" * 1000)
    res = await ex.execute(_action("file.read_text", f, {"max_bytes": 100}))
    assert res.success is True
    assert res.evidence["truncated"] is True
    assert len(res.evidence["content"]) == 100


async def test_list_directory(tmp_path):
    ex = FileExecutor()
    (tmp_path / "a.txt").write_text("1")
    (tmp_path / "b.txt").write_text("2")
    (tmp_path / "sub").mkdir()
    res = await ex.execute(_action("file.list", tmp_path))
    assert res.success is True
    assert res.evidence["count"] == 3
    names = {e["name"] for e in res.evidence["entries"]}
    assert {"a.txt", "b.txt", "sub"} == names


async def test_exists(tmp_path):
    ex = FileExecutor()
    f = tmp_path / "here.txt"
    f.write_text("x")
    res = await ex.execute(_action("file.exists", f))
    assert res.evidence["exists"] is True and res.evidence["is_file"] is True


# ── Verifier unit tests ────────────────────────────────────────────────────
async def test_copy_verifier_passes(tmp_path):
    src = tmp_path / "s.txt"
    src.write_text("payload")
    dst = tmp_path / "d.txt"
    dst.write_text("payload")
    vr = await FileCopyVerifier().verify(
        _action("file.copy", src, {"destination": dst}), ExecutorResult(success=True)
    )
    assert vr.status == VerificationStatus.PASSED


async def test_copy_verifier_fails_on_mismatch(tmp_path):
    src = tmp_path / "s.txt"
    src.write_text("payload")
    dst = tmp_path / "d.txt"
    dst.write_text("TAMPERED")
    vr = await FileCopyVerifier().verify(
        _action("file.copy", src, {"destination": dst}), ExecutorResult(success=True)
    )
    assert vr.status == VerificationStatus.FAILED


async def test_move_verifier_detects_lingering_source(tmp_path):
    src = tmp_path / "s.txt"
    src.write_text("payload")
    dst = tmp_path / "d.txt"
    dst.write_text("payload")
    # Source still present → move must be considered NOT verified.
    vr = await FileMoveVerifier().verify(
        _action("file.move", src, {"destination": dst}),
        ExecutorResult(success=True, evidence={"sha256": "irrelevant"}),
    )
    assert vr.status == VerificationStatus.FAILED


async def test_delete_verifier_passes(tmp_path):
    f = tmp_path / "gone.txt"  # never created → absent
    vr = await FileDeleteVerifier().verify(_action("file.delete", f), ExecutorResult(success=True))
    assert vr.status == VerificationStatus.PASSED


# ── End-to-end via the Dispatcher (policy + execution + verification) ───────
def _pipeline():
    reg = ActionRegistry()
    register_file_executor(reg)
    vreg = VerificationRegistry()
    register_file_verifiers(vreg)
    return Dispatcher(reg, AllowAllPolicy(), verification=vreg)


async def test_pipeline_copy_success_and_verified(tmp_path):
    src = tmp_path / "s.txt"
    src.write_text("hello")
    dst = tmp_path / "copy.txt"
    disp = _pipeline()
    result = await disp.dispatch(_action("file.copy", src, {"destination": dst}))
    assert result.status == ActionStatus.SUCCESS
    assert result.verification.status == VerificationStatus.PASSED
    assert dst.read_text() == "hello"


async def test_pipeline_move_success_and_verified(tmp_path):
    src = tmp_path / "s.txt"
    src.write_text("hello")
    dst = tmp_path / "moved.txt"
    disp = _pipeline()
    result = await disp.dispatch(_action("file.move", src, {"destination": dst}))
    assert result.status == ActionStatus.SUCCESS
    assert result.verification.status == VerificationStatus.PASSED
    assert not src.exists()


async def test_pipeline_read_text_skips_verification(tmp_path):
    f = tmp_path / "r.txt"
    f.write_text("content")
    disp = _pipeline()
    result = await disp.dispatch(_action("file.read_text", f))
    assert result.status == ActionStatus.SUCCESS
    # Read-only → no verifier registered → SKIPPED.
    assert result.verification.status == VerificationStatus.SKIPPED


async def test_pipeline_copy_failure_is_failed(tmp_path):
    disp = _pipeline()
    result = await disp.dispatch(
        _action("file.copy", tmp_path / "missing.txt", {"destination": tmp_path / "d.txt"})
    )
    assert result.status == ActionStatus.FAILED
    assert result.error.code == "file_not_found"


async def test_register_file_executor_covers_all_types():
    reg = ActionRegistry()
    register_file_executor(reg)
    for t in ("file.copy", "file.move", "file.delete", "file.mkdir", "file.write_text",
              "file.read_text", "file.list", "file.exists"):
        assert reg.has_action(t)
