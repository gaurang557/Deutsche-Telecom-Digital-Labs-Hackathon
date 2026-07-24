"""`Policy` — the authorization interface the dispatcher depends on.

Kept deliberately narrow: given an Action (and optional execution context),
return exactly one PolicyDecision. Implementations MUST be deterministic and
side-effect free — the same inputs always produce the same decision. The
dispatcher never authorizes on its own; it only obeys the returned decision.

`authorize` is synchronous because a policy is pure rule evaluation (no I/O).
"""

from __future__ import annotations

import abc
from typing import Any

from ..contracts import Action, PolicyDecision


class Policy(abc.ABC):
    @abc.abstractmethod
    def authorize(self, action: Action, context: Any = None) -> PolicyDecision:
        """Return the deterministic authorization verdict for `action`."""
        raise NotImplementedError
