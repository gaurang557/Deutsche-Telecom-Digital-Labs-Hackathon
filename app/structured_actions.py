"""Canonical planner/runtime metadata for the sprint's structured actions."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import PurePosixPath
from typing import Any

from app.schemas import (
    STRUCTURED_PARAMETER_KEYS,
    STRUCTURED_REQUIRED_PARAMETER_KEYS,
    ActionType,
    RiskLevel,
    StructuredActionType,
)

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

#: Actions that BRING THEIR TARGET INTO EXISTENCE. For these the target is a
#: destination, not a subject: a path that does not exist yet is the normal,
#: intended case, so it must be taken literally and never resolved onto some
#: same-named file elsewhere. `spreadsheet.write_cell` belongs here even though it
#: often edits an existing workbook, because it CREATES one when the target is
#: missing (see its executor docstring) — and a create that got redirected into an
#: existing workbook would modify a file the user never named.
CREATES_ITS_TARGET_ACTIONS = frozenset(
    {
        StructuredActionType.SPREADSHEET_WRITE_CELL.value,
        StructuredActionType.FILE_WRITE_TEXT.value,
        StructuredActionType.FILE_MKDIR.value,
    }
)

#: Actions whose target must ALREADY EXIST for the action to mean anything: every
#: read, plus the in-place edits (`document.replace_text`,
#: `presentation.replace_text`) and the copy/move sources. Only these may have a
#: target resolved onto a file that is really there.
#:
#: Derived by subtraction rather than listed, so it cannot drift from
#: `MODIFYING_STRUCTURED_ACTIONS`: a newly added action type is covered
#: automatically, and the only thing anyone has to declare is whether an action
#: creates its own target. Permanently denied actions are excluded because nothing
#: should help them locate a file.
REQUIRES_EXISTING_TARGET_ACTIONS = (
    STRUCTURED_ACTION_TYPES - CREATES_ITS_TARGET_ACTIONS - PERMANENTLY_DENIED_ACTIONS
)

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
- pdf.read_text: reads one known .pdf file; optional start_page, end_page, max_chars.
- pdf.search: finds query text inside one known .pdf file, never files or
  directories; query (required), optional max_results.
- spreadsheet.list_sheets: none. spreadsheet.dimensions: optional sheet.
- spreadsheet.read_range: range (for example A1:D30), optional sheet.
- spreadsheet.read_cell: cell, optional sheet.
- spreadsheet.write_cell: cell, value, overwrite, optional sheet. This is also how
  a new workbook is created: point it at an .xlsx path that does not exist yet and
  the workbook is created with that one sheet and cell.
- document.read_text: optional max_chars. presentation.read_text: optional
  max_chars and slide, counted from 1 just as you say it, so slide 3 is
  {"slide": 3}.
- document.replace_text / presentation.replace_text: find, replace, save_as,
  overwrite. Use save_as for a new output file unless the user explicitly asked
  to overwrite in place. Both need an existing .docx or .pptx to start from.
  find must be wording that is really in the file, never "slide 3" or "page 2",
  which say WHERE to look. A template file marks the spot to fill in with a
  placeholder token — an ALL-CAPS word such as SOME_PLACEHOLDER, or a {{MARKER}} —
  so for "update slide N of a template", find is that token and replace is the
  text you read from the other file. If you do not know the token, omit save_as
  and use the most likely placeholder wording rather than a position.
- file.exists: none. file.read_text: encoding, max_bytes.
  file.write_text: content, overwrite. file.copy / file.move: destination,
  overwrite. file.mkdir: parents, exist_ok.

Plan shape for "take a value out of one file and put it into a workbook" — the
plan is incomplete, and nothing happens, if the writing step is missing:
  1. read the source with the action family for its extension
  2. read a generous area of the workbook to see its layout
  3. spreadsheet.write_cell, with a $ref value bound to step 1

Never invent a sheet name you have not been shown. In a workbook that already
exists, leave the sheet parameter out unless the user named a sheet or an earlier
step's result showed you its name: left out it means that workbook's own first
sheet, while a guessed name can fail the whole task. Name the sheet only when you
are creating the workbook, where you are the one choosing the name.

Never assume a workbook's layout. There is no action that searches a workbook, so
read it: spreadsheet.read_range over a generous area such as A1:F30 shows you
where the labels and the value columns actually are. Do not assume that labels are
in column A, that values are in column B, or that data starts on row 2 — any of
those may be false, and a wrong cell silently writes to the wrong place. Read the
layout, then choose the cell that belongs to the label the user named.

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


#: Optional parameters whose value is a PATH. An empty or whitespace-only string
#: is not a path, so for these it says the same thing as omitting the key. Kept
#: narrow on purpose: for a non-path parameter an empty string can be a real
#: value (`content: ""` writes an empty file), so blankness is not read as
#: absence anywhere else.
PATH_LIKE_PARAMETERS = frozenset({"destination", "save_as"})


#: Flags for every model-supplied `$ref` regex, wherever one is compiled.
#:
#: A live run failed by exactly one letter: the planner wrote `revenue: ([0-9.]+)`
#: and the PDF says `Revenue:`. Note what that plan got RIGHT — it targeted Revenue
#: rather than the `Operating Profit` line planted beside it, and it used a proper
#: capture group. Only the capitalisation of a label was wrong, and capitalisation
#: is a detail no planner can reliably reproduce from a spoken request.
#:
#: This is the same leniency already granted to sheet names, which resolve exactly
#: first and then case- and whitespace-insensitively, so the two now agree instead
#: of one being arbitrarily stricter than the other.
#:
#: It is a MATCHING leniency and not a value transformation. What the pattern finds
#: is returned exactly as it appears in the document, with its own casing intact;
#: nothing is rewritten, only found.
REFERENCE_REGEX_FLAGS = re.IGNORECASE


def compile_reference_regex(pattern: str) -> re.Pattern[str]:
    """Compile a `$ref` regex the one way the whole system compiles them.

    THE POINT OF THIS FUNCTION IS THAT THERE IS ONLY ONE OF IT. Plan-time
    validation and execution-time matching must agree about the flags, because a
    plan-time check that compiled differently from the executor would pass a plan
    the executor then refuses — the same ordering bug that produced an unexplained
    422 earlier in this project's history.

    Raises `re.error` for a pattern that does not compile, which both callers
    already handle: the plan-time check turns it into a repair message, the
    executor into a failed action.
    """
    return re.compile(pattern, REFERENCE_REGEX_FLAGS)


def _structured_type(action_type: str) -> StructuredActionType | None:
    try:
        return StructuredActionType(action_type)
    except ValueError:
        return None


def required_parameters_for(action_type: str) -> frozenset[str]:
    """The parameters an action cannot run without."""
    structured = _structured_type(action_type)
    if structured is None:
        return frozenset()
    return STRUCTURED_REQUIRED_PARAMETER_KEYS.get(structured, frozenset())


def optional_parameters_for(action_type: str) -> frozenset[str]:
    """The parameters an action accepts but does not require.

    Derived by subtraction from the two canonical registries in `app.schemas`
    rather than listed again here, so it cannot drift from either. A legacy
    (non-structured) action yields nothing, which leaves its parameters untouched.
    """
    structured = _structured_type(action_type)
    if structured is None:
        return frozenset()
    accepted = STRUCTURED_PARAMETER_KEYS.get(structured, frozenset())
    return accepted - STRUCTURED_REQUIRED_PARAMETER_KEYS.get(structured, frozenset())


def strip_absent_optional_parameters(
    action_type: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Drop OPTIONAL parameters the planner filled in as "not using this".

    A model asked for a JSON object routinely writes `"save_as": null` for an
    optional field it does not want. Omitting the key is what that means, and the
    executors already treat an absent `save_as` as "edit in place", so dropping it
    is what makes the plan runnable rather than a reinterpretation of it.

    Strictly omission, never coercion. Nothing is retyped: no string becomes a
    bool or a number, and a value that is present and of the wrong type still
    fails loudly instead of being guessed at.

    A REQUIRED parameter is never dropped, however absent its value looks. A null
    `find` is a broken plan rather than an unused option, and silently removing it
    would turn a clear failure into a confusing one.
    """
    optional = optional_parameters_for(action_type)
    path_like = optional & PATH_LIKE_PARAMETERS
    stripped: dict[str, Any] = {}
    for key, value in parameters.items():
        if key in optional:
            if value is None:
                continue
            if key in path_like and is_absent_path_value(value):
                continue
        stripped[key] = value
    return stripped


