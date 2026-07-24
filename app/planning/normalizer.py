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


def classify_risk(action_type: ActionType) -> RiskLevel:
    if action_type in _HIGH_RISK_ACTIONS:
        return RiskLevel.HIGH
    if action_type in _MEDIUM_RISK_ACTIONS:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def build_action_plan(request: TaskRequest, draft: DraftPlan) -> ActionPlan:
    """Turn an untrusted model draft into an execution-engine contract."""
    key_to_id: dict[str, UUID] = {
        action.step_key: uuid4() for action in draft.actions
    }
    actions = [
        Action(
            action_id=key_to_id[action.step_key],
            sequence=sequence,
            type=action.type,
            target=action.target,
            parameters=action.parameters,
            depends_on=[key_to_id[key] for key in action.depends_on],
            risk=classify_risk(action.type),
            requires_confirmation=action.type in _CONFIRMATION_ACTIONS,
            expected_result=action.expected_result,
        )
        for sequence, action in enumerate(draft.actions, start=1)
    ]
    return ActionPlan(
        plan_id=uuid4(),
        request_id=request.request_id,
        summary=draft.summary,
        actions=actions,
    )

