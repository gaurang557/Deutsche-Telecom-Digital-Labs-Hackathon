from app.planning.capabilities import detect_unsupported_request
from app.planning.control import detect_control_intent
from app.planning.normalizer import build_action_plan
from app.planning.planner import Planner
from app.planning.repository import PlanRepository
from app.schemas import PlanningResponse, TaskRequest


class PlanningService:
    def __init__(self, planner: Planner, repository: PlanRepository) -> None:
        self._planner = planner
        self._repository = repository

    async def plan(self, request: TaskRequest) -> PlanningResponse:
        control_intent = detect_control_intent(request.text)
        if control_intent is not None:
            return PlanningResponse(
                request_id=request.request_id,
                control_intent=control_intent,
            )

        refusal = detect_unsupported_request(request.text)
        if refusal is not None:
            return PlanningResponse(
                request_id=request.request_id,
                refusal=refusal,
            )

        draft = await self._planner.create_draft(request)
        plan = build_action_plan(request, draft)
        self._repository.save(plan, request)
        return PlanningResponse(
            request_id=request.request_id,
            plan=plan,
        )
