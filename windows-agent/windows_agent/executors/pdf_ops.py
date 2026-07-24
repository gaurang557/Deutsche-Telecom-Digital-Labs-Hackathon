"""`PdfExecutor` — the read-only PDF executor (Milestone 3).

WHAT THIS IS
------------
A single executor that performs the `pdf.*` semantic actions and returns
structured, bounded evidence. Like `FileExecutor`, it is deliberately "dumb"
about safety: it does NOT decide permissions or ask for confirmation. That is
the Policy Engine's job (the dispatcher only runs this executor after an ALLOW
decision). See `executors/base.py`.

ACTION VOCABULARY (this milestone)
----------------------------------
Every `pdf.*` action here is READ-ONLY (no side effects). All are therefore
`RiskLevel.NONE` and need no verifier — verification only exists for *modifying*
actions, so the VerificationRegistry correctly returns SKIPPED for these types:
  * pdf.page_count   — how many pages does the document have?
  * pdf.get_metadata — the document's metadata (title/author/…)
  * pdf.read_text    — extract text from a page or an inclusive page range
  * pdf.search       — per-page hit counts for a query string

PARAMETER CONVENTIONS
---------------------
`action.target` is the PRIMARY path (the PDF file). Everything else lives in
`action.parameters`, e.g. {"page": 0} or {"start_page": 0, "end_page": 3} for
read_text, {"query": "...", "max_results": 20} for search. Page indices are
**0-based** and validated against the real page count (fail closed on range).

WHY PyMuPDF (`fitz`)
--------------------
A structured PDF API is far more reliable than scraping a viewer's GUI (see
ARCHITECTURE §3 executor-preference order). PyMuPDF is fast, pure read here, and
gives us page counts, metadata, per-page text, and `search_for` hit rectangles
directly.

WHY ASYNC + to_thread
---------------------
The executor contract is async, but PyMuPDF's calls are blocking (they parse
bytes on disk). Each operation is therefore dispatched to a worker thread via
`asyncio.to_thread` instead of blocking the event loop — exactly as file_ops
does for filesystem I/O.

SAFETY / BOUNDING
-----------------
  * Extracted text is capped (`_DEFAULT_TEXT_CHAR_CAP`, override with
    `max_chars`) so a huge PDF can never be slurped into memory / evidence; the
    dispatcher additionally bounds evidence before it leaves.
  * Search matches are capped (`_DEFAULT_SEARCH_RESULTS`, override with
    `max_results`) so a query that hits every page cannot produce unbounded
    evidence.
  * Encrypted / password-needed PDFs fail closed with a clear error — we never
    prompt for a password.
  * Expected errors are returned as `ExecutorResult(success=False, error=...)` —
    the executor never raises for ordinary failures. (Unexpected exceptions are
    still contained by the dispatcher.) The fitz document is always closed via
    try/finally.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from ..contracts import Action, ActionError, ErrorCode, ExecutorResult
from .base import BaseExecutor

# Domain-specific error codes. `ActionError.code` is a free string (see
# contracts/error.py); we use stable pdf-specific codes here and fall back to
# the shared ErrorCode values where they fit.
ERR_FILE_NOT_FOUND = "file_not_found"
ERR_NOT_A_PDF = "not_a_pdf"
ERR_ENCRYPTED = "encrypted_pdf"
ERR_PAGE_OUT_OF_RANGE = "page_out_of_range"
ERR_INVALID_PARAMS = "invalid_parameters"

#: Default cap on extracted text length so a huge PDF is never read into
#: evidence wholesale. Overridable per call via `max_chars`.
_DEFAULT_TEXT_CHAR_CAP = 20_000
#: Default/limit on the number of per-page match entries `pdf.search` returns.
_DEFAULT_SEARCH_RESULTS = 100

#: Every action type this executor handles. Used by `register_pdf_executor`.
PDF_ACTION_TYPES: tuple[str, ...] = (
    "pdf.page_count",
    "pdf.get_metadata",
    "pdf.read_text",
    "pdf.search",
)

#: Metadata keys we surface (PyMuPDF's `doc.metadata` dict keys), plus the
#: derived `page_count`. Missing/empty values are reported as null.
_METADATA_FIELDS = ("title", "author", "subject", "keywords", "creator", "producer")


def _err(code: str, message: str, *, retryable: bool = False) -> ExecutorResult:
    return ExecutorResult(
        success=False,
        error=ActionError(code=code, message=message, retryable=retryable),
    )


class PdfExecutor(BaseExecutor):
    """Executes the read-only `pdf.*` action vocabulary via PyMuPDF."""

    name = "pdf"

    async def execute(self, action: Action) -> ExecutorResult:
        # Route on the semantic type. The registry only sends us pdf.* types it
        # was told to, but we still guard against an unexpected one.
        handler = {
            "pdf.page_count": self._page_count,
            "pdf.get_metadata": self._get_metadata,
            "pdf.read_text": self._read_text,
            "pdf.search": self._search,
        }.get(action.type)
        if handler is None:
            return _err(
                ErrorCode.NOT_IMPLEMENTED.value,
                f"PdfExecutor does not handle action type {action.type!r}",
            )
        # Blocking PyMuPDF parsing runs off the event loop.
        return await asyncio.to_thread(handler, action)

    # ── helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _require_target(action: Action) -> Path | None:
        return Path(action.target) if action.target else None

    @staticmethod
    def _param(action: Action, key: str, default: Any = None) -> Any:
        return action.parameters.get(key, default)

    def _open_pdf(self, path: Path | None) -> tuple[Any, ExecutorResult | None]:
        """Open + guard a PDF, returning (doc, None) or (None, error_result).

        Fails closed on a missing path, a non-file target, anything PyMuPDF
        cannot parse as a PDF, and password-protected documents (we never
        prompt). The caller owns closing the returned document.
        """
        if path is None:
            return None, _err(ERR_INVALID_PARAMS, "pdf action requires a target path")
        if not path.exists():
            return None, _err(ERR_FILE_NOT_FOUND, f"File not found: {path}")
        if not path.is_file():
            return None, _err(ERR_NOT_A_PDF, f"Not a file: {path}")
        try:
            doc = fitz.open(str(path))
        except Exception as exc:  # PyMuPDF raises various FileData/format errors
            return None, _err(ERR_NOT_A_PDF, f"Cannot open as PDF: {path}: {exc}")
        # Password-needed documents fail closed — do NOT prompt for a password.
        if doc.needs_pass:
            doc.close()
            return None, _err(ERR_ENCRYPTED, f"PDF is password-protected: {path}")
        return doc, None

    @staticmethod
    def _as_index(value: Any) -> int | None:
        """Coerce a page parameter to a non-negative int, or None if invalid."""
        if isinstance(value, bool):  # bool is an int subclass; reject it explicitly
            return None
        if not isinstance(value, int):
            return None
        return value if value >= 0 else None

    # ── read-only operations ────────────────────────────────────────────────
    def _page_count(self, action: Action) -> ExecutorResult:
        path = self._require_target(action)
        doc, err = self._open_pdf(path)
        if err is not None:
            return err
        try:
            return ExecutorResult(
                success=True,
                evidence={"path": str(path), "page_count": doc.page_count},
            )
        finally:
            doc.close()

    def _get_metadata(self, action: Action) -> ExecutorResult:
        path = self._require_target(action)
        doc, err = self._open_pdf(path)
        if err is not None:
            return err
        try:
            raw = doc.metadata or {}
            # Empty strings from PyMuPDF are reported as null so the planner sees
            # a clear "missing" rather than a confusing empty value.
            metadata = {key: (raw.get(key) or None) for key in _METADATA_FIELDS}
            metadata["page_count"] = doc.page_count
            return ExecutorResult(
                success=True,
                evidence={"path": str(path), "metadata": metadata},
            )
        finally:
            doc.close()

    def _read_text(self, action: Action) -> ExecutorResult:
        path = self._require_target(action)
        doc, err = self._open_pdf(path)
        if err is not None:
            return err
        try:
            page_count = doc.page_count
            span = self._resolve_page_span(action, page_count)
            if isinstance(span, ExecutorResult):  # validation error
                return span
            start, end = span

            max_chars = int(self._param(action, "max_chars", _DEFAULT_TEXT_CHAR_CAP))
            parts: list[str] = []
            total = 0
            pages_read = 0
            truncated = False
            for index in range(start, end + 1):
                pages_read += 1
                text = doc.load_page(index).get_text()
                if total + len(text) > max_chars:
                    parts.append(text[: max_chars - total])
                    truncated = True
                    break
                parts.append(text)
                total += len(text)
            return ExecutorResult(
                success=True,
                evidence={
                    "path": str(path),
                    "text": "".join(parts),
                    "pages_read": pages_read,
                    "truncated": truncated,
                },
            )
        finally:
            doc.close()

    def _search(self, action: Action) -> ExecutorResult:
        path = self._require_target(action)
        query = self._param(action, "query")
        if not isinstance(query, str) or not query.strip():
            # Open validity is irrelevant if the query is unusable; fail fast.
            doc, err = self._open_pdf(path)
            if err is not None:
                return err
            doc.close()
            return _err(ERR_INVALID_PARAMS, "pdf.search requires a non-empty parameters.query")

        doc, err = self._open_pdf(path)
        if err is not None:
            return err
        try:
            max_results = int(self._param(action, "max_results", _DEFAULT_SEARCH_RESULTS))
            if max_results <= 0:
                max_results = _DEFAULT_SEARCH_RESULTS

            matches: list[dict[str, int]] = []
            total_matches = 0
            truncated = False
            for index in range(doc.page_count):
                hits = doc.load_page(index).search_for(query)
                if not hits:
                    continue
                if len(matches) >= max_results:
                    # More pages match than we will report — mark bounded.
                    truncated = True
                    break
                matches.append({"page": index, "count": len(hits)})
                total_matches += len(hits)
            return ExecutorResult(
                success=True,
                evidence={
                    "path": str(path),
                    "matches": matches,
                    "total_matches": total_matches,
                    "truncated": truncated,
                },
            )
        finally:
            doc.close()

    # ── read_text page-span resolution ──────────────────────────────────────
    def _resolve_page_span(
        self, action: Action, page_count: int
    ) -> tuple[int, int] | ExecutorResult:
        """Resolve read_text's page selection to an inclusive [start, end].

        `page` (single) wins if given; otherwise `[start_page, end_page]`
        defaulting to the whole document. Out-of-range / malformed indices fail
        closed with a structured error.
        """
        single = self._param(action, "page")
        if single is not None:
            index = self._as_index(single)
            if index is None:
                return _err(ERR_INVALID_PARAMS, f"page must be a non-negative integer, got {single!r}")
            if index >= page_count:
                return _err(
                    ERR_PAGE_OUT_OF_RANGE,
                    f"page {index} out of range (document has {page_count} page(s))",
                )
            return index, index

        start_raw = self._param(action, "start_page", 0)
        end_raw = self._param(action, "end_page", page_count - 1)
        start = self._as_index(start_raw)
        end = self._as_index(end_raw)
        if start is None or end is None:
            return _err(
                ERR_INVALID_PARAMS,
                f"start_page/end_page must be non-negative integers, got {start_raw!r}/{end_raw!r}",
            )
        if start >= page_count or end >= page_count:
            return _err(
                ERR_PAGE_OUT_OF_RANGE,
                f"page range [{start}, {end}] out of range (document has {page_count} page(s))",
            )
        if start > end:
            return _err(ERR_INVALID_PARAMS, f"start_page {start} is after end_page {end}")
        return start, end


def register_pdf_executor(registry, executor: PdfExecutor | None = None, *, override: bool = False) -> PdfExecutor:
    """Register a single `PdfExecutor` for every `pdf.*` action type.

    Returns the executor instance so callers can reuse it. One instance handles
    all PDF operations (it is stateless), matching the "one executor, many
    action types" pattern noted in `executors/base.py`.
    """
    executor = executor or PdfExecutor()
    for action_type in PDF_ACTION_TYPES:
        registry.register_action(action_type, executor, override=override)
    return executor
