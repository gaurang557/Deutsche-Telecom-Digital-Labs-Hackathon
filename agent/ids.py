"""Human-readable id generators for requests and actions.

Not uuid4 on purpose: these ids get read aloud and shown on a
projector during the demo, so short counters like "req1" and
"req1-s2" are more useful than "a1b2c3d4-...".
"""

import itertools
import string
import threading

# A single shared counter for the whole process. itertools.count(1) is an
# infinite iterator that yields 1, 2, 3, ... each time next() is called on it.
_request_counter = itertools.count(1)

# next() on a plain itertools.count is not guaranteed atomic if two threads
# call it at the exact same instant, so we serialise access with a lock.
_counter_lock = threading.Lock()


def new_request_id() -> str:
    """Return the next request id: "req1", "req2", "req3", ..."""
    with _counter_lock:
        n = next(_request_counter)
    return f"req{n}"


def make_action_id(request_id: str, step_index: int, revision: int | None = None) -> str:
    """Build an action id for one step of one request.

    make_action_id("req1", 2)        -> "req1-s2"    (the original attempt)
    make_action_id("req1", 2, 1)     -> "req1-s2a"    (first retry)
    make_action_id("req1", 2, 2)     -> "req1-s2b"    (second retry)

    `revision` is None for the original attempt at a step, and 1, 2, 3, ...
    for each retry after that. Keeping the original attempt's id free of a
    letter suffix means the common case (no retry) stays short.
    """
    base = f"{request_id}-s{step_index}"

    if revision is None:
        return base

    if revision < 1:
        raise ValueError("revision must be >= 1; the original attempt uses revision=None")

    # string.ascii_lowercase is "abcdefghijklmnopqrstuvwxyz"; revision=1 -> "a".
    if revision > len(string.ascii_lowercase):
        raise ValueError(
            f"revision {revision} exceeds the supported range "
            f"(max {len(string.ascii_lowercase)} retries)"
        )
    letter = string.ascii_lowercase[revision - 1]

    return f"{base}{letter}"
