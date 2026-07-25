import logging
import os
import platform
from uuid import UUID, uuid4

from app.execution.hybrid import resolve_plan_target
from app.planning.capabilities import (
    find_extension_family_mismatch,
    find_fabricated_user_profile_path,
    ground_spoken_filename,
)
from app.planning.exceptions import InvalidPlannerResponseError
from app.schemas import (
    Action,
    ActionPlan,
    ActionType,
    DraftAction,
    DraftPlan,
    RiskLevel,
    StructuredActionType,
    TaskRequest,
)
from app.structured_actions import (
    MAC_ONLY_LEGACY_ACTIONS,
    PATH_LIKE_PARAMETERS,
    REQUIRES_EXISTING_TARGET_ACTIONS,
    action_identity_hash,
    strip_absent_optional_parameters,
    structured_confirmation_required,
    structured_risk,
)

_LOGGER = logging.getLogger(__name__)

_HIGH_RISK_ACTIONS = {
    ActionType.CLOSE_APPLICATION,
    ActionType.CLOSE_ALL_APPLICATIONS,
    ActionType.DELETE_FILE,
    ActionType.SEND_MESSAGE,
    ActionType.SUBMIT_FORM,
    ActionType.PUBLISH_CONTENT,
}
_MEDIUM_RISK_ACTIONS = {
    ActionType.COPY_FILE_CONTENT,
    ActionType.CREATE_FILE,
    ActionType.MOVE_FILE,
    ActionType.OVERWRITE_FILE,
}
_CONFIRMATION_ACTIONS = _HIGH_RISK_ACTIONS | {
    ActionType.COPY_FILE_CONTENT,
    ActionType.MOVE_FILE,
    ActionType.OVERWRITE_FILE,
}

_ACTION_VERBS = {
    ActionType.OPEN_APPLICATION: "Open",
    ActionType.OPEN_FILE: "Find and open",
    ActionType.OPEN_URL: "Open",
    ActionType.FOCUS_APPLICATION: "Bring into focus",
    ActionType.CLOSE_APPLICATION: "Close",
    ActionType.CLOSE_ALL_APPLICATIONS: "Close all open applications on",
    ActionType.CLICK_ELEMENT: "Select",
    ActionType.TYPE_TEXT: "Enter text in",
    ActionType.PRESS_KEY: "Use a keyboard shortcut in",
    ActionType.READ_FILE: "Read",
    ActionType.COPY_FILE_CONTENT: "Copy the contents of",
    ActionType.CREATE_FILE: "Create",
    ActionType.MOVE_FILE: "Move",
    ActionType.OVERWRITE_FILE: "Update",
    ActionType.DELETE_FILE: "Move to the Trash or Recycle Bin",
    ActionType.SEND_MESSAGE: "Send a message to",
    ActionType.SUBMIT_FORM: "Submit",
    ActionType.PUBLISH_CONTENT: "Publish",
    ActionType.SUMMARIZE_GMAIL_EMAIL: "Summarize the open email in",
}


def classify_risk(
    action_type: ActionType | StructuredActionType,
    parameters: dict | None = None,
) -> RiskLevel:
    if isinstance(action_type, StructuredActionType):
        return structured_risk(action_type.value, parameters or {})
    if action_type in _HIGH_RISK_ACTIONS:
        return RiskLevel.HIGH
    if action_type in _MEDIUM_RISK_ACTIONS:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def describe_action(
    action_type: ActionType | StructuredActionType,
    target: str,
    parameters: dict,
) -> str:
    if isinstance(action_type, StructuredActionType):
        if action_type is StructuredActionType.SPREADSHEET_WRITE_CELL:
            cell = str(parameters.get("cell", "the requested cell"))
            sheet = parameters.get("sheet")
            location = f"{sheet}!{cell}" if sheet else f"cell {cell}"
            if bool(parameters.get("overwrite")):
                return (
                    f"Overwrite {location} in {target}; its existing value may be "
                    "replaced."
                )
            return (
                f"Write to {location} in {target}; if it already contains data, "
                "stop without overwriting it."
            )
        friendly = action_type.value.replace(".", " ").replace("_", " ")
        return f"{friendly.capitalize()} for {target}."
    if action_type is ActionType.OPEN_URL:
        browser = parameters.get("browser", "your default browser")
        return f"Open {target} in {browser}."
    if action_type is ActionType.COPY_FILE_CONTENT:
        destination = parameters.get("destination", "the destination file")
        return f"Copy the contents of {target} to {destination}."
    verb = _ACTION_VERBS[action_type]
    return f"{verb} {target}."


