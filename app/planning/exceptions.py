class PlannerError(Exception):
    """Base exception for planner failures."""


class PlannerUnavailableError(PlannerError):
    """Raised when the configured model service cannot be reached."""


class InvalidPlannerResponseError(PlannerError):
    """Raised when a model response does not satisfy the action schema."""

