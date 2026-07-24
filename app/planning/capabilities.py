"""Deterministic capability checks that keep the planner from guessing.

The planner is a local LLM and will happily invent a plausible-looking plan for a
request the runtime cannot satisfy. The checks here run in ordinary application
code, so a refusal never depends on the model choosing to behave.

Two distinct classes of problem are handled:

* :func:`detect_unsupported_request` — the *request* asks for an artifact this
  build cannot create at all (a new PDF, Word document, or PowerPoint deck).
  The caller turns the returned sentence into a user-facing refusal instead of a
  plan that is guaranteed to fail at execution.
* :func:`find_fabricated_user_profile_path` and
  :func:`find_extension_family_mismatch` — the *draft* contains a hallucinated
  absolute user-profile path, or a path whose extension contradicts the action
  family (for example a ``.docx`` target on a ``spreadsheet.*`` action).

A new ``.xlsx`` workbook is deliberately absent from the unsupported list:
``spreadsheet.write_cell`` creates the workbook when the target does not exist,
so "create a new spreadsheet" is a supported request.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.schemas import StructuredActionType
from app.structured_actions import (
    FAMILY_EXTENSIONS,
    PATH_LIKE_PARAMETERS,
    action_mutates,
    compile_reference_regex,
    is_absent_path_value,
    required_parameters_for,
)

#: Determiners the model tends to put between a create verb and the artifact.
_DETERMINERS = r"(?:me\s+)?(?:(?:a|an|the|another|some|new|blank|empty|fresh)\s+)*"
_CREATE_VERBS = r"(?:create|make|generate|build|draft|produce|author)"

#: Each entry binds a create verb tightly to an artifact this build cannot
#: create from nothing, then explains what *is* possible. The gap between verb
#: and noun is limited to determiners so that a supported request such as
#: "create a summary in the presentation template" does not match.
_UNSUPPORTED_CREATE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            rf"\b{_CREATE_VERBS}\s+{_DETERMINERS}"
            r"(?:powerpoints?|power\s*points?|presentations?|slide\s*decks?|decks?"
            r"|slideshows?|slides?|pptx)\b",
            re.IGNORECASE,
        ),
        "I can't create a new PowerPoint presentation from scratch. I can read an "
        "existing .pptx and save an updated copy of it — for example, replacing "
        "placeholder text in a template and writing the result to a new file.",
    ),
    (
        re.compile(
            rf"\b{_CREATE_VERBS}\s+{_DETERMINERS}"
            r"(?:word\s+(?:documents?|docs?|files?)|docx)\b",
            re.IGNORECASE,
        ),
        "I can't create a new Word document from scratch. I can read an existing "
        ".docx and save an updated copy of it, or write a plain text file instead.",
    ),
    (
        re.compile(
            rf"\b{_CREATE_VERBS}\s+{_DETERMINERS}(?:pdfs?)\b",
            re.IGNORECASE,
        ),
        "I can't create or edit a PDF. I can only read from an existing PDF — its "
        "text, page count, metadata, or a search across it.",
    ),
)

#: Owned by ``app.structured_actions`` so the mismatch check below and the
#: deterministic family correction there can never disagree about which
#: extension belongs to which family.
_FAMILY_EXTENSIONS = FAMILY_EXTENSIONS

#: Extensions whose bytes are a container, not text. Reading one with
#: `file.read_text` yields mojibake, and writing one with `file.write_text`
#: destroys it. Observed live: a planner reached for `file.write_text` on an
#: .xlsx, which would have replaced a workbook with a line of plain text.
_BINARY_DOCUMENT_EXTENSIONS = frozenset(
    {".xlsx", ".xlsm", ".docx", ".pptx", ".pdf"}
)

_TEXT_ONLY_ACTIONS = frozenset(
    {
        StructuredActionType.FILE_READ_TEXT.value,
        StructuredActionType.FILE_WRITE_TEXT.value,
    }
)

#: Parameters that carry a second path the runtime will resolve and write to.
#: Sorted for a deterministic report order when several candidates are wrong.
#: Derived from the canonical set rather than restated, so the mismatch check and
#: the stripping rule cannot come to disagree about what a path parameter is.
_PATH_PARAMETERS = tuple(sorted(PATH_LIKE_PARAMETERS))

_USER_PROFILE_PATTERN = re.compile(r"^[A-Za-z]:[\\/]+Users[\\/]+([^\\/]+)", re.IGNORECASE)


def detect_unsupported_request(text: str) -> str | None:
    """Return a user-legible refusal when no visible action can satisfy `text`."""
    for pattern, refusal in _UNSUPPORTED_CREATE_PATTERNS:
        if pattern.search(text):
            return refusal
    return None


#: Verbs that mean the user wants something on disk to be different afterwards.
#: Deliberately base forms only: a transcript of a spoken instruction is
#: imperative ("update slide 3", "fill in the revenue column"), while inflected
#: forms show up in descriptive noun phrases ("the updated report") where no
#: mutation is being asked for. Kept bounded and conservative — see
#: `plan_omits_required_mutation` for why a false positive is cheap and a false
#: negative is not.
_MUTATION_VERBS = (
    "update",
    "change",
    "replace",
    "set",
    "fill",
    "write",
    "put",
    "add",
    "insert",
    "rename",
    "move",
    "copy",
    "delete",
    "remove",
    "save",
    "create",
    "organize",
    "organise",
    "sort",
    "record",
    "populate",
    "append",
)

_MUTATION_INTENT_PATTERN = re.compile(
    rf"\b(?:{'|'.join(_MUTATION_VERBS)})\b",
    re.IGNORECASE,
)


def detect_mutation_intent(text: str) -> bool:
    """Whether the request asks for something to be changed, not just read.

    Deterministic and text-only: it never inspects a plan and never decides
    permission, risk, or authority. Its single consumer may only use it to
    REJECT a plan that changes nothing, never to add or upgrade an action.
    """
    return _MUTATION_INTENT_PATTERN.search(text) is not None


def _home() -> Path | None:
    try:
        return Path.home()
    except (OSError, RuntimeError):
        return None


def find_fabricated_user_profile_path(value: Any) -> str | None:
    """Return the path when it names a user profile other than the real one.

    The planner is told to use the Desktop/Documents/Downloads aliases that the
    runtime resolves. When it invents an absolute path such as
    ``C:\\Users\\User\\Desktop\\...`` the plan is doomed, and on a multi-user
    machine it would also reach outside the current user's own files.

    Identity is decided by resolving both sides, not by comparing the profile
    segment as text. Windows 8.3 short names mean the same profile can be spelled
    two ways (``ANIKET~1`` and ``Aniket Kadiyan``), and a textual comparison
    rejected legitimate paths under the user's own temp directory.
    """
    if not isinstance(value, str):
        return None
    match = _USER_PROFILE_PATTERN.match(value.strip())
    if match is None:
        return None
    home = _home()
    if home is None:
        return None

    try:
        resolved = Path(value).resolve(strict=False)
        if resolved.is_relative_to(home.resolve(strict=False)):
            return None
    except (OSError, RuntimeError, ValueError):
        pass

    if match.group(1).casefold() == home.name.casefold():
        return None
    return value


def find_extension_family_mismatch(
    action_type: Any,
    target: str,
    parameters: dict[str, Any],
) -> str | None:
    """Return a message when a path's extension contradicts the action family."""
    if not isinstance(action_type, StructuredActionType):
        return None

    candidates = [target]
    for key in _PATH_PARAMETERS:
        value = parameters.get(key)
        if isinstance(value, str):
            candidates.append(value)

    if action_type.value in _TEXT_ONLY_ACTIONS:
        for candidate in candidates:
            suffix = Path(candidate).suffix.casefold()
            if suffix in _BINARY_DOCUMENT_EXTENSIONS:
                return (
                    f"{action_type.value} treats a file as plain text, so it "
                    f"cannot be used on {candidate!r}; use the {suffix.lstrip('.')} "
                    "action family instead"
                )
        return None

    family = action_type.value.split(".", 1)[0]
    allowed = _FAMILY_EXTENSIONS.get(family)
    if allowed is None:
        return None

    expected = ", ".join(sorted(allowed))
    for candidate in candidates:
        suffix = Path(candidate).suffix.casefold()
        if suffix and suffix not in allowed:
            return (
                f"{action_type.value} needs a {expected} path but the planner "
                f"proposed {candidate!r}"
            )
    return None


