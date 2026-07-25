"""Deterministic draft repair, completion, and recoverable re-prompting.

WHY THIS EXISTS
---------------
Three classes of planner mistake used to end a task outright, even though all are
recoverable:

* A mechanical one with exactly one right answer — `document.read_text` aimed at
  a `.pdf`. :func:`correct_action_families` fixes these in code. Asking a 3B
  model to try again is strictly worse than applying the mapping ourselves, the
  same reasoning behind the sheet-name and path-discovery fixes.
* A judgement one we cannot fix without inventing intent — a plan of pure reads
  for a request that asked for a change, or a path/extension problem with no
  unambiguous correction. :func:`find_recoverable_problems` describes these so
  the planner's existing repair loop can re-prompt with a bounded message.
* An explicit completion whose inputs all come from the user and existing draft —
  one source read, zero or one workbook read, one requested cell, and one numeric
  field. With no workbook read, an unambiguous same-folder workbook phrase supplies
  only the basename. :func:`complete_explicit_spreadsheet_cell_write` appends the
  canonical existing action only when every input is unique.

Everything here is ordinary application code. Nothing consults the model, and
nothing decides permission, risk, trust, confirmation, or verification.

WHAT REPAIR MAY NEVER DO
------------------------
No repair decides authority, permission, risk, confirmation, policy, execution,
or verification. Family corrections preserve authority. Explicit completion is
visible in plan review and still passes through the ordinary policy and executor;
ambiguous inputs fail closed. Recoverable-problem checks only cause rejection.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from pydantic import ValidationError

from app.planning.capabilities import (
    detect_spreadsheet_cell_write_intent,
    find_explicit_local_root_mismatch,
    find_explicit_spreadsheet_cell,
    find_extension_family_mismatch,
    find_fabricated_user_profile_path,
    find_invalid_reference_group,
    find_invalid_slide_number,
    find_non_string_path_parameter,
    find_null_required_parameter,
    find_positional_search_text,
    find_spoken_filename,
    plan_omits_required_mutation,
)
from app.schemas import DraftPlan, StructuredActionType
from app.structured_actions import (
    PATH_LIKE_PARAMETERS,
    action_mutates,
    canonical_family_correction,
)

_TEXT_SOURCE_READ_TYPES = frozenset(
    {
        StructuredActionType.PDF_READ_TEXT,
        StructuredActionType.DOCUMENT_READ_TEXT,
        StructuredActionType.FILE_READ_TEXT,
        StructuredActionType.PRESENTATION_READ_TEXT,
    }
)
_SPREADSHEET_READ_TYPES = frozenset(
    {
        StructuredActionType.SPREADSHEET_READ_RANGE,
        StructuredActionType.SPREADSHEET_READ_CELL,
        StructuredActionType.SPREADSHEET_LIST_SHEETS,
        StructuredActionType.SPREADSHEET_DIMENSIONS,
    }
)
_NUMERIC_FIELD_FROM_SOURCE_PATTERN = re.compile(
    r"""
    \b(?:read|get|extract|find)\s+(?:the\s+)?
    (?P<field>[a-z0-9][a-z0-9&'’+\- ]{0,119}?)\s+from\b
    (?=\s+(?:the|a|an)\s+[^,.;:!?\r\n]{1,120}
       \b(?:pdf|document|file|presentation|deck|powerpoint)\b)
    """,
    re.IGNORECASE | re.VERBOSE,
)
_SAME_FOLDER_RELATION_PATTERN = re.compile(
    r"\b(?:in|into|to)\s+(?:the\s+)?same\s+(?:folder|directory)\b",
    re.IGNORECASE,
)
_AMBIGUOUS_FIELD_WORDS = frozenset(
    {"a", "an", "and", "from", "into", "or", "the", "to"}
)
_NUMERIC_FIELD_WORDS = frozenset(
    {
        "amount",
        "balance",
        "cost",
        "count",
        "expense",
        "income",
        "margin",
        "number",
        "percent",
        "percentage",
        "price",
        "profit",
        "quantity",
        "rate",
        "revenue",
        "sales",
        "score",
        "tax",
        "total",
        "units",
        "value",
    }
)


class RecoverablePlanError(ValueError):
    """A draft that is well-formed but semantically wrong in a fixable way.

    Subclasses `ValueError` so the planner's existing validate-and-repair loop
    treats it exactly like a schema failure: re-prompt with the bounded message,
    and fail closed through `InvalidPlannerResponseError` if repair runs out.
    """


class FamilyCorrection(NamedTuple):
    """One rewrite: which step changed, and from what to what.

    Returned for the audit trail so a correction is visible rather than silent —
    the same reason the sheet and path substitutions are recorded as evidence.
    """

    step_key: str
    previous: str
    corrected: str

    def describe(self) -> str:
        return f"{self.step_key}: {self.previous} -> {self.corrected}"


class SpreadsheetWriteCompletion(NamedTuple):
    """The bounded facts recorded when one explicit write is appended."""

    step_key: str
    source_step_key: str
    workbook_step_key: str | None
    target: str
    cell: str
    regex: str

    def describe(self) -> str:
        return (
            f"{self.step_key}: {self.source_step_key} -> {self.target} "
            f"cell {self.cell}"
        )


def _numeric_field_phrase(request_text: str) -> str | None:
    """Extract one bounded field phrase immediately preceding a source reference."""
    candidates: list[str] = []
    for match in _NUMERIC_FIELD_FROM_SOURCE_PATTERN.finditer(request_text):
        phrase = " ".join(match.group("field").split())
        words = phrase.casefold().split()
        if (
            not 1 <= len(words) <= 8
            or any(word in _AMBIGUOUS_FIELD_WORDS for word in words)
            or not any(word in _NUMERIC_FIELD_WORDS for word in words)
        ):
            continue
        candidates.append(phrase)
    if len(candidates) != 1:
        return None
    return candidates[0]


def _numeric_field_regex(field_phrase: str) -> str:
    label = r"\s+".join(re.escape(token) for token in field_phrase.split())
    number = r"([+-]?(?:\d+(?:\.\d+)?|\.\d+))(?![\d.])"
    return rf"{label}\s*:\s*{number}"


def _same_folder_workbook_target(
    request_text: str,
    source_target: str,
) -> str | None:
    """Derive only a workbook basename under the source target's stated parent."""
    if (
        _SAME_FOLDER_RELATION_PATTERN.search(request_text) is None
        or "://" in source_target
    ):
        return None
    filename = find_spoken_filename(request_text, "spreadsheet")
    if filename is None:
        return None
    separator = max(source_target.rfind("/"), source_target.rfind("\\"))
    if separator < 0:
        return None
    target = f"{source_target[: separator + 1]}{filename}"
    if len(target) > 500:
        return None
    return target


def complete_explicit_spreadsheet_cell_write(
    draft: DraftPlan,
    request_text: str,
) -> tuple[DraftPlan, SpreadsheetWriteCompletion | None]:
    """Append one explicit cell write only when every semantic input is unique.

    This composes an existing planner-visible action; it neither authorises nor
    executes it. The returned draft is revalidated through Pydantic and will still
    pass through semantic checks, normalisation, policy, review, and verification.
    """
    if not detect_spreadsheet_cell_write_intent(request_text):
        return draft, None
    cell = find_explicit_spreadsheet_cell(request_text)
    if cell is None:
        return draft, None
    if any(action_mutates(action.type) for action in draft.actions):
        return draft, None

    source_reads = [
        action for action in draft.actions if action.type in _TEXT_SOURCE_READ_TYPES
    ]
    workbook_reads = [
        action for action in draft.actions if action.type in _SPREADSHEET_READ_TYPES
    ]
    if len(source_reads) != 1 or len(workbook_reads) > 1:
        return draft, None

    step_keys = [action.step_key for action in draft.actions]
    if len(step_keys) != len(set(step_keys)) or len(draft.actions) >= 50:
        return draft, None
    field_phrase = _numeric_field_phrase(request_text)
    if field_phrase is None:
        return draft, None

    source = source_reads[0]
    workbook = workbook_reads[0] if workbook_reads else None
    if workbook is not None:
        workbook_target = workbook.target
        workbook_step_key = workbook.step_key
    else:
        workbook_target = _same_folder_workbook_target(request_text, source.target)
        workbook_step_key = None
        if workbook_target is None:
            return draft, None

    step_key = "write_requested_cell"
    suffix = 2
    while step_key in step_keys:
        step_key = f"write_requested_cell_{suffix}"
        suffix += 1

    regex = _numeric_field_regex(field_phrase)
    payload = draft.model_dump(mode="json")
    payload["actions"].append(
        {
            "step_key": step_key,
            "type": StructuredActionType.SPREADSHEET_WRITE_CELL.value,
            "target": workbook_target,
            "description": "Write the requested value to the requested spreadsheet cell.",
            "parameters": {
                "cell": cell,
                "value": {
                    "$ref": f"{source.step_key}.evidence.text",
                    "regex": regex,
                    "group": 1,
                    "coerce": "number",
                },
                "overwrite": False,
            },
            "depends_on": list(
                dict.fromkeys(
                    [source.step_key]
                    + ([workbook_step_key] if workbook_step_key is not None else [])
                )
            ),
            "expected_result": {
                "cell": cell,
                "value_source": source.step_key,
            },
        }
    )
    completion = SpreadsheetWriteCompletion(
        step_key=step_key,
        source_step_key=source.step_key,
        workbook_step_key=workbook_step_key,
        target=workbook_target,
        cell=cell,
        regex=regex,
    )
    try:
        return DraftPlan.model_validate(payload), completion
    except ValidationError:
        return draft, None


def correct_action_families(draft: DraftPlan) -> tuple[DraftPlan, list[FamilyCorrection]]:
    """Rewrite action types whose target extension names a different family.

    Returns the draft to carry on with and the corrections applied. The original
    draft is never mutated; a corrected copy is re-validated through `DraftPlan`
    so a rewrite can never bypass the schema, the planner-visible allowlist, or
    the per-action parameter rules. If the corrected draft would not validate —
    for example because a parameter is meaningless in the new family — the
    corrections are DISCARDED and the caller falls through to the repair loop
    rather than guessing which parameters to drop.
    """
    payload = draft.model_dump(mode="json")
    corrections: list[FamilyCorrection] = []

    for action in payload["actions"]:
        previous = str(action["type"])
        corrected = canonical_family_correction(previous, str(action.get("target") or ""))
        if corrected is None:
            continue
        corrections.append(
            FamilyCorrection(str(action["step_key"]), previous, corrected)
        )
        action["type"] = corrected

    if not corrections:
        return draft, []
    try:
        return DraftPlan.model_validate(payload), corrections
    except ValidationError:
        return draft, []


def find_recoverable_problems(draft: DraftPlan, request_text: str) -> list[str]:
    """Semantic problems a repair attempt could plausibly fix, as messages.

    Ordered so the most actionable comes first, and deduplicated so a repeated
    mistake across steps does not inflate the message the planner is re-prompted
    with. Returning an empty list means the draft is fit to normalise.
    """
    problems: list[str] = []

    omission = plan_omits_required_mutation(
        request_text, [action.type for action in draft.actions]
    )
    if omission is not None:
        problems.append(omission)

    for action in draft.actions:
        candidates = [action.target]
        candidates.extend(
            value
            for key in sorted(PATH_LIKE_PARAMETERS)
            if isinstance(value := action.parameters.get(key), str)
        )
        for candidate in candidates:
            root_mismatch = find_explicit_local_root_mismatch(request_text, candidate)
            if root_mismatch is not None:
                problems.append(f"{action.step_key}: {root_mismatch}")
            fabricated = find_fabricated_user_profile_path(candidate)
            if fabricated is not None:
                problems.append(
                    f"{action.step_key}: {fabricated!r} invents a user-profile "
                    "directory. Use a Desktop, Documents, or Downloads path instead."
                )
        mismatch = find_extension_family_mismatch(action.type, action.target, action.parameters)
        if mismatch is not None:
            problems.append(f"{action.step_key}: {mismatch}")
        bad_slide = find_invalid_slide_number(action.parameters)
        if bad_slide is not None:
            problems.append(f"{action.step_key}: {bad_slide}")
        null_required = find_null_required_parameter(action.type, action.parameters)
        if null_required is not None:
            problems.append(f"{action.step_key}: {null_required}")
        bad_path_param = find_non_string_path_parameter(action.parameters)
        if bad_path_param is not None:
            problems.append(f"{action.step_key}: {bad_path_param}")
        bad_group = find_invalid_reference_group(action.parameters)
        if bad_group is not None:
            problems.append(f"{action.step_key}: {bad_group}")

    return list(dict.fromkeys(problems))


def find_advisory_problems(draft: DraftPlan) -> list[str]:
    """Problems worth recording that must NOT stop the plan.

    WHY THIS CATEGORY EXISTS
    ------------------------
    A search string that names a position ("slide 3") instead of wording is
    certainly wrong, and rejecting it looked right. In practice it made things
    worse: the local 3B model cannot reliably produce the alternative — the deck's
    real placeholder token, which it cannot know without reading the deck first —
    so every attempt burned repair budget and the task ended as an opaque "could
    not produce a valid action plan" instead of running.

    Letting it through costs nothing and explains more. `replace_text` that finds
    no match is a NO-OP: it changes no file, so there is no dangerous outcome to
    prevent, and the failure arrives as `Text not found in presentation: 'slide 3'`
    — which says precisely what went wrong, after the read steps have visibly
    succeeded.

    This is a reporting change only. It removes no safety property: the
    mutation-completeness check still requires the plan to CONTAIN a write step, so
    the silent-success defect stays fixed, and nothing here grants an action,
    relaxes confirmation, or alters risk.
    """
    advisories: list[str] = []
    for action in draft.actions:
        positional = find_positional_search_text(action.type, action.parameters)
        if positional is not None:
            advisories.append(f"{action.step_key}: {positional}")
    return list(dict.fromkeys(advisories))
