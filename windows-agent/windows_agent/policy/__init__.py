"""Policy gateway.

The `Policy` interface returns a deterministic `PolicyDecision` for an Action.
The real deterministic engine is integrated in Milestone 12; Milestone 1 ships
mock policies so the pipeline can be built and tested end-to-end now.
"""

from .base import Policy
from .mock import AllowAllPolicy, ConfigurablePolicy, action_hash

__all__ = ["Policy", "AllowAllPolicy", "ConfigurablePolicy", "action_hash"]
