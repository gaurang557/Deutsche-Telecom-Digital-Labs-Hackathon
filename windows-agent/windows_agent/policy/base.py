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

    def validate_confirmation(self, token: str, action: Action) -> bool:
        """Return True iff `token` is a valid single-use confirmation for THIS
        exact `action`.

        A CONFIRM decision from `authorize` is only honoured by the dispatcher
        when a valid confirmation token is presented on a later re-dispatch. The
        deterministic engine (see `policy/deterministic.py`) overrides this to
        check a single-use, TTL-bounded, action-bound token. The default here is
        the safe one — no policy grants a confirmation implicitly — so mocks and
        any future policy without a confirmation store fail closed (a CONFIRM
        decision keeps requesting confirmation rather than ever auto-proceeding).
        """
        return False