def is_absent_path_value(value: Any) -> bool:
    """Whether a value in a PATH parameter says "no path" rather than naming one.

    A path is a string. A blank string is not a file name, and `False` is not a
    file name either — a model writing `save_as: false` is saying it is not using
    the option, in the same breath as the one writing `null`.

    This stays omission rather than coercion because it is confined to parameters
    whose only legal value is a path: nothing is retyped, and no OTHER parameter
    treats `False` as absent. `overwrite: false` in particular is a real, meaningful
    value and is never touched — the golden workflow-1 plan depends on that.
    """
    if value is False:
        return True
    return isinstance(value, str) and not value.strip()


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


#: Extensions each structured action family can operate on. `file.*` is
#: deliberately absent: it is extension-agnostic by design. This is the one
#: definition of the mapping; `planning/capabilities.py` imports it rather than
#: keeping a copy, so the mismatch check and the correction below cannot drift
#: apart from each other or from the planner-visible list.
FAMILY_EXTENSIONS: dict[str, frozenset[str]] = {
    "spreadsheet": frozenset({".xlsx"}),
    "pdf": frozenset({".pdf"}),
    "document": frozenset({".docx"}),
    "presentation": frozenset({".pptx"}),
}

#: The inverse of `FAMILY_EXTENSIONS`, restricted to extensions that imply
#: exactly ONE family. An extension claimed by two families would be ambiguous
#: and is left out, so a correction is only ever attempted where there is a
#: single right answer.
_EXTENSION_CLAIMS = Counter(
    extension for extensions in FAMILY_EXTENSIONS.values() for extension in extensions
)
FAMILY_FOR_EXTENSION: dict[str, str] = {
    extension: family
    for family, extensions in FAMILY_EXTENSIONS.items()
    for extension in extensions
    if _EXTENSION_CLAIMS[extension] == 1
}