#: A bare positional reference: wording that says WHERE to look rather than what
#: is written there. Anchored at both ends and deliberately narrow, so it matches
#: only a phrase that is nothing but a position — "slide 3", "the page 2" — and
#: never a real sentence that happens to mention one ("slide 3 of the review").
#: A deck whose text is literally just "Slide 3" is vanishingly unlikely, and the
#: cost of being wrong is one repair attempt rather than a wrong action.
_POSITIONAL_REFERENCE_PATTERN = re.compile(
    r"^\s*(?:the\s+)?(?:slide|page|sheet|row|column)\s*#?\s*\d+\s*$",
    re.IGNORECASE,
)

_REPLACE_TEXT_ACTIONS = frozenset(
    {
        StructuredActionType.DOCUMENT_REPLACE_TEXT.value,
        StructuredActionType.PRESENTATION_REPLACE_TEXT.value,
    }
)


def find_positional_search_text(action_type: Any, parameters: dict[str, Any]) -> str | None:
    """Return a message when a replace step is searching for a position.

    Observed live: asked to "update slide 3 of <deck>", the planner passed the
    literal string "slide 3" as the text to find. That phrase is how the user
    pointed at a location; it is not text that exists in the deck, so the step
    could only ever fail with `text_not_found`.

    SAFETY: returns a message or None, nothing else. It never rewrites the
    parameter, never invents replacement text, and never touches policy,
    confirmation, or verification — its only effect is to send the plan back for
    one bounded repair attempt.
    """
    if str(action_type) not in _REPLACE_TEXT_ACTIONS:
        return None
    find = parameters.get("find")
    if not isinstance(find, str) or not _POSITIONAL_REFERENCE_PATTERN.match(find):
        return None
    return (
        f"{find!r} says where to look, not what is written there, so there is "
        "nothing in the file to find. Read that slide or page first, then use the "
        "exact wording it actually contains as the text to find."
    )


