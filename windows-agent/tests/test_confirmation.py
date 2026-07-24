"""Milestone 12 — confirmation tokens.

Covers the anti-injection primitives directly: a stable/canonical
``action_hash`` and a ``ConfirmationStore`` whose tokens are single-use,
TTL-bounded, and bound to the EXACT action they were minted for. Reuse, expiry,
and a mutated action must all fail closed.
"""

from windows_agent.contracts import Action
from windows_agent.policy import ConfirmationStore, action_hash


def _action(type_: str = "file.delete", target: str = "C:/work/report.tmp", **params) -> Action:
    return Action(
        action_id="a1", task_id="t1", sequence=0, type=type_,
        target=target, parameters=params, reason="test",
    )


# ── action_hash ─────────────────────────────────────────────────────────────
def test_action_hash_is_deterministic_and_key_order_independent():
    a1 = _action("spreadsheet.write_cell", cell="A1", value=1, overwrite=True)
    a2 = _action("spreadsheet.write_cell", overwrite=True, value=1, cell="A1")  # different insertion order
    assert action_hash(a1) == action_hash(a1)  # stable across calls
    assert action_hash(a1) == action_hash(a2)  # independent of parameter key order


def test_action_hash_changes_with_type_target_or_params():
    base = _action("file.delete", target="C:/x")
    assert action_hash(base) != action_hash(_action("file.move", target="C:/x"))          # type
    assert action_hash(base) != action_hash(_action("file.delete", target="C:/y"))         # target
    assert action_hash(base) != action_hash(_action("file.delete", target="C:/x", missing_ok=True))  # params


# ── ConfirmationStore lifecycle ─────────────────────────────────────────────
def test_mint_and_validate_happy_path():
    store = ConfirmationStore()
    action = _action()
    token = store.mint(action_hash(action))
    assert store.validate(token, action) is True


def test_token_is_single_use():
    store = ConfirmationStore()
    action = _action()
    token = store.mint(action_hash(action))
    assert store.validate(token, action) is True
    assert store.validate(token, action) is False  # replay fails
    assert store.is_used(token) is True


def test_expired_token_fails():
    clock = {"now": 1000.0}
    store = ConfirmationStore(ttl_seconds=30.0, clock=lambda: clock["now"])
    action = _action()
    token = store.mint(action_hash(action))
    clock["now"] += 31.0  # advance past the TTL
    assert store.is_expired(token) is True
    assert store.validate(token, action) is False


def test_token_valid_within_ttl():
    clock = {"now": 1000.0}
    store = ConfirmationStore(ttl_seconds=30.0, clock=lambda: clock["now"])
    action = _action()
    token = store.mint(action_hash(action))
    clock["now"] += 29.0  # still inside the window
    assert store.is_expired(token) is False
    assert store.validate(token, action) is True


def test_mutated_action_fails_and_does_not_burn_token():
    """The confused-deputy defense: approve X, attacker swaps in X'. Validation
    fails on the hash mismatch, and (crucially) the failed attempt does NOT
    consume the token, so the legitimate action can still be confirmed."""
    store = ConfirmationStore()
    approved = _action("file.delete", target="C:/work/report.tmp")
    token = store.mint(action_hash(approved))
    mutated = _action("file.delete", target="C:/work/payroll.xlsx")
    assert store.validate(token, mutated) is False
    assert store.validate(token, approved) is True  # untouched by the failed attempt


def test_unknown_token_fails():
    store = ConfirmationStore()
    assert store.validate("no-such-token", _action()) is False
