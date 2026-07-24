"""Verifiers for the `document.*` actions (Milestone 6).

CORE PRINCIPLE (repeated because it matters)
--------------------------------------------
"The executor returned success" is NOT proof. The one modifying document
action, `document.replace_text`, gets an independent verifier that RE-OPENS the
output document and RE-SCANS its text, then PASSes iff the correction is
actually visible on disk:

  * document.replace_text → reopen the output document (`output_path` from
    evidence — equal to `save_as` or the original path) and confirm the
    `replace` string is present at least the expected number of times AND (when
    `find != replace`) the original `find` no longer appears.

Read-only actions (read_text / get_metadata / find) have no verifier — the
VerificationRegistry returns SKIPPED for them, which is correct: there is no
state change to confirm.

INDEPENDENCE
------------
The verifier opens the document itself (it does not trust the executor's live
handle) so a buggy/lying executor cannot fake a pass. The output path, expected
replacement count, and the find/replace strings are taken from the
ExecutorResult evidence — that is the executor's stated intent that we hold it
to. Text is gathered from the SAME places the executor edits (body paragraphs +
tables + section headers/footers) via `_all_paragraphs`.

ROBUST, SIMPLE COMPARISON
-------------------------
We compare on plain text occurrence counts (formatting is irrelevant to whether
the correction landed):
  * the `replace` string must occur at least `replacements` times, and
  * when the correction genuinely removes the old text (`find != replace` and
    `find` is not a substring of `replace`), `find` must be gone entirely.
When `replace` contains `find` (e.g. "cat" → "cats"), the find-absence check is
skipped, because `find` is *expected* to still appear inside the replacement.

Verification runs off the event loop via `asyncio.to_thread` (blocking I/O).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from docx import Document

from ..contracts import Action, ExecutorResult, VerificationResult, VerificationStatus
from ..executors.document_ops import _all_paragraphs
from .base import Verifier


def _passed(method: str, expected: Any, observed: Any, message: str = "") -> VerificationResult:
    return VerificationResult(
        status=VerificationStatus.PASSED, method=method, expected=expected, observed=observed, message=message
    )


def _failed(method: str, expected: Any, observed: Any, message: str) -> VerificationResult:
    return VerificationResult(
        status=VerificationStatus.FAILED, method=method, expected=expected, observed=observed, message=message
    )


def _gather_text(doc: Any) -> str:
    """Concatenate the text of every paragraph the executor may have edited."""
    return "\n".join(para.text for para in _all_paragraphs(doc))


class DocumentReplaceTextVerifier(Verifier):
    """Confirm the correction actually landed in the output document on disk."""

    async def verify(self, action: Action, result: ExecutorResult, context: Any = None) -> VerificationResult:
        return await asyncio.to_thread(self._check, action, result)

    @staticmethod
    def _check(action: Action, result: ExecutorResult) -> VerificationResult:
        method = "reopen document and re-scan text"
        evidence = result.evidence or {}
        params = action.parameters or {}

        # `output_path` is where the executor says it wrote (save_as or original).
        path_str = evidence.get("output_path") or evidence.get("path") or action.target
        find = evidence.get("find", params.get("find"))
        replace = evidence.get("replace") if "replace" in evidence else params.get("replace", "")
        expected_count = evidence.get("replacements", 0)

        if not path_str or not isinstance(find, str) or not isinstance(replace, str):
            return _failed(method, "path+find+replace", None, "Missing path/find/replace for verification")
        path = Path(path_str)
        if not path.exists():
            return _failed(method, f"exists({path})", False, f"Document missing after replace: {path}")

        try:
            doc = Document(str(path))
        except Exception as exc:
            return _failed(method, replace, None, f"Cannot re-open document: {exc}")
        full = _gather_text(doc)

        replace_count = full.count(replace) if replace else 0
        find_count = full.count(find)
        # `find` only has to be gone when the replacement genuinely removes it.
        require_find_absent = find != replace and find not in replace

        expected = {
            "replace": replace,
            "min_replace_count": expected_count,
            "find_absent_required": require_find_absent,
        }
        observed = {"replace_count": replace_count, "find_count": find_count}

        replace_ok = bool(replace) and replace_count >= expected_count and expected_count >= 1
        find_ok = (not require_find_absent) or find_count == 0

        if replace_ok and find_ok:
            return _passed(
                method, expected, observed,
                f"Replace verified: {replace!r} present {replace_count}x (>= {expected_count}); "
                f"{find!r} count now {find_count}",
            )
        return _failed(
            method, expected, observed,
            f"Replace not confirmed at {path}: {replace!r} present {replace_count}x "
            f"(expected >= {expected_count}), {find!r} count {find_count} "
            f"(find_absent_required={require_find_absent})",
        )


#: Maps each modifying `document.*` type to its verifier class. Read-only types
#: are intentionally absent (→ VerificationRegistry returns SKIPPED).
DOCUMENT_VERIFIERS: dict[str, type[Verifier]] = {
    "document.replace_text": DocumentReplaceTextVerifier,
}


def register_document_verifiers(registry, *, override: bool = False) -> None:
    """Register a verifier for every modifying `document.*` action type."""
    for action_type, verifier_cls in DOCUMENT_VERIFIERS.items():
        registry.register_verifier(action_type, verifier_cls(), override=override)
