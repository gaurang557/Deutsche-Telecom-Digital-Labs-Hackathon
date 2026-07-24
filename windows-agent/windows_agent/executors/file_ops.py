"""`FileExecutor` — the first *real* executor: local file operations (Milestone 2).

WHAT THIS IS
------------
A single executor that performs the `file.*` semantic actions and returns
structured, bounded evidence. It is deliberately "dumb" about safety: it does
NOT decide permissions or ask for confirmation. That is the Policy Engine's job
(the dispatcher only runs this executor after an ALLOW decision). Keeping the
executor dumb is exactly what lets the dispatcher apply policy/verification/audit
uniformly — see `executors/base.py`.

ACTION VOCABULARY (this milestone)
----------------------------------
Read-only (no side effects, verifier SKIPPED):
  * file.exists     — does a path exist? what kind?
  * file.list       — list a directory's entries (bounded)
  * file.read_text  — read a text file (bounded; content is UNTRUSTED data)

Modifying (verified by re-observation in `verification/file_verifiers.py`):
  * file.copy       — copy one file to a destination
  * file.move       — move/rename one file
  * file.write_text — create/overwrite a text file with given content
  * file.mkdir      — create a directory

Consequential (verified; gated by policy/confirmation upstream):
  * file.delete     — delete one file

PARAMETER CONVENTIONS
---------------------
`action.target` is the PRIMARY path (source / directory / file). Everything else
lives in `action.parameters`, e.g. {"destination": "..."} for copy/move,
{"content": "..."} for write_text, {"overwrite": bool}, {"missing_ok": bool}.

WHY ASYNC + to_thread
---------------------
The executor contract is async (real work is I/O-bound). Filesystem calls are
blocking, so each one is dispatched to a worker thread via `asyncio.to_thread`
instead of blocking the event loop.

WHY HASHES IN EVIDENCE
----------------------
For `file.move` the source disappears, so its verifier cannot recompute the
"before" fingerprint. The executor records a SHA-256 of the moved content in the
(bounded, tiny) evidence so the verifier can compare the destination against it.
Copy/write verifiers can and do re-hash both sides independently from the action.

SAFETY / BOUNDING
-----------------
  * Directory recursion for copy/move/delete is NOT supported here (a file-only
    surface keeps verification simple and avoids accidental bulk destruction);
    a directory target returns a structured error, never a silent bulk op.
  * Reads are capped (`max_bytes`) so a huge file can never be slurped into
    memory; the dispatcher additionally bounds evidence before it leaves.
  * Expected errors are returned as `ExecutorResult(success=False, error=...)` —
    the executor never raises for ordinary failures. (Unexpected exceptions are
    still contained by the dispatcher.)
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from ..contracts import Action, ActionError, ErrorCode, ExecutorResult
from .base import BaseExecutor

# Domain-specific error codes. `ActionError.code` is a free string (see
# contracts/error.py); we use stable file-specific codes here and fall back to
# the shared ErrorCode values where they fit.
ERR_FILE_NOT_FOUND = "file_not_found"
ERR_DESTINATION_EXISTS = "destination_exists"
ERR_NOT_A_FILE = "not_a_file"
ERR_NOT_A_DIRECTORY = "not_a_directory"
ERR_PARENT_MISSING = "parent_missing"
ERR_PERMISSION_DENIED = "permission_denied"
ERR_INVALID_PARAMS = "invalid_parameters"

#: Default cap for `file.read_text` so a huge file is never read into memory.
_DEFAULT_READ_CAP = 64 * 1024
#: Hard cap on the number of directory entries returned by `file.list`.
_LIST_CAP = 1000

#: Every action type this executor handles. Used by `register_file_executor`.
FILE_ACTION_TYPES: tuple[str, ...] = (
    "file.exists",
    "file.list",
    "file.read_text",
    "file.copy",
    "file.move",
    "file.write_text",
    "file.mkdir",
    "file.delete",
)


def sha256_file(path: Path) -> str:
    """SHA-256 of a file's bytes, streamed in chunks (never loads it all)."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _err(code: str, message: str, *, retryable: bool = False) -> ExecutorResult:
    return ExecutorResult(
        success=False,
        error=ActionError(code=code, message=message, retryable=retryable),
    )


