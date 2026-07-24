"""Turn a step's evidence into a bounded, redacted view fit to show a person.

WHY THIS EXISTS
---------------
A run used to report only a status and, for reads, "Nothing to verify: this step
only read, it changed nothing." That is accurate and tells you nothing: the whole
point of the workflow is that a value came OUT of one file and went INTO another,
and none of that chain was visible. This module surfaces the chain from evidence
the executors already return.

WHAT IT MAY NOT DO
------------------
It is a projection, not a capability. It reads `ActionResult.evidence`, and it
never asks an executor for more, never re-reads a file, and never decides
anything about permission, risk, trust, confirmation, or verification.

THREE BOUNDS, ALL LOAD-BEARING
------------------------------
1. Everything is clamped by a named constant below. A full document, workbook or
   directory listing must never reach a response, so every excerpt carries an
   explicit `truncated` flag and no caller can mistake a sample for the whole.
2. Clamp BEFORE redacting, never after. `redact_sensitive_data` replaces any
   string over 2000 characters with a `<TEXT:len=...>` digest, so redacting first
   would throw away the very excerpt we are trying to show. Excerpts are cut to a
   few hundred characters and then redacted, which keeps both properties.
3. Excerpts are marked untrusted. Every one of them is file content, so the UI
   presents them as quoted material rather than as agent output.
"""

from __future__ import annotations

from typing import Any

from agent.redaction import redact_sensitive_data
from app.schemas import (
    ActionResult,
    StepComparison,
    StepDetail,
    StepExcerpt,
    StepFact,
)

#: Longest text sample shown for a document read. A few hundred characters is
#: enough to recognise the content and to see why a pattern did or did not match.
MAX_EXCERPT_CHARS = 600

#: Spreadsheet ranges are clamped twice — by rows AND by total characters — so a
#: wide sheet cannot slip past a generous row limit.
MAX_TABLE_ROWS = 8
MAX_TABLE_CHARS = 600

#: Any single labelled value (a path, a cell value, an expected/observed blob).
MAX_FACT_CHARS = 240

#: Excerpts inside an ERROR message are held to a tighter budget than excerpts in
#: a detail panel, because `_run_structured` clamps a failure message to 500
#: characters before the user ever sees it. Sized so a whole diagnosis — the
#: reference, the pattern and the text — survives that clamp intact rather than
#: being cut off mid-excerpt, which would defeat the point of including it.
MAX_DIAGNOSIS_CHARS = 200

#: The pattern is model-supplied and bounded at 300 characters upstream, which is
#: too long to sit inside a 500-character message next to an excerpt.
MAX_PATTERN_CHARS = 120

_TRUNCATION_MARK = "…"

_READ_TEXT_ACTIONS = {
    "pdf.read_text": ("pages_read", "page"),
    "document.read_text": ("paragraph_count", "paragraph"),
    "presentation.read_text": ("slides_read", "slide"),
}

_REPLACE_ACTIONS = {"document.replace_text", "presentation.replace_text"}


def _clip(text: str, limit: int) -> tuple[str, bool]:
    """Cut `text` to `limit` characters, reporting whether anything was removed."""
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip() + _TRUNCATION_MARK, True


def _redacted(text: str) -> str:
    """The project's existing redaction layer, applied to an already-clipped string.

    Only ever called on clipped text. Redacting first would be worse than useless:
    `redact_sensitive_data` swaps any string over 2000 characters for a
    `<TEXT:len=...>` digest, which would erase the excerpt entirely.
    """
    redacted = redact_sensitive_data(text)
    return redacted if isinstance(redacted, str) else str(redacted)


def _safe(value: Any, limit: int = MAX_FACT_CHARS) -> str:
    """Render any evidence value as a short, redacted string."""
    text = value if isinstance(value, str) else repr(value)
    clipped, _ = _clip(text, limit)
    return _redacted(clipped)


def _fact(label: str, value: Any, *, limit: int = MAX_FACT_CHARS) -> StepFact:
    return StepFact(label=label, value=_safe(value, limit))