def find_null_required_parameter(action_type: Any, parameters: dict[str, Any]) -> str | None:
    """Return a message when a REQUIRED parameter was supplied as null.

    An optional parameter given as null means "not using this" and is dropped
    during plan build. A REQUIRED one given as null means the plan is incomplete,
    so it earns a repair attempt instead: dropping it would only produce a more
    confusing failure further down.

    Only `None` counts as no value here. An empty string can be a legitimate
    required value — `file.write_text` with `content: ""` writes an empty file —
    and calling that absent would reject a valid plan.
    """
    absent = sorted(
        key
        for key in required_parameters_for(str(action_type))
        if key in parameters and parameters[key] is None
    )
    if not absent:
        return None
    names = ", ".join(absent)
    return f"{names} is required and was given no value; supply a real value for it."


def find_non_string_path_parameter(parameters: dict[str, Any]) -> str | None:
    """Return a message when a path parameter holds something that is not a path.

    `destination` and `save_as` name a file, so a number, an object or a list there
    is a malformed plan. Naming the offending value gives the planner something it
    can actually correct, where the old execution-time failure reported only the
    parameter name and was undiagnosable from a log.

    Values that mean "not using this" are NOT reported: they are dropped during
    plan build instead. The absence test is shared with the stripping rule rather
    than restated, because the two disagreeing would be its own bug — this check
    runs on the draft, BEFORE stripping, so anything it rejects here can never
    reach the point where it would have been harmlessly removed.
    """
    for key in sorted(PATH_LIKE_PARAMETERS):
        if key not in parameters:
            continue
        value = parameters[key]
        if value is None or isinstance(value, str) or is_absent_path_value(value):
            continue
        return (
            f"{key} must be a file path written as a string, not {value!r}. "
            f"Leave {key} out entirely to change the file named by target."
        )
    return None


