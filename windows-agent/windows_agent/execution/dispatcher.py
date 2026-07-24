"""`Dispatcher` — the single, safe execution pipeline for an Action.

FULL PIPELINE (Milestone 1)
---------------------------
    Action
      → schema validation      (dicts are validated into an Action; invalid → FAILED)
      → cancellation check     (cancelled context → CANCELLED, executor never runs)
      → action registry        (unknown type → FAILED, fails closed)
      → policy gateway         (obey a deterministic PolicyDecision)
            DENY    → DENIED,             executor NOT called
            CONFIRM → NEEDS_CONFIRMATION, executor NOT called
            CLARIFY → CLARIFY,            executor NOT called
            ALLOW   → proceed
      → required-verifier guard (missing → FAILED, executor NOT called)
      → executor               (execute_authorized_action)
      → verification registry  (independent re-observation; only when execution succeeded)
      → ActionResult           (build_action_result; verification FAILED forces FAILED)
    with structured audit events emitted around each stage.

WHY ONE PIPELINE?
-----------------
Every action flows through `dispatch()`, so policy/verification/audit are wired
in exactly one place and can never be bypassed by an individual executor. The
dispatcher NEVER authorizes on its own — it only consumes a PolicyDecision.

FAIL-CLOSED GUARANTEES
----------------------
Invalid input, unknown action, executor exceptions, and verifier exceptions all
return a structured FAILED ActionResult. Consequential work never runs without
an ALLOW decision, and actions marked as requiring verification never start
without a registered verifier.
"""

from __future__ import annotations

from collections.abc import Mapping  # used for runtime isinstance checks
from typing import Any

from pydantic import ValidationError

from ..audit import AuditSink, NullAuditSink
from ..contracts import (
    Action,
    ActionError,
    ActionResult,
    ActionStatus,
    AuditEvent,
    AuditEventType,
    ErrorCode,
    ExecutorResult,
    PolicyDecision,
    PolicyOutcome,
    VerificationResult,
    VerificationStatus,
)
from ..policy.base import Policy
from ..verification.registry import VerificationRegistry
from .context import ExecutionContext
from .registry import ActionRegistry

_COMPONENT = "dispatcher"

# Evidence caps — never hand the planner whole PDFs/workbooks/DOM trees.
_MAX_STR = 2000
_MAX_ITEMS = 50


def _bound(value: Any, _depth: int = 0) -> Any:
    """Recursively shrink evidence to safe sizes (see module docstring)."""
    if _depth > 6:
        return "…"
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[:_MAX_STR] + "…[truncated]"
    if isinstance(value, dict):
        return {k: _bound(v, _depth + 1) for k, v in list(value.items())[:_MAX_ITEMS]}
    if isinstance(value, (list, tuple)):
        return [_bound(v, _depth + 1) for v in list(value)[:_MAX_ITEMS]]
    return value


