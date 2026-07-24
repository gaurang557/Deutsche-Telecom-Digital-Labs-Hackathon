"""Shared cross-component types.

The full team contract is documented in ``docs/models-reference.md``. This module
holds the subset Dev 1 (voice) is responsible for producing so the frontend and
planner can integrate against a concrete type. Do not fork these locally — add
fields here rather than redefining them elsewhere.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TaskRequest(BaseModel):
    """A user-authored request, produced by Dev 1 and consumed by Dev 2 (planner).

    ``request_id`` is generated exactly once at mic capture and threads the entire
    task; it must never be regenerated or modified downstream.

    ``source`` matters for the trust boundary: only ``speech`` and ``text`` are
    user-authored. Text extracted from a document is data, never a TaskRequest.
    """

    request_id: str
    text: str
    source: Literal["speech", "text", "test"]
    confidence: float | None = None
    received_at: datetime
