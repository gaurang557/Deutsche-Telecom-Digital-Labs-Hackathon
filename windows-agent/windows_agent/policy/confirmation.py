"""Confirmation tokens — the anti-injection core of the safety engine (M12).

WHY THIS EXISTS
---------------
A `HIGH`/`CONSEQUENTIAL` action is allowed to run only after an explicit human
confirmation. The danger is a *confused-deputy / prompt-injection* attack: the
user approves "delete report.tmp", but by the time execution happens the action
has been mutated to "delete payroll.xlsx". To make that impossible we do NOT
trust a boolean "confirmed" flag — we bind every confirmation to the EXACT
action it approved.

TWO PRIMITIVES
--------------
* ``action_hash(action)`` — a stable, canonical fingerprint over the action's
  identity-relevant parts (type, target, sorted parameters). Deterministic:
  identical actions hash identically; any change to type/target/parameters
  changes the hash.
* ``ConfirmationStore`` — mints single-use tokens bound to an action_hash with a
  created-at timestamp and a TTL. ``validate(token, action)`` returns True *only*
  if the token exists, is unused, is not expired, AND ``action_hash(action)``
  matches the hash the token was minted for. On success the token is burned
  (marked used). Reuse, expiry, or a mutated action all fail closed.

SCOPE
-----
In-memory only for the MVP — persistence (a redacting, SQLite-backed store) is
teammate-owned (M11) and slots in behind the same shape. The deterministic
guarantee of the policy covers the *decision* (outcome / risk / rule_id /
action_hash); the token itself is deliberately a random single-use nonce.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from ..contracts import Action

#: Default time-to-live for a minted confirmation token. Confirmations are meant
#: to be granted immediately before execution, so this is deliberately short.
DEFAULT_TTL_SECONDS: float = 300.0  # 5 minutes


def action_hash(action: Action) -> str:
    """Stable SHA-256 over the identity-relevant parts of an action.

    Canonical + deterministic: parameters are serialised with ``sort_keys`` so
    key ordering never affects the hash, and only (type, target, parameters)
    participate — NOT ``action_id``/``sequence``/``reason`` (those may legitimately
    differ between the proposal and the confirmed re-dispatch of the same work).

    Changing type/target/parameters changes the hash, which is exactly what
    invalidates a stale/mismatched confirmation.
    """
    payload = json.dumps(
        {"type": action.type, "target": action.target, "parameters": action.parameters},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class _TokenEntry:
    """Internal record for one minted token."""

    action_hash: str
    created_at: float
    ttl: float
    used: bool = False


class ConfirmationStore:
    """In-memory single-use confirmation tokens bound to an ``action_hash``.

    A token is valid for exactly one successful ``validate`` call, only within
    its TTL, and only for the exact action it was minted for. Everything else
    fails closed.

    ``clock`` is injectable purely for deterministic testing of expiry; it
    defaults to ``time.monotonic`` (immune to wall-clock adjustments).
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = float(ttl_seconds)
        self._clock = clock
        self._tokens: dict[str, _TokenEntry] = {}

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def mint(self, action_hash_value: str) -> str:
        """Mint a fresh single-use token bound to ``action_hash_value``.

        The token is an opaque random nonce; callers store it in the
        PolicyDecision and present it back on the confirming re-dispatch.
        """
        token = uuid.uuid4().hex
        self._tokens[token] = _TokenEntry(
            action_hash=action_hash_value,
            created_at=self._clock(),
            ttl=self._ttl,
        )
        return token

    def validate(self, token: str, action: Action) -> bool:
        """Return True iff ``token`` authorizes THIS exact ``action`` right now.

        Fails closed (returns False, without consuming the token) when the token
        is unknown, already used, expired, or bound to a different action. On the
        single success it burns the token so it can never be replayed.
        """
        entry = self._tokens.get(token)
        if entry is None:
            return False
        if entry.used:
            return False
        if (self._clock() - entry.created_at) > entry.ttl:
            return False
        # The anti-injection check: the presented action must be byte-for-byte the
        # action that was confirmed. A mutated action produces a different hash.
        if entry.action_hash != action_hash(action):
            return False
        entry.used = True  # single-use: burn it only on a genuine match
        return True

    def is_used(self, token: str) -> bool:
        """Whether ``token`` has already been consumed (unknown tokens → False)."""
        entry = self._tokens.get(token)
        return bool(entry and entry.used)

    def is_expired(self, token: str) -> bool:
        """Whether ``token`` exists but is past its TTL (unknown tokens → False)."""
        entry = self._tokens.get(token)
        if entry is None:
            return False
        return (self._clock() - entry.created_at) > entry.ttl

    def __len__(self) -> int:
        return len(self._tokens)
