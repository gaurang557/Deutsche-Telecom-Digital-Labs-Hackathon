from threading import Lock
from uuid import UUID

from app.schemas import ActionPlan


class PlanRepository:
    """Process-local plan storage for the MVP execution handoff."""

    def __init__(self) -> None:
        self._plans: dict[UUID, ActionPlan] = {}
        self._execution_claims: set[UUID] = set()
        self._lock = Lock()

    def save(self, plan: ActionPlan) -> None:
        with self._lock:
            self._plans[plan.plan_id] = plan

    def get(self, plan_id: UUID) -> ActionPlan | None:
        with self._lock:
            return self._plans.get(plan_id)

    def claim_execution(self, plan_id: UUID) -> bool:
        """Ensure a plan cannot be executed twice by repeated UI submissions."""
        with self._lock:
            if plan_id in self._execution_claims:
                return False
            self._execution_claims.add(plan_id)
            return True