def _excerpt(
    label: str,
    raw: str,
    limit: int = MAX_EXCERPT_CHARS,
    *,
    already_truncated: bool = False,
) -> StepExcerpt:
    """A clamped, redacted, untrusted-by-construction sample of file content.

    `already_truncated` carries a cut that happened before this call — rows dropped
    from a range, or the executor itself stopping early — so one flag on the payload
    covers every reason the sample is not the whole thing.
    """
    clipped, truncated = _clip(raw, limit)
    return StepExcerpt(
        label=label,
        body=_redacted(clipped),
        truncated=truncated or already_truncated,
        untrusted=True,
    )


def excerpt_for_diagnosis(raw: Any, limit: int = MAX_DIAGNOSIS_CHARS) -> str:
    """A bounded, redacted sample for an error message.

    Used where a failure is only explicable next to the data that caused it — a
    regex that did not match is meaningless without a look at the text it was
    matched against. Whitespace is collapsed so a PDF's line breaks do not eat the
    budget.
    """
    text = raw if isinstance(raw, str) else repr(raw)
    clipped, truncated = _clip(" ".join(text.split()), limit)
    body = _redacted(clipped)
    return f"{body} [truncated]" if truncated else body


def pattern_for_diagnosis(regex: str) -> str:
    """A model-supplied pattern, shortened to fit beside an excerpt in a message."""
    clipped, truncated = _clip(regex, MAX_PATTERN_CHARS)
    return f"{clipped!r} [truncated]" if truncated else repr(clipped)


def _render_rows(values: Any) -> tuple[str, bool]:
    """Lay a read range out as text, clamped by rows and by total characters."""
    if not isinstance(values, list):
        return "", False
    rows = values[:MAX_TABLE_ROWS]
    dropped_rows = len(values) > MAX_TABLE_ROWS
    lines = []
    for row in rows:
        cells = row if isinstance(row, list) else [row]
        lines.append(
            " | ".join("" if cell is None else str(cell) for cell in cells[:20])
        )
    body, clipped = _clip("\n".join(lines), MAX_TABLE_CHARS)
    return body, clipped or dropped_rows


def _comparison(result: ActionResult) -> StepComparison | None:
    """Pull the verifier's expected/observed pair out of the verification record.

    `observed` is what the verifier saw after reopening the file from disk, which
    is the only evidence that a change actually landed.
    """
    verification = result.verification
    if verification is None:
        return None
    evidence = verification.evidence or {}
    method = evidence.get("method")
    expected = evidence.get("expected")
    observed = evidence.get("observed")
    if expected is None and observed is None:
        return None
    return StepComparison(
        method=_safe(method) if method is not None else None,
        expected=_safe(expected) if expected is not None else None,
        observed=_safe(observed) if observed is not None else None,
    )


def _path_note(evidence: dict[str, Any]) -> str | None:
    """The existing path-substitution note, as its own informational line."""
    if not evidence.get("path_substituted"):
        return None
    requested = evidence.get("requested_path")
    used = evidence.get("path") or evidence.get("output_path")
    if not requested or not used:
        return None
    return f"Used {_safe(used)} for a step that asked for {_safe(requested)}."


def _sheet_facts(evidence: dict[str, Any]) -> list[StepFact]:
    facts = [_fact("Sheet", evidence["sheet"])] if evidence.get("sheet") else []
    if evidence.get("sheet_substituted") and evidence.get("requested_sheet"):
        facts.append(_fact("Sheet asked for", evidence["requested_sheet"]))
    return facts


def build_step_detail(action_type: str, result: ActionResult) -> StepDetail | None:
    """The bounded view of one step, or None when there is nothing worth adding.

    Never raises: a display helper that can break a response would be worse than
    no display helper, so an unexpected evidence shape yields None.
    """
    try:
        return _build(action_type, result)
    except Exception:  # noqa: BLE001 - display must not be able to fail a response
        return None


