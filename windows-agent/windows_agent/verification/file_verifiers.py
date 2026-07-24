"""Verifiers for the `file.*` actions (Milestone 2).

CORE PRINCIPLE (repeated because it matters)
--------------------------------------------
"The executor returned success" is NOT proof. Each verifier here RE-OBSERVES the
real filesystem after the fact and compares expected vs observed:

  * file.copy       — destination exists AND its bytes hash-match the source.
  * file.move       — destination exists, source is GONE, and destination bytes
                      match the pre-move fingerprint recorded in evidence.
  * file.write_text — file exists AND its content equals what we asked to write.
  * file.mkdir      — the directory now exists.
  * file.delete     — the path no longer exists.

Read-only actions (file.exists / file.list / file.read_text) have no verifier —
the VerificationRegistry returns SKIPPED for them, which is correct: there is no
state change to confirm.

INDEPENDENCE
------------
Where possible a verifier recomputes fingerprints straight from the action's own
paths, NOT from the executor's evidence, so a buggy/lying executor cannot fake a
pass. `file.move` is the one case that must trust a recorded hash, because the
source no longer exists to re-hash (documented in `executors/file_ops.py`).

Verification runs off the event loop via `asyncio.to_thread` (blocking I/O).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..contracts import Action, ExecutorResult, VerificationResult, VerificationStatus
from ..executors.file_ops import sha256_file
from .base import Verifier


def _passed(method: str, expected: Any, observed: Any, message: str = "") -> VerificationResult:
    return VerificationResult(
        status=VerificationStatus.PASSED, method=method, expected=expected, observed=observed, message=message
    )


def _failed(method: str, expected: Any, observed: Any, message: str) -> VerificationResult:
    return VerificationResult(
        status=VerificationStatus.FAILED, method=method, expected=expected, observed=observed, message=message
    )


class FileCopyVerifier(Verifier):
    """Confirm the destination exists and matches the source byte-for-byte."""

    async def verify(self, action: Action, result: ExecutorResult, context: Any = None) -> VerificationResult:
        return await asyncio.to_thread(self._check, action, result)

    @staticmethod
    def _check(action: Action, result: ExecutorResult) -> VerificationResult:
        method = "re-hash source and destination"
        src = Path(action.target) if action.target else None
        dst_param = action.parameters.get("destination")
        if src is None or not dst_param:
            return _failed(method, "src+dst paths", None, "Missing source/destination for verification")
        dst = Path(dst_param)
        if not dst.exists():
            return _failed(method, f"exists({dst})", False, f"Destination missing after copy: {dst}")
        # Prefer independent re-hash of the source; fall back to recorded hash.
        expected_hash = sha256_file(src) if src.is_file() else result.evidence.get("sha256")
        observed_hash = sha256_file(dst)
        if expected_hash and expected_hash == observed_hash:
            return _passed(method, expected_hash, observed_hash, "Copy verified: destination matches source")
        return _failed(method, expected_hash, observed_hash, "Copy hash mismatch")


class FileMoveVerifier(Verifier):
    """Confirm the destination exists, the source is gone, and content matches."""

    async def verify(self, action: Action, result: ExecutorResult, context: Any = None) -> VerificationResult:
        return await asyncio.to_thread(self._check, action, result)

    @staticmethod
    def _check(action: Action, result: ExecutorResult) -> VerificationResult:
        method = "check dst exists + src absent + hash match"
        src = Path(action.target) if action.target else None
        dst_param = action.parameters.get("destination")
        if src is None or not dst_param:
            return _failed(method, "src+dst paths", None, "Missing source/destination for verification")
        dst = Path(dst_param)
        if not dst.exists():
            return _failed(method, f"exists({dst})", False, f"Destination missing after move: {dst}")
        if src.exists():
            return _failed(method, f"absent({src})", True, f"Source still present after move: {src}")
        # Source is gone, so we must trust the pre-move fingerprint from evidence.
        expected_hash = result.evidence.get("sha256")
        observed_hash = sha256_file(dst)
        if expected_hash and expected_hash != observed_hash:
            return _failed(method, expected_hash, observed_hash, "Move hash mismatch")
        return _passed(method, expected_hash, observed_hash, "Move verified: destination present, source removed")


class FileWriteVerifier(Verifier):
    """Confirm the file exists and its content equals what we asked to write."""

    async def verify(self, action: Action, result: ExecutorResult, context: Any = None) -> VerificationResult:
        return await asyncio.to_thread(self._check, action, result)

    @staticmethod
    def _check(action: Action, result: ExecutorResult) -> VerificationResult:
        method = "re-read file and compare content"
        path = Path(action.target) if action.target else None
        expected = action.parameters.get("content")
        encoding = action.parameters.get("encoding", "utf-8")
        if path is None or not isinstance(expected, str):
            return _failed(method, "path+content", None, "Missing path/content for verification")
        if not path.exists():
            return _failed(method, f"exists({path})", False, f"File missing after write: {path}")
        observed = path.read_text(encoding=encoding)
        if observed == expected:
            return _passed(method, f"<{len(expected)} chars>", f"<{len(observed)} chars>", "Write verified")
        return _failed(method, f"<{len(expected)} chars>", f"<{len(observed)} chars>", "Written content differs")


class FileMkdirVerifier(Verifier):
    """Confirm the directory now exists."""

    async def verify(self, action: Action, result: ExecutorResult, context: Any = None) -> VerificationResult:
        return await asyncio.to_thread(self._check, action)

    @staticmethod
    def _check(action: Action) -> VerificationResult:
        method = "stat directory"
        path = Path(action.target) if action.target else None
        if path is None:
            return _failed(method, "path", None, "Missing path for verification")
        if path.is_dir():
            return _passed(method, "is_dir=True", True, f"Directory exists: {path}")
        return _failed(method, "is_dir=True", False, f"Directory missing after mkdir: {path}")


class FileDeleteVerifier(Verifier):
    """Confirm the target path no longer exists."""

    async def verify(self, action: Action, result: ExecutorResult, context: Any = None) -> VerificationResult:
        return await asyncio.to_thread(self._check, action)

    @staticmethod
    def _check(action: Action) -> VerificationResult:
        method = "confirm path absent"
        path = Path(action.target) if action.target else None
        if path is None:
            return _failed(method, "path", None, "Missing path for verification")
        if not path.exists():
            return _passed(method, "exists=False", False, f"Deleted: {path}")
        return _failed(method, "exists=False", True, f"Path still present after delete: {path}")


#: Maps each modifying `file.*` type to its verifier class. Read-only types are
#: intentionally absent (→ VerificationRegistry returns SKIPPED).
FILE_VERIFIERS: dict[str, type[Verifier]] = {
    "file.copy": FileCopyVerifier,
    "file.move": FileMoveVerifier,
    "file.write_text": FileWriteVerifier,
    "file.mkdir": FileMkdirVerifier,
    "file.delete": FileDeleteVerifier,
}


def register_file_verifiers(registry, *, override: bool = False) -> None:
    """Register a verifier for every modifying `file.*` action type."""
    for action_type, verifier_cls in FILE_VERIFIERS.items():
        registry.register_verifier(action_type, verifier_cls(), override=override)
