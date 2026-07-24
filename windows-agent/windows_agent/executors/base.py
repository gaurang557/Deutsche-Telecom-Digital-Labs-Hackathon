"""`BaseExecutor` — the async contract every executor implements.

WHAT AN EXECUTOR IS
-------------------
An executor DOES the work for one or more semantic action types (e.g. a future
`FileExecutor` handles file.copy/file.move/...). The mapping from action type to
executor lives in the ActionRegistry, not here.

WHY ASYNC (even though M0 does no I/O)
--------------------------------------
Real executors will be I/O-bound: reading files, parsing PDFs, driving a browser
or the Windows desktop. Making the interface `async` from the start means we
never have to rewrite every executor + the dispatcher to add concurrency later.
Retrofitting sync -> async is a large, error-prone change; paying the small cost
now avoids it.

WHY RETURN `ExecutorResult` (not a bool)
----------------------------------------
Executors must report structured facts: what happened (evidence), what changed
(side_effects), and a typed error on failure. A bare bool can't carry any of
that, and "returned without raising" is not proof of success.

WHAT AN EXECUTOR MUST NOT DO
----------------------------
It must NOT decide permissions, ask for confirmation, or run verification. Those
are separate deterministic stages owned by the Policy Engine / Dispatcher /
Verification layers. Keeping executors "dumb" is what lets the dispatcher apply
safety uniformly to all of them.
"""

from __future__ import annotations

import abc

from ..contracts import Action, ExecutorResult


class BaseExecutor(abc.ABC):
    #: Human-friendly name for diagnostics/registration convenience only.
    name: str = "base"

    @abc.abstractmethod
    async def execute(self, action: Action) -> ExecutorResult:
        """Perform the action and return bounded evidence + side effects.

        Implementations should catch their own expected errors and return an
        ExecutorResult(success=False, error=...). Unexpected exceptions are still
        safe: the Dispatcher contains them and converts to a FAILED result.
        """
        raise NotImplementedError
