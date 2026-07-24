"""Canonical planner/runtime metadata for the sprint's structured actions."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.schemas import ActionType, RiskLevel, StructuredActionType

STRUCTURED_ACTION_TYPES = frozenset(action.value for action in StructuredActionType)

READ_ONLY_STRUCTURED_ACTIONS = frozenset(
    {
        StructuredActionType.FILE_EXISTS.value,
        StructuredActionType.FILE_LIST.value,
        StructuredActionType.FILE_READ_TEXT.value,
        StructuredActionType.PDF_PAGE_COUNT.value,
        StructuredActionType.PDF_GET_METADATA.value,
        StructuredActionType.PDF_READ_TEXT.value,
        StructuredActionType.PDF_SEARCH.value,
        StructuredActionType.SPREADSHEET_LIST_SHEETS.value,
        StructuredActionType.SPREADSHEET_DIMENSIONS.value,
        StructuredActionType.SPREADSHEET_READ_CELL.value,
        StructuredActionType.SPREADSHEET_READ_RANGE.value,
        StructuredActionType.DOCUMENT_READ_TEXT.value,
        StructuredActionType.DOCUMENT_GET_METADATA.value,
        StructuredActionType.DOCUMENT_FIND.value,
        StructuredActionType.PRESENTATION_SLIDE_COUNT.value,
        StructuredActionType.PRESENTATION_GET_METADATA.value,
        StructuredActionType.PRESENTATION_READ_TEXT.value,
        StructuredActionType.PRESENTATION_FIND.value,
    }
)

MODIFYING_STRUCTURED_ACTIONS = STRUCTURED_ACTION_TYPES - READ_ONLY_STRUCTURED_ACTIONS
UNTRUSTED_CONTENT_ACTIONS = frozenset(
    {
        StructuredActionType.FILE_READ_TEXT.value,
        StructuredActionType.PDF_READ_TEXT.value,
        StructuredActionType.DOCUMENT_READ_TEXT.value,
        StructuredActionType.PRESENTATION_READ_TEXT.value,
    }
)
PERMANENTLY_DENIED_ACTIONS = frozenset({StructuredActionType.FILE_DELETE.value})

MAC_ONLY_LEGACY_ACTIONS = frozenset(
    {
        ActionType.CLOSE_APPLICATION,
        ActionType.CLOSE_ALL_APPLICATIONS,
        ActionType.SUMMARIZE_GMAIL_EMAIL,
    }
)

#: Grouped by file type on purpose. A flat alphabetical list made a 3B model
#: reach for `document.find` when handling a PDF, because it was the first
#: plausible name it saw.
PLANNER_VISIBLE_ACTION_TYPES = (
    StructuredActionType.PDF_READ_TEXT.value,
    StructuredActionType.PDF_SEARCH.value,
    StructuredActionType.SPREADSHEET_LIST_SHEETS.value,
    StructuredActionType.SPREADSHEET_DIMENSIONS.value,
    StructuredActionType.SPREADSHEET_READ_RANGE.value,
    StructuredActionType.SPREADSHEET_READ_CELL.value,
    StructuredActionType.SPREADSHEET_WRITE_CELL.value,
    StructuredActionType.DOCUMENT_READ_TEXT.value,
    StructuredActionType.DOCUMENT_REPLACE_TEXT.value,
    StructuredActionType.PRESENTATION_READ_TEXT.value,
    StructuredActionType.PRESENTATION_REPLACE_TEXT.value,
    StructuredActionType.FILE_EXISTS.value,
    StructuredActionType.FILE_READ_TEXT.value,
    StructuredActionType.FILE_WRITE_TEXT.value,
    StructuredActionType.FILE_COPY.value,
    StructuredActionType.FILE_MOVE.value,
    StructuredActionType.FILE_MKDIR.value,
    ActionType.OPEN_FILE.value,
    ActionType.OPEN_URL.value,
    ActionType.OPEN_APPLICATION.value,
    ActionType.FOCUS_APPLICATION.value,
)

PLANNER_ACTION_GUIDANCE = """
Pick the action family from the file extension. This matters more than anything
else: a .pdf file can ONLY be handled by pdf.* actions, never document.*.
  .pdf  -> pdf.read_text, pdf.search
  .xlsx -> spreadsheet.read_range, spreadsheet.read_cell, spreadsheet.write_cell
  .docx -> document.read_text, document.replace_text
  .pptx -> presentation.read_text, presentation.replace_text
  anything else -> file.exists, file.read_text, file.write_text, file.copy,
                   file.move, file.mkdir