def _reject_hallucinated_paths(draft: DraftPlan) -> None:
    """Fail a draft whose paths cannot be what the user meant.

    Both checks catch classes of model hallucination that would otherwise only
    surface as a confusing execution failure: an invented user-profile directory,
    and an extension that contradicts the action family (a `.docx` target on a
    spreadsheet action, for example).
    """
    for action in draft.actions:
        candidates = [action.target]
        candidates.extend(
            value
            for key in sorted(PATH_LIKE_PARAMETERS)
            if isinstance(value := action.parameters.get(key), str)
        )
        for candidate in candidates:
            fabricated = find_fabricated_user_profile_path(candidate)
            if fabricated is not None:
                raise InvalidPlannerResponseError(
                    "The planner invented an absolute user-profile path "
                    f"({fabricated!r}). Ask again naming a Desktop, Documents, or "
                    "Downloads location."
                )
        mismatch = find_extension_family_mismatch(
            action.type,
            action.target,
            action.parameters,
        )
        if mismatch is not None:
            raise InvalidPlannerResponseError(
                f"The planner proposed a mismatched file type: {mismatch}"
            )


def _ground_spoken_filenames(request_text: str, draft: DraftPlan) -> DraftPlan:
    """Copy a draft with conservatively grounded action target basenames."""
    payload = draft.model_dump(mode="json")
    revised = False
    for action, action_payload in zip(
        draft.actions,
        payload["actions"],
        strict=True,
    ):
        corrected = ground_spoken_filename(
            request_text,
            action.type,
            action.target,
        )
        if corrected is None:
            continue
        action_payload["target"] = corrected
        revised = True
        _LOGGER.info(
            "plan_revised: grounded filename for %s: %s -> %s",
            action.step_key,
            action.target,
            corrected,
            extra={
                "event": "plan_revised",
                "outcome": "filename_grounded",
                "step_key": action.step_key,
                "requested_target": action.target,
                "corrected_target": corrected,
            },
        )
    if not revised:
        return draft
    return DraftPlan.model_validate(payload)


def _targets_read_by_plan(actions: list[DraftAction]) -> set[str]:
    """Normalised targets that some step of this plan needs to already exist.

    Used to tell a genuine "create this file" step apart from one that is really
    filling in a file the plan has already read. A step that creates its target
    keeps the path it was given unless the plan itself shows the file is expected
    to be there.
    """
    return {
        os.path.normcase(action.target)
        for action in actions
        if isinstance(action.type, StructuredActionType)
        and action.type.value in REQUIRES_EXISTING_TARGET_ACTIONS
    }


def _resolve_target_for_plan(
    action_type: str,
    target: str,
    *,
    read_elsewhere_in_plan: bool,
) -> tuple[str, str | None]:
    """Point a step at the file it meant, while the plan is still being built.

    Delegates the bounded search to `resolve_plan_target` rather than repeating
    it, so the depth, directory-budget, candidate, symlink/junction, hidden-tree
    and containment limits all remain defined in exactly one place.

    Several possible files is not something to resolve or to re-prompt the model
    about — only the user knows which folder they meant — so it fails closed here
    with the candidates named.
    """
    try:
        return resolve_plan_target(
            action_type, target, read_elsewhere_in_plan=read_elsewhere_in_plan
        )
    except ValueError as exc:
        raise InvalidPlannerResponseError(str(exc)) from exc


