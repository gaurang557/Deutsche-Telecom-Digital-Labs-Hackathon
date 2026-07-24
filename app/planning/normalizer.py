import platform
from uuid import UUID, uuid4

from app.planning.capabilities import (
    find_extension_family_mismatch,
    find_fabricated_user_profile_path,
)
from app.planning.exceptions import InvalidPlannerResponseError
from app.schemas import (
    Action,
    ActionPlan,
    ActionType,
    DraftPlan,
    RiskLevel,
    StructuredActionType,
    TaskRequest,
)
from app.structured_actions import (
    MAC_ONLY_LEGACY_ACTIONS,
    action_identity_hash,
    structured_confirmation_required,
    structured_risk,
)

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
            for key in ("destination", "save_as")
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


def build_action_plan(request: TaskRequest, draft: DraftPlan) -> ActionPlan:
    """Turn an untrusted model draft into an execution-engine contract."""
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
    actions: list[Action] = []
    for sequence, action in enumerate(actions_to_build, start=1):
        action_type = action.type.value
        if isinstance(action.type, StructuredActionType):
            requires_confirmation = structured_confirmation_required(
                action_type,
                action.parameters,
            )
        else:
            requires_confirmation = action.type in _CONFIRMATION_ACTIONS
        confirmation_hash = (
            action_identity_hash(action_type, action.target, action.parameters)
            if requires_confirmation
            else None
        )
        actions.append(
            Action(
                action_id=key_to_id[action.step_key],
                sequence=sequence,
                step_key=action.step_key,
                type=action.type,
                target=action.target,
                description=action.description.strip()
                or describe_action(action.type, action.target, action.parameters),
                parameters=action.parameters,
                depends_on=[
                    key_to_id[key]
                    for key in action.depends_on
                    if key in retained_keys
                ],
                risk=classify_risk(action.type, action.parameters),
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