def find_invalid_reference_group(parameters: Any, *, depth: int = 0) -> str | None:
    """Return a message when a `$ref`'s regex cannot yield the group it asks for.

    A live run produced a `$ref` whose regex defined no capture group while asking
    for group 1. `match.group(1)` raised `IndexError` mid-execution, which escaped
    as an HTTP 500. The executor now refuses that deliberately, but a plan is
    cheaper to fix than a run, so it is caught here first and the planner is told
    to add the parentheses.

    Walks nested structures because a `$ref` can sit anywhere in a parameter tree.
    Returns a message or None: it never rewrites the pattern and never guesses
    which group was meant.
    """
    if depth > 8:
        return None
    if isinstance(parameters, list):
        for item in parameters[:100]:
            problem = find_invalid_reference_group(item, depth=depth + 1)
            if problem is not None:
                return problem
        return None
    if not isinstance(parameters, dict):
        return None
    if "$ref" not in parameters:
        for item in list(parameters.values())[:100]:
            problem = find_invalid_reference_group(item, depth=depth + 1)
            if problem is not None:
                return problem
        return None

    regex = parameters.get("regex")
    if regex is None or not isinstance(regex, str):
        return None
    try:
        # The shared compile path, so this check and the executor can never
        # disagree about the flags a `$ref` pattern is matched with.
        compiled = compile_reference_regex(regex)
    except re.error as exc:
        return f"the regex {regex!r} is not a valid pattern ({exc})."
    group = parameters.get("group", 1)
    if isinstance(group, bool) or not isinstance(group, (int, str)):
        return None
    if isinstance(group, int) and not 0 <= group <= compiled.groups:
        return (
            f"the regex {regex!r} has {compiled.groups} capture group(s), so "
            f"group {group} does not exist. Put parentheses around the part of "
            'the pattern that is the value you want, for example "revenue: '
            '([0-9.]+)" with "group": 1.'
        )
    if isinstance(group, str) and group not in compiled.groupindex:
        return f"the regex {regex!r} has no group named {group!r}."
    return None


def find_invalid_slide_number(parameters: dict[str, Any]) -> str | None:
    """Return a message when a `slide` parameter is not a slide number.

    Slide positions are 1-based on the planner-facing contract, the way a person
    counts them. Catching a bad one here gives the planner a bounded repair
    attempt; `_to_executor_indexes` refuses it again at the conversion boundary,
    so a nonsensical value can never reach an executor either way.

    SAFETY: returns a message or None. It never rewrites the parameter and never
    guesses which slide was meant.
    """
    if "slide" not in parameters:
        return None
    value = parameters["slide"]
    if value is None:
        return None
    # A `$ref` is still a placeholder here; its value is unknowable until the
    # earlier step runs. The boundary check sees the resolved value and refuses it
    # there if it is not a slide number, so nothing is let through unchecked.
    if isinstance(value, dict):
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return f"slide must be a whole number counting from 1, not {value!r}."
    if value < 1:
        return (
            f"slide counts from 1, the way you would say it, so {value} is not a "
            "slide number. The first slide is 1."
        )
    return None


def plan_omits_required_mutation(request_text: str, action_types: Iterable[Any]) -> str | None:
    """Return a message when a change was asked for and the plan changes nothing.

    Observed live and far worse than a failure: asked to read a document and
    update a slide, the planner emitted two reads, both succeeded, and the task
    was reported as "everything completed successfully" while the slide was
    untouched. A plan of pure reads can never satisfy a request to change
    something, so it is rejected here and sent back for repair.

    SAFETY: this function only ever returns a refusal message. It cannot add,
    synthesise, authorise, or upgrade an action, and it does not touch policy,
    confirmation, or verification. The worst outcome of a false positive is one
    wasted repair attempt followed by a clean, honest failure — never a change
    in privilege. Mutation intent is therefore read conservatively from the
    user's own words only.
    """
    if not detect_mutation_intent(request_text):
        return None
    if any(action_mutates(action_type) for action_type in action_types):
        return None
    return (
        "The request asks for something to be changed, but every step in this "
        "plan only reads. Add the step that actually performs the change "
        "(writing the cell, replacing the text, or moving the file), keeping "
        "the reading steps it depends on."
    )