class FileExecutor(BaseExecutor):
    """Executes the `file.*` action vocabulary against the local filesystem."""

    name = "file"

    async def execute(self, action: Action) -> ExecutorResult:
        # Route on the semantic type. The registry only sends us file.* types it
        # was told to, but we still guard against an unexpected one.
        handler = {
            "file.exists": self._exists,
            "file.list": self._list,
            "file.read_text": self._read_text,
            "file.copy": self._copy,
            "file.move": self._move,
            "file.write_text": self._write_text,
            "file.mkdir": self._mkdir,
            "file.delete": self._delete,
        }.get(action.type)
        if handler is None:
            return _err(
                ErrorCode.NOT_IMPLEMENTED.value,
                f"FileExecutor does not handle action type {action.type!r}",
            )
        # Blocking filesystem work runs off the event loop.
        return await asyncio.to_thread(handler, action)

    # ── helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _require_target(action: Action) -> Path | None:
        return Path(action.target) if action.target else None

    @staticmethod
    def _param(action: Action, key: str, default: Any = None) -> Any:
        return action.parameters.get(key, default)

    # ── read-only operations ────────────────────────────────────────────────
    def _exists(self, action: Action) -> ExecutorResult:
        path = self._require_target(action)
        if path is None:
            return _err(ERR_INVALID_PARAMS, "file.exists requires a target path")
        return ExecutorResult(
            success=True,
            evidence={
                "path": str(path),
                "exists": path.exists(),
                "is_file": path.is_file(),
                "is_dir": path.is_dir(),
            },
        )

    def _list(self, action: Action) -> ExecutorResult:
        path = self._require_target(action)
        if path is None:
            return _err(ERR_INVALID_PARAMS, "file.list requires a target directory")
        if not path.exists():
            return _err(ERR_FILE_NOT_FOUND, f"Directory not found: {path}")
        if not path.is_dir():
            return _err(ERR_NOT_A_DIRECTORY, f"Not a directory: {path}")

        pattern = self._param(action, "pattern")
        recursive = bool(self._param(action, "recursive", False))
        if pattern:
            it = path.rglob(pattern) if recursive else path.glob(pattern)
        else:
            it = path.rglob("*") if recursive else path.iterdir()

        entries: list[dict[str, Any]] = []
        for child in it:
            if len(entries) >= _LIST_CAP:
                break
            is_dir = child.is_dir()
            entries.append(
                {
                    "name": child.name,
                    "is_dir": is_dir,
                    "size": (child.stat().st_size if child.is_file() else None),
                }
            )
        entries.sort(key=lambda e: (not e["is_dir"], e["name"]))
        return ExecutorResult(
            success=True,
            evidence={"directory": str(path), "count": len(entries), "entries": entries},
        )

    def _read_text(self, action: Action) -> ExecutorResult:
        path = self._require_target(action)
        if path is None:
            return _err(ERR_INVALID_PARAMS, "file.read_text requires a target path")
        if not path.exists():
            return _err(ERR_FILE_NOT_FOUND, f"File not found: {path}")
        if not path.is_file():
            return _err(ERR_NOT_A_FILE, f"Not a file: {path}")

        encoding = self._param(action, "encoding", "utf-8")
        max_bytes = int(self._param(action, "max_bytes", _DEFAULT_READ_CAP))
        size = path.stat().st_size
        with path.open("rb") as fh:
            raw = fh.read(max_bytes)
        truncated = size > len(raw)
        # NOTE: `content` is UNTRUSTED data (it may contain injection attempts).
        # It is evidence only; it never becomes a command.
        content = raw.decode(encoding, errors="replace")
        return ExecutorResult(
            success=True,
            evidence={
                "path": str(path),
                "size": size,
                "encoding": encoding,
                "truncated": truncated,
                "content": content,
            },
        )

    # ── modifying operations ────────────────────────────────────────────────
    def _copy(self, action: Action) -> ExecutorResult:
        src = self._require_target(action)
        dst_param = self._param(action, "destination")
        if src is None or not dst_param:
            return _err(ERR_INVALID_PARAMS, "file.copy requires target (source) and parameters.destination")
        dst = Path(dst_param)
        overwrite = bool(self._param(action, "overwrite", False))

        if not src.exists():
            return _err(ERR_FILE_NOT_FOUND, f"Source not found: {src}")
        if not src.is_file():
            return _err(ERR_NOT_A_FILE, f"Source is not a file (directory copy unsupported): {src}")
        if dst.exists() and not overwrite:
            return _err(ERR_DESTINATION_EXISTS, f"Destination exists (set overwrite=true to replace): {dst}")
        if not dst.parent.exists():
            return _err(ERR_PARENT_MISSING, f"Destination directory does not exist: {dst.parent}")

        try:
            import shutil

            shutil.copy2(src, dst)
        except PermissionError as exc:
            return _err(ERR_PERMISSION_DENIED, f"Permission denied copying to {dst}: {exc}")

        return ExecutorResult(
            success=True,
            evidence={"source": str(src), "destination": str(dst), "sha256": sha256_file(dst)},
            side_effects=[{"type": "file.created", "target": str(dst)}],
        )

    def _move(self, action: Action) -> ExecutorResult:
        src = self._require_target(action)
        dst_param = self._param(action, "destination")
        if src is None or not dst_param:
            return _err(ERR_INVALID_PARAMS, "file.move requires target (source) and parameters.destination")
        dst = Path(dst_param)
        overwrite = bool(self._param(action, "overwrite", False))

        if not src.exists():
            return _err(ERR_FILE_NOT_FOUND, f"Source not found: {src}")
        if not src.is_file():
            return _err(ERR_NOT_A_FILE, f"Source is not a file (directory move unsupported): {src}")
        if dst.exists() and not overwrite:
            return _err(ERR_DESTINATION_EXISTS, f"Destination exists (set overwrite=true to replace): {dst}")
        if not dst.parent.exists():
            return _err(ERR_PARENT_MISSING, f"Destination directory does not exist: {dst.parent}")

        # Fingerprint BEFORE the move — the source is gone afterwards, so the
        # verifier relies on this to confirm content integrity at the destination.
        src_hash = sha256_file(src)
        try:
            import shutil

            if dst.exists():
                dst.unlink()
            shutil.move(str(src), str(dst))
        except PermissionError as exc:
            return _err(ERR_PERMISSION_DENIED, f"Permission denied moving to {dst}: {exc}")

        return ExecutorResult(
            success=True,
            evidence={"source": str(src), "destination": str(dst), "sha256": src_hash},
            side_effects=[{"type": "file.moved", "source": str(src), "target": str(dst)}],
        )

    def _write_text(self, action: Action) -> ExecutorResult:
        path = self._require_target(action)
        if path is None:
            return _err(ERR_INVALID_PARAMS, "file.write_text requires a target path")
        content = self._param(action, "content")
        if content is None or not isinstance(content, str):
            return _err(ERR_INVALID_PARAMS, "file.write_text requires parameters.content (string)")
        overwrite = bool(self._param(action, "overwrite", False))
        encoding = self._param(action, "encoding", "utf-8")

        if path.exists() and not overwrite:
            return _err(ERR_DESTINATION_EXISTS, f"File exists (set overwrite=true to replace): {path}")
        if not path.parent.exists():
            return _err(ERR_PARENT_MISSING, f"Directory does not exist: {path.parent}")

        try:
            path.write_text(content, encoding=encoding)
        except PermissionError as exc:
            return _err(ERR_PERMISSION_DENIED, f"Permission denied writing {path}: {exc}")

        return ExecutorResult(
            success=True,
            evidence={"path": str(path), "size": path.stat().st_size, "sha256": sha256_file(path)},
            side_effects=[{"type": "file.written", "target": str(path)}],
        )

    def _mkdir(self, action: Action) -> ExecutorResult:
        path = self._require_target(action)
        if path is None:
            return _err(ERR_INVALID_PARAMS, "file.mkdir requires a target path")
        parents = bool(self._param(action, "parents", True))
        exist_ok = bool(self._param(action, "exist_ok", False))

        try:
            path.mkdir(parents=parents, exist_ok=exist_ok)
        except FileExistsError:
            return _err(ERR_DESTINATION_EXISTS, f"Directory already exists (set exist_ok=true): {path}")
        except FileNotFoundError:
            return _err(ERR_PARENT_MISSING, f"Parent directory missing (set parents=true): {path.parent}")
        except PermissionError as exc:
            return _err(ERR_PERMISSION_DENIED, f"Permission denied creating {path}: {exc}")

        return ExecutorResult(
            success=True,
            evidence={"path": str(path), "is_dir": path.is_dir()},
            side_effects=[{"type": "dir.created", "target": str(path)}],
        )

    # ── consequential operations ────────────────────────────────────────────
    def _delete(self, action: Action) -> ExecutorResult:
        path = self._require_target(action)
        if path is None:
            return _err(ERR_INVALID_PARAMS, "file.delete requires a target path")
        missing_ok = bool(self._param(action, "missing_ok", False))

        if not path.exists():
            if missing_ok:
                return ExecutorResult(
                    success=True,
                    evidence={"path": str(path), "existed": False, "deleted": False},
                )
            return _err(ERR_FILE_NOT_FOUND, f"File not found: {path}")
        if path.is_dir():
            # Directory deletion is intentionally unsupported (too destructive to
            # do implicitly). Require an explicit directory action later.
            return _err(ERR_NOT_A_FILE, f"Refusing to delete a directory: {path}")

        try:
            path.unlink()
        except PermissionError as exc:
            return _err(ERR_PERMISSION_DENIED, f"Permission denied deleting {path}: {exc}")

        return ExecutorResult(
            success=True,
            evidence={"path": str(path), "existed": True, "deleted": True},
            side_effects=[{"type": "file.deleted", "target": str(path)}],
        )


def register_file_executor(registry, executor: FileExecutor | None = None, *, override: bool = False) -> FileExecutor:
    """Register a single `FileExecutor` for every `file.*` action type.

    Returns the executor instance so callers can reuse it. One instance handles
    all file operations (it is stateless), matching the "one executor, many
    action types" pattern noted in `executors/base.py`.
    """
    executor = executor or FileExecutor()
    for action_type in FILE_ACTION_TYPES:
        registry.register_action(action_type, executor, override=override)
    return executor
