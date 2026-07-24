"""`Verifier` — independently confirms an action's effect.

A verifier receives the Action and the ExecutorResult and RE-OBSERVES real
state (e.g. reopen a file, re-read a cell) to produce a VerificationResult.
It must NOT trust the executor's own in-memory view — "no exception" is not
proof of success. Async because verification usually does I/O.
"""

from __future__ import annotations

import abc
from typing import Any

from ..contracts import Action, ExecutorResult, VerificationResult


class Verifier(abc.ABC):
    @abc.abstractmethod
    async def verify(
        self, action: Action, result: ExecutorResult, context: Any = None
    ) -> VerificationResult:
        """Re-observe state and return whether the action truly succeeded."""
        raise NotImplementedError
