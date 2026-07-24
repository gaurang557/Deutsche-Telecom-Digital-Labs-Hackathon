"""Policy gateway.

The `Policy` interface returns a deterministic `PolicyDecision` for an Action.
`DeterministicPolicy` (Milestone 12) is the real rule-based safety core; the
`AllowAllPolicy`/`ConfigurablePolicy` mocks (Milestone 1) remain for wiring,
demos, and the existing tests. `ConfirmationStore` + `action_hash` implement the
single-use, action-bound confirmation tokens that gate consequential actions.
"""

from .base import Policy
from .confirmation import ConfirmationStore, DEFAULT_TTL_SECONDS, action_hash
from .deterministic import DeterministicPolicy, classify_risk
from .mock import AllowAllPolicy, ConfigurablePolicy

__all__ = [
    "Policy",
    "AllowAllPolicy",
    "ConfigurablePolicy",
    "DeterministicPolicy",
    "classify_risk",
    "ConfirmationStore",
    "action_hash",
    "DEFAULT_TTL_SECONDS",
]