def _build(action_type: str, result: ActionResult) -> StepDetail | None:
    evidence = result.evidence or {}
    note = _path_note(evidence)
    comparison = _comparison(result)

    if action_type in _READ_TEXT_ACTIONS:
        count_key, unit = _READ_TEXT_ACTIONS[action_type]
        count = evidence.get(count_key)
        path = evidence.get("path", "")
        facts = [_fact("File", path)]
        if isinstance(count, int):
            facts.append(_fact(f"{unit.capitalize()}s read", count))
        if evidence.get("truncated"):
            facts.append(_fact("Reading stopped early", "the file is longer"))
        text = evidence.get("text")
        return StepDetail(
            summary=_summary_for_read(count, unit, path),
            facts=facts,
            excerpt=(
                _excerpt(
                    f"Text found in {_basename(path)}",
                    text,
                    already_truncated=bool(evidence.get("truncated")),
                )
                if isinstance(text, str) and text.strip()
                else None
            ),
            comparison=comparison,
            note=note,
        )

    if action_type == "file.read_text":
        path = evidence.get("path", "")
        facts = [_fact("File", path)]
        if evidence.get("size") is not None:
            facts.append(_fact("Size in bytes", evidence["size"]))
        content = evidence.get("content")
        return StepDetail(
            summary=f"Read {_basename(path)}.",
            facts=facts,
            excerpt=(
                _excerpt(
                    f"Text found in {_basename(path)}",
                    content,
                    already_truncated=bool(evidence.get("truncated")),
                )
                if isinstance(content, str) and content.strip()
                else None
            ),
            comparison=comparison,
            note=note,
        )

    if action_type == "spreadsheet.read_range":
        path = evidence.get("path", "")
        facts = [_fact("Workbook", path), *_sheet_facts(evidence)]
        if evidence.get("range"):
            facts.append(_fact("Range", evidence["range"]))
        rows, cols = evidence.get("rows"), evidence.get("cols")
        if isinstance(rows, int) and isinstance(cols, int):
            facts.append(_fact("Size", f"{rows} row(s) x {cols} column(s)"))
        rendered, rows_dropped = _render_rows(evidence.get("values"))
        return StepDetail(
            summary=f"Read {evidence.get('range', 'cells')} from {_basename(path)}.",
            facts=facts,
            excerpt=(
                _excerpt(
                    f"Cells read from {_basename(path)}",
                    rendered,
                    MAX_TABLE_CHARS,
                    already_truncated=rows_dropped or bool(evidence.get("truncated")),
                )
                if rendered
                else None
            ),
            comparison=comparison,
            note=note,
        )

    if action_type == "spreadsheet.write_cell":
        path = evidence.get("path", "")
        cell = evidence.get("cell", "")
        facts = [_fact("Workbook", path), *_sheet_facts(evidence)]
        facts.append(_fact("Cell", cell))
        facts.append(_fact("Value written", evidence.get("value")))
        if evidence.get("previous") is not None:
            facts.append(_fact("Value before", evidence["previous"]))
        elif evidence.get("overwrote") or evidence.get("created") is False:
            facts.append(_fact("Value before", "empty"))
        return StepDetail(
            summary=f"Wrote {_safe(evidence.get('value'), 80)} into {cell}.",
            facts=facts,
            comparison=comparison,
            note=note,
        )

    if action_type in _REPLACE_ACTIONS:
        path = evidence.get("path", "")
        output = evidence.get("output_path", path)
        facts = [_fact("File", path)]
        if output and output != path:
            facts.append(_fact("Written to", output))
        else:
            facts.append(_fact("Written to", "the same file, in place"))
        facts.append(_fact("Searched for", evidence.get("find")))
        facts.append(_fact("Replaced with", evidence.get("replace")))
        if evidence.get("replacements") is not None:
            facts.append(_fact("Replacements made", evidence["replacements"]))
        return StepDetail(
            summary=f"Replaced text in {_basename(str(output))}.",
            facts=facts,
            comparison=comparison,
            note=note,
        )

    if action_type in {"file.copy", "file.move"}:
        verb = "Copied" if action_type == "file.copy" else "Moved"
        source = evidence.get("source", "")
        destination = evidence.get("destination", "")
        return StepDetail(
            summary=f"{verb} {_basename(source)} to {_basename(destination)}.",
            facts=[_fact("From", source), _fact("To", destination)],
            comparison=comparison,
            note=note,
        )

    if note or comparison:
        return StepDetail(summary="", comparison=comparison, note=note)
    return None


def _summary_for_read(count: Any, unit: str, path: str) -> str:
    where = _basename(path)
    if isinstance(count, int):
        plural = "" if count == 1 else "s"
        return f"Read {count} {unit}{plural} from {where}."
    return f"Read text from {where}."


def _basename(path: str) -> str:
    """The file name alone, so a summary line stays readable.

    Deliberately string-only: this runs on evidence from an executor that may have
    been on another machine, so it must not touch the local filesystem.
    """
    if not path:
        return "the file"
    return path.replace("\\", "/").rstrip("/").split("/")[-1] or path
