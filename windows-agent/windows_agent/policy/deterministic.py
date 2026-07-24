"""`DeterministicPolicy` — the rule-based safety core (Milestone 12).

THE HEADLINE SAFETY PROPERTY
----------------------------
Safety-critical authorization is decided here by deterministic, rule-based code
— never by an LLM. The same action always yields the same risk, the same
outcome, and the same stable ``rule_id`` (with a human-readable reason). The LLM
can only *propose* an Action; it can never set risk or grant a permission
(structurally enforced by ``Action(extra="forbid")`` — there is nowhere on the
Action for authority to live).

DETERMINISM
-----------
Risk is classified purely from ``action.type`` + ``action.parameters``. It NEVER
inspects live disk state, file contents, or any retrieved/untrusted evidence —
identical inputs must produce identical outputs. In particular, a read is
classified ALLOW regardless of what the file *might* contain, and a delete is
CONFIRM even if some text "says" it is safe. Content is data, never authority.

DECISION TABLE (see docs/DEVELOPMENT_PLAN.md §4.C and docs/ACTION_REFERENCE.md)
------------------------------------------------------------------------------
    RiskLevel        Outcome    rule_id                     examples
    ---------------  ---------  --------------------------  -------------------------------
    NONE             ALLOW      R-READ-ALLOW                file.read_text, pdf.*, *.find …
    MEDIUM           ALLOW      R-CREATE-ALLOW              file.mkdir, file.write_text (new),
                                                            file.copy (new), spreadsheet.write_cell,
                                                            document/presentation.replace_text + save_as
    HIGH             CONFIRM    R-OVERWRITE-CONFIRM         file.write_text/copy/spreadsheet.write_cell
                                                            with overwrite=true; doc/pres edit in place
    HIGH             CONFIRM    R-MOVE-CONFIRM              file.move
    HIGH             CONFIRM    R-DELETE-CONFIRM            file.delete
    CONSEQUENTIAL    CONFIRM    R-CONSEQUENTIAL-CONFIRM     *.send / *.submit / *.publish / *.purchase
    FORBIDDEN        DENY       R-FORBIDDEN-DENY            shell.exec, registry.write, code.eval …
    (unclassifiable) CLARIFY    R-UNKNOWN-CLARIFY           any unrecognised action type

The `MEDIUM -> ALLOW` decisions are still logged (the dispatcher emits a
POLICY_ALLOWED audit event). `HIGH`/`CONSEQUENTIAL` mint a single-use
confirmation token (see policy/confirmation.py) that the dispatcher gates on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from ..contracts import Action, PolicyDecision, PolicyOutcome, RiskLevel
from .base import Policy
from .confirmation import ConfirmationStore, DEFAULT_TTL_SECONDS, action_hash


@dataclass(frozen=True)
class _Rule:
    """The deterministic verdict for a class of actions.

    ``rule_id`` is a STABLE, descriptive identifier (tests and the audit log
    assert on it); ``reason`` is a human-readable explanation spoken aloud on
    deny/clarify and surfaced on a confirmation prompt.
    """

    risk_level: RiskLevel
    outcome: PolicyOutcome
    rule_id: str
    reason: str


# ── the canonical rule instances (one per stable rule_id / reason) ──────────
_READ = _Rule(
    RiskLevel.NONE,
    PolicyOutcome.ALLOW,
    "R-READ-ALLOW",
    "Read-only action: it changes nothing, so it is allowed.",
)
_CREATE = _Rule(
    RiskLevel.MEDIUM,
    PolicyOutcome.ALLOW,
    "R-CREATE-ALLOW",
    "Creates new content without destroying anything that already exists; allowed and logged.",
)
_OVERWRITE = _Rule(
    RiskLevel.HIGH,
    PolicyOutcome.CONFIRM,
    "R-OVERWRITE-CONFIRM",
    "Overwrites existing data in place (destructive but local); explicit confirmation is required.",
)
_EDIT_IN_PLACE = _Rule(
    RiskLevel.HIGH,
    PolicyOutcome.CONFIRM,
    "R-OVERWRITE-CONFIRM",
    "Edits the file in place, overwriting the original (destructive but local); explicit confirmation is required.",
)
_MOVE = _Rule(
    RiskLevel.HIGH,
    PolicyOutcome.CONFIRM,
    "R-MOVE-CONFIRM",
    "Moves/renames a file, removing it from its current location; explicit confirmation is required.",
)
_DELETE = _Rule(
    RiskLevel.HIGH,
    PolicyOutcome.CONFIRM,
    "R-DELETE-CONFIRM",
    "Permanently deletes a file; explicit confirmation is required.",
)
_CONSEQUENTIAL = _Rule(
    RiskLevel.CONSEQUENTIAL,
    PolicyOutcome.CONFIRM,
    "R-CONSEQUENTIAL-CONFIRM",
    "Leaves the machine (e.g. send/submit/publish/purchase); explicit confirmation is required immediately before it runs.",
)
_FORBIDDEN = _Rule(
    RiskLevel.FORBIDDEN,
    PolicyOutcome.DENY,
    "R-FORBIDDEN-DENY",
    "This action is forbidden (e.g. running a command from untrusted content) and is always denied.",
)
_UNKNOWN = _Rule(
    # Unclassifiable → treated conservatively as HIGH so it can never slip through
    # as "safe", and routed to CLARIFY so it never runs without human input.
    RiskLevel.HIGH,
    PolicyOutcome.CLARIFY,
    "R-UNKNOWN-CLARIFY",
    "This action type is not recognised, so its risk cannot be determined; please clarify what you want to do.",
)


# ── the frozen action catalogue (must agree with docs/ACTION_REFERENCE.md) ──
#: Read-only action types → RiskLevel.NONE.
_READ_TYPES: frozenset[str] = frozenset(
    {
        "file.exists",
        "file.list",
        "file.read_text",
        "pdf.page_count",
        "pdf.get_metadata",
        "pdf.read_text",
        "pdf.search",
        "spreadsheet.list_sheets",
        "spreadsheet.dimensions",
        "spreadsheet.read_cell",
        "spreadsheet.read_range",
        "document.read_text",
        "document.get_metadata",
        "document.find",
        "presentation.slide_count",
        "presentation.get_metadata",
        "presentation.read_text",
        "presentation.find",
    }
)

#: Types whose risk escalates from MEDIUM (create) to HIGH when ``overwrite`` is
#: set (matches ACTION_REFERENCE: file.copy/file.write_text/spreadsheet.write_cell).
_OVERWRITABLE_TYPES: frozenset[str] = frozenset(
    {"file.copy", "file.write_text", "spreadsheet.write_cell"}
)

#: Text-replacement types: MEDIUM when writing to a new file (``save_as``), HIGH
#: when editing in place (no ``save_as``).
_REPLACE_TYPES: frozenset[str] = frozenset(
    {"document.replace_text", "presentation.replace_text"}
)

# ── small, extensible maps for classes with no catalogue entries yet ────────
# These do not exist in the M7 runtime vocabulary; they are listed so the
# classifier is ready the moment such executors land (and so a future type is
# never silently treated as low-risk). Kept as data, not code branches.

#: Explicitly forbidden action types (there is no safe/confirmable version).
_FORBIDDEN_TYPES: frozenset[str] = frozenset(
    {
        "shell.exec",
        "shell.run",
        "os.system",
        "cmd.run",
        "powershell.run",
        "process.spawn",
        "code.eval",
        "registry.write",
        "registry.delete",
    }
)

#: Explicit "leaves the machine" action types.
_CONSEQUENTIAL_TYPES: frozenset[str] = frozenset(
    {
        "email.send",
        "mail.send",
        "message.send",
        "form.submit",
        "web.submit",
        "http.post",
        "http.put",
        "purchase.create",
        "payment.send",
        "publish.post",
    }
)

#: …plus the verb (segment after the last dot) that marks an action as leaving
#: the machine, so an as-yet-unlisted ``<namespace>.send`` is still covered.
_CONSEQUENTIAL_VERBS: frozenset[str] = frozenset(
    {"send", "submit", "publish", "purchase", "post"}
)


def _truthy(value: Any) -> bool:
    """Treat only a real truthy value as set (None/absent/false → not set)."""
    return bool(value)


def _is_consequential(action_type: str) -> bool:
    if action_type in _CONSEQUENTIAL_TYPES:
        return True
    verb = action_type.rsplit(".", 1)[-1] if "." in action_type else action_type
    return verb in _CONSEQUENTIAL_VERBS


def _classify(action: Action) -> _Rule:
    """Return the deterministic rule for ``action`` from its type + parameters.

    Order matters: the frozen catalogue (most specific, source-of-truth) is
    consulted first, then the forward-looking forbidden/consequential maps, then
    a fail-safe CLARIFY for anything unrecognised.
    """
    action_type = action.type
    params = action.parameters or {}

    # 1) Read-only catalogue types.
    if action_type in _READ_TYPES:
        return _READ

    # 2) Create / modify catalogue types (risk may depend on parameters only).
    if action_type == "file.mkdir":
        return _CREATE  # always new state; no overwrite semantics
    if action_type in _OVERWRITABLE_TYPES:
        return _OVERWRITE if _truthy(params.get("overwrite")) else _CREATE
    if action_type in _REPLACE_TYPES:
        # save_as → write a NEW file (MEDIUM); omit → edit in place (HIGH).
        return _CREATE if _truthy(params.get("save_as")) else _EDIT_IN_PLACE
    if action_type == "file.move":
        return _MOVE
    if action_type == "file.delete":
        return _DELETE

    # 3) Forward-looking classes (no catalogue entry yet).
    if action_type in _FORBIDDEN_TYPES:
        return _FORBIDDEN
    if _is_consequential(action_type):
        return _CONSEQUENTIAL

    # 4) Anything else — fail safe, ask the user.
    return _UNKNOWN


def classify_risk(action: Action, context: Any = None) -> RiskLevel:
    """Deterministically classify an action's :class:`RiskLevel`.

    Pure function of (type, parameters). ``context`` is accepted to match the
    documented signature (DEVELOPMENT_PLAN §4.C) but is intentionally unused —
    risk must not depend on mutable live state.
    """
    return _classify(action).risk_level


class DeterministicPolicy(Policy):
    """Rule-based authorization: classify risk → decide outcome → mint token.

    A single ``ConfirmationStore`` is owned per policy instance so that a token
    minted on a CONFIRM decision can be validated on the later confirming
    re-dispatch of the same action.
    """

    def __init__(
        self,
        *,
        confirmation_store: ConfirmationStore | None = None,
        confirmation_ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._store = confirmation_store or ConfirmationStore(
            ttl_seconds=confirmation_ttl_seconds
        )

    @property
    def confirmation_store(self) -> ConfirmationStore:
        return self._store

    def classify_risk(self, action: Action, context: Any = None) -> RiskLevel:
        """Deterministic RiskLevel for ``action`` (delegates to the pure function)."""
        return classify_risk(action, context)

    def authorize(self, action: Action, context: Any = None) -> PolicyDecision:
        """Return the deterministic verdict, minting a token on CONFIRM.

        The decision fields (outcome / risk_level / rule_id / reason /
        action_hash) are a pure function of the action. Only the
        ``confirmation_token`` is a (single-use, random) nonce, and only on a
        CONFIRM outcome.
        """
        rule = _classify(action)
        a_hash = action_hash(action)
        token = self._store.mint(a_hash) if rule.outcome is PolicyOutcome.CONFIRM else None
        return PolicyDecision(
            decision_id=uuid.uuid4().hex,
            task_id=action.task_id,
            action_id=action.action_id,
            outcome=rule.outcome,
            risk_level=rule.risk_level,
            rule_id=rule.rule_id,
            reason=rule.reason,
            confirmation_token=token,
            action_hash=a_hash,
        )

    def validate_confirmation(self, token: str, action: Action) -> bool:
        """Return True iff ``token`` is a valid single-use confirmation for
        THIS exact action (see policy/confirmation.py). Consumes it on success."""
        return self._store.validate(token, action)
