from app.planning.control import detect_control_intent
from app.planning.normalizer import build_action_plan
from app.planning.planner import Planner
from app.schemas import PlanningResponse, TaskRequest


class PlanningService:
    def __init__(self, planner: Planner) -> None:
        self._planner = planner

    async def plan(self, request: TaskRequest) -> PlanningResponse:
        control_intent = detect_control_intent(request.text)
        if control_intent is not None:
            return PlanningResponse(
                request_id=request.request_id,
                control_intent=control_intent,
            )

        draft = await self._planner.create_draft(request)
        return PlanningResponse(
            request_id=request.request_id,
            plan=build_action_plan(request, draft),
        )