#: Legacy (pre-structured) action types that change state on disk or send
#: something outward. The remaining legacy types only open, focus, or observe.
#: Kept beside the structured sets so "does this plan change anything?" has a
#: single answer for both vocabularies.
MUTATING_LEGACY_ACTIONS = frozenset(
    {
        ActionType.COPY_FILE_CONTENT,
        ActionType.CREATE_FILE,
        ActionType.MOVE_FILE,
        ActionType.OVERWRITE_FILE,
        ActionType.DELETE_FILE,
        ActionType.SEND_MESSAGE,
        ActionType.SUBMIT_FORM,
        ActionType.PUBLISH_CONTENT,
    }
)


def action_mutates(action_type: ActionType | StructuredActionType | str) -> bool:
    """Whether an action type changes anything, per the canonical registry.

    Structured types are decided by `MODIFYING_STRUCTURED_ACTIONS` — the same
    set that drives risk classification and the verifier-required rule — so
    there is no second list to keep in step.
    """
    if isinstance(action_type, StructuredActionType):
        return action_type.value in MODIFYING_STRUCTURED_ACTIONS
    if isinstance(action_type, ActionType):
        return action_type in MUTATING_LEGACY_ACTIONS
    if action_type in STRUCTURED_ACTION_TYPES:
        return action_type in MODIFYING_STRUCTURED_ACTIONS
    try:
        return ActionType(action_type) in MUTATING_LEGACY_ACTIONS
    except ValueError:
        return False


def canonical_family_correction(action_type: str, target: str) -> str | None:
    """The action type `target`'s extension unambiguously calls for, or None.

    `document.read_text` aimed at a `.pdf` is a mechanical mistake with exactly
    one right answer, and a 3B model keeps making it despite the prompt saying
    otherwise. Correcting it deterministically is strictly better than asking
    the model again — the same reasoning behind the sheet-name and path fixes.

    A correction is returned ONLY when every one of these holds:
      * the proposed type belongs to an extension-bound family (never `file.*`,
        which is extension-agnostic on purpose);
      * the target's extension implies exactly one family, and it is a
        different one;
      * that family has an action with the SAME verb, and it is planner-visible
        (so a correction can never reach an action the model is not allowed to
        propose, nor a permanently denied one);
      * the correction does not escalate privilege — a read stays a read, and a
        modifying action may not become one that needs confirmation when the
        original did not.
    Anything else returns None and falls through to the repair loop, which asks
    the planner to fix it rather than guessing here.
    """
    if action_type not in STRUCTURED_ACTION_TYPES:
        return None
    family, _, verb = action_type.partition(".")
    if family not in FAMILY_EXTENSIONS or not verb:
        return None

    extension = PurePosixPath(str(target).replace("\\", "/")).suffix.casefold()
    wanted_family = FAMILY_FOR_EXTENSION.get(extension)
    if wanted_family is None or wanted_family == family:
        return None

    candidate = f"{wanted_family}.{verb}"
    if candidate not in PLANNER_VISIBLE_ACTION_TYPES:
        return None
    if candidate in PERMANENTLY_DENIED_ACTIONS:
        return None
    # Privilege guard: never turn a read into a write, and never turn a write
    # into one that would additionally demand confirmation.
    if not action_mutates(action_type) and action_mutates(candidate):
        return None
    if structured_confirmation_required(candidate, {}) and not structured_confirmation_required(
        action_type, {}
    ):
        return None
    return candidate
