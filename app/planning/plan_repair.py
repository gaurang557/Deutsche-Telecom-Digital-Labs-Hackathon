"""Deterministic draft repair, and the recoverable problems worth re-prompting for.

WHY THIS EXISTS
---------------
Two classes of planner mistake used to end a task outright, even though both are
recoverable:

* A mechanical one with exactly one right answer — `document.read_text` aimed at
  a `.pdf`. :func:`correct_action_families` fixes these in code. Asking a 3B
  model to try again is strictly worse than applying the mapping ourselves, the
  same reasoning behind the sheet-name and path-discovery fixes.
* A judgement one we cannot fix without inventing intent — a plan of pure reads
  for a request that asked for a change, or a path/extension problem with no
  unambiguous correction. :func:`find_recoverable_problems` describes these so
  the planner's existing repair loop can re-prompt with a bounded message.

Everything here is ordinary application code. Nothing consults the model, and
nothing decides permission, risk, trust, confirmation, or verification.

WHAT REPAIR MAY NEVER DO
------------------------
Corrections may only ever *narrow or preserve* authority: a read stays a read,
and a correction that would need confirmation when the original did not is
refused (enforced by `canonical_family_correction`). The recoverable-problem
checks are pure functions returning messages — they can only cause a REJECTION
and a re-prompt, never the addition or authorisation of an action.
"""

from __future__ import annotations

from typing import NamedTuple

from pydantic import ValidationError

from app.planning.capabilities import (
    find_extension_family_mismatch,
    find_fabricated_user_profile_path,
    plan_omits_required_mutation,
)
from app.schemas import DraftPlan
from app.structured_actions import canonical_family_correction


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
            for key in ("destination", "save_as")
            if isinstance(value := action.parameters.get(key), str)
        )
        for candidate in candidates:
            fabricated = find_fabricated_user_profile_path(candidate)
            if fabricated is not None:
                problems.append(
                    f"{action.step_key}: {fabricated!r} invents a user-profile "
                    "directory. Use a Desktop, Documents, or Downloads path instead."
                )
        mismatch = find_extension_family_mismatch(action.type, action.target, action.parameters)
        if mismatch is not None:
            problems.append(f"{action.step_key}: {mismatch}")

    return list(dict.fromkeys(problems))