Parameters, exactly these names and no others:
- pdf.read_text: optional start_page, end_page, max_chars.
- pdf.search: query (required), optional max_results.
- spreadsheet.list_sheets: none. spreadsheet.dimensions: sheet.
- spreadsheet.read_range: sheet, range (for example A1:D30).
- spreadsheet.read_cell: sheet, cell.
- spreadsheet.write_cell: sheet, cell, value, overwrite. This is also how a new
  workbook is created: point it at an .xlsx path that does not exist yet and the
  workbook is created with that one sheet and cell.
- document.read_text / presentation.read_text: optional max_chars.
- document.replace_text / presentation.replace_text: find, replace, save_as,
  overwrite. Use save_as for a new output file unless the user explicitly asked
  to overwrite in place. Both need an existing .docx or .pptx to start from.
- file.exists: none. file.read_text: encoding, max_bytes.
  file.write_text: content, overwrite. file.copy / file.move: destination,
  overwrite. file.mkdir: parents, exist_ok.

Plan shape for "take a value out of one file and put it into a workbook" — the
plan is incomplete, and nothing happens, if the writing step is missing:
  1. read the source with the action family for its extension
  2. discover the workbook's layout before writing to it
  3. spreadsheet.write_cell, with a $ref value bound to step 1

Never assume a workbook's layout. There is no action that searches a workbook, so
discover it: spreadsheet.list_sheets tells you the sheet names, and
spreadsheet.read_range over a generous area such as A1:F30 shows you where the
labels and the value columns actually are. Do not assume the sheet you want is the
first one, that labels are in column A, that values are in column B, or that data
starts on row 2 — any of those may be false, and a wrong cell silently writes to
the wrong place. Read the layout, then choose the cell that belongs to the label
the user named.

Never reuse a sheet name or cell reference from an example; those are illustrations
of form, not of content.

If the label the user named matches more than one row, or matches none, do not
guess a cell. Explain the choice you need the user to make in the summary instead.

To use a value that an earlier step read out of a file, put this exact object in
place of the value. Never invent a placeholder string such as "$amount":

  {"$ref": "<earlier step_key>.evidence.text",
   "regex": "<regex with one capture group>",
   "group": 1,
   "coerce": "number"}

Use "coerce": "number" for an amount and "coerce": "string" for text. All four
keys are required, and the regex must contain exactly one capture group. A
$ref may only appear inside a later action's parameters.
"""


def action_identity_hash(action_type: str, target: str | None, parameters: dict[str, Any]) -> str:
    payload = json.dumps(
        {"type": str(action_type), "target": target, "parameters": parameters},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def structured_risk(action_type: str, parameters: dict[str, Any]) -> RiskLevel:
    if action_type in PERMANENTLY_DENIED_ACTIONS:
        return RiskLevel.HIGH
    if structured_confirmation_required(action_type, parameters):
        return RiskLevel.HIGH
    if action_type in MODIFYING_STRUCTURED_ACTIONS:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def structured_confirmation_required(
    action_type: str,
    parameters: dict[str, Any],
) -> bool:
    if bool(parameters.get("overwrite")):
        return True
    if action_type in {
        StructuredActionType.DOCUMENT_REPLACE_TEXT.value,
        StructuredActionType.PRESENTATION_REPLACE_TEXT.value,
    }:
        return not bool(parameters.get("save_as"))
    return False
