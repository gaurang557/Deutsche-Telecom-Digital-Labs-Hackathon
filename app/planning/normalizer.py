from uuid import UUID, uuid4

from app.schemas import (
    Action,
    ActionPlan,
    ActionType,
    DraftPlan,
    RiskLevel,
    TaskRequest,
)

_HIGH_RISK_ACTIONS = {
    ActionType.DELETE_FILE,
    ActionType.SEND_MESSAGE,
    ActionType.SUBMIT_FORM,
    ActionType.PUBLISH_CONTENT,
}
_MEDIUM_RISK_ACTIONS = {
    ActionType.CREATE_FILE,
    ActionType.MOVE_FILE,
    ActionType.OVERWRITE_FILE,
}
_CONFIRMATION_ACTIONS = _HIGH_RISK_ACTIONS | {
    ActionType.MOVE_FILE,
    ActionType.OVERWRITE_FILE,
}

_ACTION_VERBS = {
    ActionType.OPEN_APPLICATION: "Open",
    ActionType.OPEN_FILE: "Find and open",
    ActionType.FOCUS_APPLICATION: "Bring into focus",
    ActionType.CLICK_ELEMENT: "Select",
    ActionType.TYPE_TEXT: "Enter text in",
    ActionType.PRESS_KEY: "Use a keyboard shortcut in",
    ActionType.READ_FILE: "Read",
    ActionType.CREATE_FILE: "Create",
    ActionType.MOVE_FILE: "Move",
    ActionType.OVERWRITE_FILE: "Update",
    ActionType.DELETE_FILE: "Move to the Trash or Recycle Bin",
    ActionType.SEND_MESSAGE: "Send a message to",
    ActionType.SUBMIT_FORM: "Submit",
    ActionType.PUBLISH_CONTENT: "Publish",
}


def classify_risk(action_type: ActionType) -> RiskLevel:
    if action_type in _HIGH_RISK_ACTIONS:
        return RiskLevel.HIGH
    if action_type in _MEDIUM_RISK_ACTIONS:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def describe_action(action_type: ActionType, target: str) -> str:
    verb = _ACTION_VERBS[action_type]
    return f"{verb} {target}."


def build_action_plan(request: TaskRequest, draft: DraftPlan) -> ActionPlan:
    """Turn an untrusted model draft into an execution-engine contract."""
    request_text = request.text.casefold()
    has_file_open = any(
        action.type is ActionType.OPEN_FILE for action in draft.actions
    )
    actions_to_build = [
        action
        for action in draft.actions
        if not (
            has_file_open
            and action.type
            in {ActionType.OPEN_APPLICATION, ActionType.FOCUS_APPLICATION}
            and action.target.casefold() not in request_text
        )
    ]
    retained_keys = {action.step_key for action in actions_to_build}
    key_to_id: dict[str, UUID] = {
        action.step_key: uuid4() for action in actions_to_build
    }
    actions = [
        Action(
            action_id=key_to_id[action.step_key],
            sequence=sequence,
            type=action.type,
            target=action.target,
            description=action.description.strip()
            or describe_action(action.type, action.target),
            parameters=action.parameters,
            depends_on=[
                key_to_id[key]
                for key in action.depends_on
                if key in retained_keys
            ],
            risk=classify_risk(action.type),
            requires_confirmation=action.type in _CONFIRMATION_ACTIONS,
            expected_result=action.expected_result,
        )
        for sequence, action in enumerate(actions_to_build, start=1)
    ]
    return ActionPlan(
        plan_id=uuid4(),
        request_id=request.request_id,
        summary=draft.summary,
        actions=actions,
    )
