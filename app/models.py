"""Backward-compatible imports for the team's shared API contracts.

New code should import these models from :mod:`app.schemas`.
"""

from app.schemas import TaskRequest

__all__ = ["TaskRequest"]