def build_action_plan(request: TaskRequest, draft: DraftPlan) -> ActionPlan:
    """Turn an untrusted model draft into an execution-engine contract."""
    # Ground the basename before path resolution and before any description,
    # confirmation decision, or confirmation hash derives from the target.
    draft = _ground_spoken_filenames(request.text, draft)
    _reject_hallucinated_paths(draft)
    if platform.system() == "Windows":
        unsupported = [
            action.type.value
            for action in draft.actions
            if action.type in MAC_ONLY_LEGACY_ACTIONS
        ]
        if unsupported:
            names = ", ".join(sorted(unsupported))
            raise InvalidPlannerResponseError(
                f"The planner proposed macOS-only action(s) on Windows: {names}"
            )

    request_text = request.text.casefold()
    has_file_open = any(
        action.type is ActionType.OPEN_FILE for action in draft.actions
    )
    url_browsers = {
        browser.casefold()
        for action in draft.actions
        if action.type is ActionType.OPEN_URL
        and isinstance((browser := action.parameters.get("browser")), str)
    }
    actions_to_build = [
        action
        for action in draft.actions
        if not (
            has_file_open
            and action.type
            in {ActionType.OPEN_APPLICATION, ActionType.FOCUS_APPLICATION}
            and action.target.casefold() not in request_text
        )
        and not (
            url_browsers
            and action.type
            in {ActionType.OPEN_APPLICATION, ActionType.FOCUS_APPLICATION}
            and action.target.casefold() in url_browsers
        )
    ]
    retained_keys = {action.step_key for action in actions_to_build}
    key_to_id: dict[str, UUID] = {
        action.step_key: uuid4() for action in actions_to_build
    }
    read_targets = _targets_read_by_plan(actions_to_build)
    actions: list[Action] = []
    for sequence, action in enumerate(actions_to_build, start=1):
        action_type = action.type.value
        # Resolve the target BEFORE risk, the confirmation summary, and the
        # confirmation hash are derived, so all three describe the file that will
        # actually be touched. Doing this at execution time instead would let a
        # user approve one file and have another one changed.
        target, resolved_from = _resolve_target_for_plan(
            action_type,
            action.target,
            read_elsewhere_in_plan=os.path.normcase(action.target) in read_targets,
        )
        # Drop optional parameters the planner filled in as null BEFORE anything
        # derives from them. Everything below — risk, the confirmation decision,
        # the summary the user reads, the confirmation hash, and the parameters
        # handed to the executor — uses this one dict, so what the user approves is
        # exactly what runs. Stripping after the hash would reopen the
        # confirm-one-thing-do-another hole.
        parameters = strip_absent_optional_parameters(action_type, action.parameters)
        if isinstance(action.type, StructuredActionType):
            requires_confirmation = structured_confirmation_required(
                action_type,
                parameters,
            )
        else:
            requires_confirmation = action.type in _CONFIRMATION_ACTIONS
        confirmation_hash = (
            action_identity_hash(action_type, target, parameters)
            if requires_confirmation
            else None
        )
        actions.append(
            Action(
                action_id=key_to_id[action.step_key],
                sequence=sequence,
                step_key=action.step_key,
                type=action.type,
                target=target,
                resolved_from=resolved_from,
                description=(
                    describe_action(action.type, target, parameters)
                    if action.type is StructuredActionType.SPREADSHEET_WRITE_CELL
                    else action.description.strip()
                    or describe_action(action.type, target, parameters)
                ),
                parameters=parameters,
                depends_on=[
                    key_to_id[key]
                    for key in action.depends_on
                    if key in retained_keys
                ],
                risk=classify_risk(action.type, parameters),
                requires_confirmation=requires_confirmation,
                confirmation_hash=confirmation_hash,
                expected_result=action.expected_result,
            )
        )
    return ActionPlan(
        plan_id=uuid4(),
        request_id=request.request_id,
        summary=draft.summary,
        actions=actions,
    )