class Dispatcher:
    def __init__(
        self,
        registry: ActionRegistry,
        policy: Policy,
        *,
        verification: VerificationRegistry | None = None,
        audit: AuditSink | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy  # required: the dispatcher must never self-authorize
        self._verification = verification or VerificationRegistry()
        self._audit = audit or NullAuditSink()

    # ── public entry point ─────────────────────────────────────────────────
    async def dispatch(self, action: Action | Mapping[str, Any], context: ExecutionContext | None = None) -> ActionResult:
        context = context or ExecutionContext()

        # 1) Schema validation (planner may hand us a dict).
        if not isinstance(action, Action):
            try:
                action = Action.model_validate(dict(action))
            except (ValidationError, TypeError, ValueError) as exc:
                data = dict(action) if isinstance(action, Mapping) else {}
                return ActionResult(
                    action_id=str(data.get("action_id", "unknown")),
                    task_id=str(data.get("task_id", "unknown")),
                    status=ActionStatus.FAILED,
                    error=ActionError(
                        code=ErrorCode.VALIDATION_ERROR.value,
                        message=f"Invalid Action: {exc}",
                        retryable=False,
                    ),
                )

        self._emit(AuditEventType.ACTION_PROPOSED, action, summary=f"proposed {action.type}")

        # 2) Cancellation boundary — do not start work on a cancelled run.
        if context.is_cancelled():
            self._emit(AuditEventType.ACTION_CANCELLED, action, outcome="cancelled",
                       summary="cancelled before start")
            return self._simple_result(action, ActionStatus.CANCELLED, ErrorCode.CANCELLED,
                                       "Run was cancelled before this action started.")

        # 3) Registry lookup — unknown type fails closed.
        registration = self._registry.get_action_registration(action.type)
        if registration is None:
            self._emit(AuditEventType.ACTION_FAILED, action, outcome="failed",
                       summary=f"unknown action {action.type}")
            return self._simple_result(
                action, ActionStatus.FAILED, ErrorCode.UNKNOWN_ACTION,
                f"No executor registered for action type {action.type!r}",
                details={"known_types": self._registry.list_registered_actions()},
            )
        handler = registration.handler

        # 4) Policy gateway — obey the deterministic decision.
        decision: PolicyDecision = self._policy.authorize(action, context)
        gated = self._apply_policy(action, decision)
        if gated is not None:
            return gated  # DENY / CONFIRM / CLARIFY short-circuit here

        # 5) Required-verifier guard (after policy, before any side effect).
        if registration.requires_verification and not self._verification.has_verifier(action.type):
            message = f"Required verifier is not registered for action type {action.type!r}"
            self._emit(
                AuditEventType.ACTION_FAILED,
                action,
                outcome="failed",
                summary=message,
                details={"error_code": ErrorCode.VERIFIER_MISSING.value},
            )
            return self._simple_result(
                action,
                ActionStatus.FAILED,
                ErrorCode.VERIFIER_MISSING,
                message,
            )

        # 6) Execute (only reached on ALLOW with required verification available).
        exec_result = await self.execute_authorized_action(handler, action, context)

        # 7) Verify — only if execution actually succeeded.
        verification: VerificationResult | None = None
        if exec_result.success:
            verification = await self._run_verification(
                action,
                exec_result,
                context,
                required=registration.requires_verification,
            )

        # 8) Build the planner-facing result.
        return self.build_action_result(action, exec_result, verification)

    # ── stage helpers ──────────────────────────────────────────────────────
    def _apply_policy(self, action: Action, decision: PolicyDecision) -> ActionResult | None:
        """Return a short-circuit ActionResult for non-ALLOW outcomes, else None."""
        if decision.outcome is PolicyOutcome.DENY:
            self._emit(AuditEventType.POLICY_DENIED, action, outcome="denied",
                       summary=decision.reason, details={"rule_id": decision.rule_id})
            return self._simple_result(action, ActionStatus.DENIED, ErrorCode.POLICY_DENIED, decision.reason)

        if decision.outcome is PolicyOutcome.CONFIRM:
            self._emit(AuditEventType.POLICY_CONFIRMATION_REQUIRED, action, outcome="confirm",
                       summary=decision.reason,
                       details={"rule_id": decision.rule_id, "action_hash": decision.action_hash})
            return self._simple_result(
                action, ActionStatus.NEEDS_CONFIRMATION, ErrorCode.CONFIRMATION_REQUIRED, decision.reason,
                evidence={"confirmation_token": decision.confirmation_token, "action_hash": decision.action_hash},
            )

        if decision.outcome is PolicyOutcome.CLARIFY:
            self._emit(AuditEventType.POLICY_CLARIFICATION_REQUIRED, action, outcome="clarify",
                       summary=decision.reason, details={"rule_id": decision.rule_id})
            return self._simple_result(action, ActionStatus.CLARIFY, ErrorCode.CLARIFICATION_REQUIRED, decision.reason)

        # ALLOW
        self._emit(AuditEventType.POLICY_ALLOWED, action, outcome="allow",
                   summary=decision.reason, details={"rule_id": decision.rule_id})
        return None

    async def execute_authorized_action(
        self, handler, action: Action, context: ExecutionContext
    ) -> ExecutorResult:
        """Run an ALLOW-ed action's executor, containing any exception."""
        self._emit(AuditEventType.ACTION_STARTED, action, summary=f"executing {action.type}")
        try:
            result = await handler.execute(action)
        except Exception as exc:
            result = ExecutorResult(
                success=False,
                error=ActionError(
                    code=ErrorCode.EXECUTOR_ERROR.value,
                    message=f"Executor raised: {type(exc).__name__}: {exc}",
                    retryable=False,
                ),
            )
        if result.success:
            self._emit(AuditEventType.ACTION_COMPLETED, action, outcome="success",
                       summary=f"completed {action.type}")
        else:
            self._emit(AuditEventType.ACTION_FAILED, action, outcome="failed",
                       summary=f"failed {action.type}")
        return result

    async def _run_verification(
        self,
        action: Action,
        exec_result: ExecutorResult,
        context: ExecutionContext,
        *,
        required: bool,
    ) -> VerificationResult:
        self._emit(AuditEventType.VERIFICATION_STARTED, action, summary=f"verifying {action.type}")
        try:
            verification = await self._verification.verify_action(action, exec_result, context)
        except Exception as exc:
            exception_name = type(exc).__name__
            verification = VerificationResult(
                status=VerificationStatus.FAILED,
                method=f"verifier exception containment ({exception_name})",
                expected="verifier completes without raising",
                observed=exception_name,
                message=f"Verifier raised {exception_name}: {exc}",
            )

        if required and verification.status is VerificationStatus.SKIPPED:
            skipped_message = verification.message
            verification = VerificationResult(
                status=VerificationStatus.FAILED,
                method=verification.method,
                expected="required verification result",
                observed=VerificationStatus.SKIPPED.value,
                message=(
                    f"Required verifier returned SKIPPED for {action.type!r}"
                    + (f": {skipped_message}" if skipped_message else "")
                ),
            )

        if verification.status is VerificationStatus.PASSED:
            self._emit(AuditEventType.VERIFICATION_PASSED, action, outcome="passed", summary=verification.message)
        elif verification.status is VerificationStatus.FAILED:
            self._emit(AuditEventType.VERIFICATION_FAILED, action, outcome="failed", summary=verification.message)
        else:
            self._emit(AuditEventType.VERIFICATION_SKIPPED, action, outcome="skipped", summary=verification.message)
        return verification

    def build_action_result(
        self, action: Action, exec_result: ExecutorResult, verification: VerificationResult | None
    ) -> ActionResult:
        """Combine execution + verification into the planner-facing result.

        A modifying action is NEVER reported as successful if verification failed.
        """
        if not exec_result.success:
            status, error = ActionStatus.FAILED, exec_result.error
        elif verification is not None and verification.status is VerificationStatus.FAILED:
            status = ActionStatus.FAILED
            error = ActionError(
                code=ErrorCode.VERIFICATION_FAILED.value,
                message=verification.message or "Verification failed after execution.",
                retryable=False,
            )
        else:
            status, error = ActionStatus.SUCCESS, None

        return ActionResult(
            action_id=action.action_id,
            task_id=action.task_id,
            status=status,
            evidence=_bound(exec_result.evidence),
            verification=verification,
            error=error,
        )

    # ── small utilities ────────────────────────────────────────────────────
    def _simple_result(
        self,
        action: Action,
        status: ActionStatus,
        code: ErrorCode,
        message: str,
        *,
        evidence: dict | None = None,
        details: dict | None = None,
    ) -> ActionResult:
        """Build a result for a short-circuit path (no executor ran)."""
        error = None
        if status not in (ActionStatus.SUCCESS,):
            error = ActionError(code=code.value, message=message, retryable=False, details=details)
        return ActionResult(
            action_id=action.action_id,
            task_id=action.task_id,
            status=status,
            evidence=_bound(evidence or {}),
            error=error,
        )

    def _emit(
        self,
        event_type: AuditEventType,
        action: Action,
        *,
        outcome: str | None = None,
        summary: str = "",
        details: dict | None = None,
    ) -> None:
        self._audit.emit(
            AuditEvent(
                task_id=action.task_id,
                action_id=action.action_id,
                sequence=action.sequence,
                event_type=event_type,
                component=_COMPONENT,
                outcome=outcome,
                summary=summary,
                details=_bound(details or {}),
            )
        )
