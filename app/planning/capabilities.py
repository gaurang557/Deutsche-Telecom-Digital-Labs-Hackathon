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
from pathlib import Path
from typing import Any

from app.schemas import StructuredActionType

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

#: Extensions each structured action family is able to operate on. ``file.*`` is
#: deliberately absent: it is extension-agnostic by design.
_FAMILY_EXTENSIONS: dict[str, frozenset[str]] = {
    "spreadsheet": frozenset({".xlsx"}),
    "pdf": frozenset({".pdf"}),
    "document": frozenset({".docx"}),
    "presentation": frozenset({".pptx"}),
}

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
_PATH_PARAMETERS = ("destination", "save_as")

_USER_PROFILE_PATTERN = re.compile(r"^[A-Za-z]:[\\/]+Users[\\/]+([^\\/]+)", re.IGNORECASE)


def detect_unsupported_request(text: str) -> str | None:
    """Return a user-legible refusal when no visible action can satisfy `text`."""
    for pattern, refusal in _UNSUPPORTED_CREATE_PATTERNS:
        if pattern.search(text):
            return refusal
    return None


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
