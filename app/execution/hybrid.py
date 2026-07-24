"""Exact legacy/structured routing for the integrated MVP runtime."""

# ruff: noqa: E402, I001

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from agent import store as agent_store
from app.config import DB_PATH
from app.execution.executor import DesktopExecutor
from app.planning.repository import PlanRepository
from app.schemas import (
    Action,
    ActionPlan,
    ActionResult,
    ActionStatus,
    ActionType,
    ExecutionStatus,
    PlanExecutionResponse,
    VerificationResult,
)
from app.structured_actions import (
    STRUCTURED_ACTION_TYPES,
    UNTRUSTED_CONTENT_ACTIONS,
)

_WINDOWS_AGENT_ROOT = Path(__file__).resolve().parents[2] / "windows-agent"
if str(_WINDOWS_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_WINDOWS_AGENT_ROOT))

from windows_agent.audit import AuditSink, NullAuditSink  # noqa: E402
from windows_agent.contracts import (  # noqa: E402
    Action as StructuredAction,
    ActionResult as StructuredResult,
    ActionStatus as StructuredStatus,
    AuditEvent,
    AuditEventType,
    VerificationStatus,
)
from windows_agent.execution import ActionRegistry, Dispatcher, ExecutionContext  # noqa: E402
from windows_agent.executors import (  # noqa: E402
    register_document_executor,
    register_file_executor,
    register_pdf_executor,
    register_presentation_executor,
    register_spreadsheet_executor,
)
from windows_agent.policy import SprintPolicy, action_hash as structured_action_hash  # noqa: E402
from windows_agent.verification import (  # noqa: E402
    VerificationRegistry,
    register_document_verifiers,
    register_file_verifiers,
    register_presentation_verifiers,
    register_spreadsheet_verifiers,
)

_LEGACY_ACTION_TYPES = frozenset(action.value for action in ActionType)
_REFERENCE_KEYS = frozenset({"$ref", "regex", "group", "coerce"})
_INJECTION_PATTERNS = (
    re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|user)\b", re.IGNORECASE),
    re.compile(r"\b(?:powershell|cmd\.exe|run\s+(?:a\s+)?command|shell)\b", re.IGNORECASE),
    re.compile(r"\b(?:delete|remove)\s+(?:all\s+)?files?\b", re.IGNORECASE),
    re.compile(r"\b(?:claim|grant|assume)\s+authori[sz]ation\b", re.IGNORECASE),
)


class StoreAuditSink(AuditSink):
    """Bridge native structured events into the existing redacting SQLite store."""

    def __init__(
        self,
        repository: PlanRepository | None = None,
        db_path: Path = DB_PATH,
    ) -> None:
        self._repository = repository
        agent_store.connect(str(db_path))

    def emit(self, event: AuditEvent) -> None:
        details = {
            "component": event.component,
            "outcome": event.outcome,
            "summary": event.summary[:300],
            **dict(list(event.details.items())[:20]),
        }
        agent_store.log(
            event.task_id,
            event.event_type.value,
            details,
            action_id=event.action_id,
        )
        if self._repository is not None:
            try:
                self._repository.log_event(
                    UUID(event.task_id),
                    event.event_type.value,
                    event.summary[:300] or event.event_type.value.replace("_", " "),
                )
            except (ValueError, OSError):
                pass


def build_structured_dispatcher(
    audit: AuditSink | None = None,
) -> Dispatcher:
    registry = ActionRegistry()
    register_file_executor(registry)
    register_pdf_executor(registry)
    register_spreadsheet_executor(registry)
    register_document_executor(registry)
    register_presentation_executor(registry)
    registered = frozenset(registry.list_registered_actions())
    if registered != STRUCTURED_ACTION_TYPES:
        missing = sorted(STRUCTURED_ACTION_TYPES - registered)
        unexpected = sorted(registered - STRUCTURED_ACTION_TYPES)
        raise RuntimeError(
            f"Structured registry/schema mismatch; missing={missing}, unexpected={unexpected}"
        )

    verification = VerificationRegistry()
    register_file_verifiers(verification)
    register_spreadsheet_verifiers(verification)
    register_document_verifiers(verification)
    register_presentation_verifiers(verification)
    return Dispatcher(
        registry,
        SprintPolicy(),
        verification=verification,
        audit=audit or NullAuditSink(),
    )


class HybridExecutor:
    """Route exact legacy types to DesktopExecutor and dotted types to Dispatcher."""

    def __init__(
        self,
        *,
        desktop: DesktopExecutor | None = None,
        dispatcher: Dispatcher | None = None,
        audit: AuditSink | None = None,
        structured_enabled: bool = True,
    ) -> None:
        self._desktop = desktop or DesktopExecutor()
        self._audit = audit or NullAuditSink()
        self._dispatcher = dispatcher or build_structured_dispatcher(self._audit)
        self._structured_enabled = structured_enabled

    async def execute_plan(
        self,
        plan: ActionPlan,
        approved_action_ids: set[UUID],
        control_state: Callable[[], ExecutionStatus | None] | None = None,
        approved_action_hashes: dict[UUID, str] | None = None,
    ) -> PlanExecutionResponse:
        results: list[ActionResult] = []
        succeeded: set[UUID] = set()
        prior_by_key: dict[str, ActionResult] = {}
        approved_hashes = approved_action_hashes or {}
        self._emit_task(AuditEventType.TASK_STARTED, plan, "task execution started")

        for action in plan.actions:
            control_result = await self._wait_for_control(plan, action, control_state)
            if control_result is not None:
                results.append(control_result)
                self._emit_task(AuditEventType.TASK_CANCELLED, plan, "task cancelled")
                return PlanExecutionResponse(
                    plan_id=plan.plan_id,
                    status=ExecutionStatus.CANCELLED,
                    results=results,
                )
            if any(dependency not in succeeded for dependency in action.depends_on):
                results.append(
                    ActionResult(
                        action_id=action.action_id,
                        status=ActionStatus.BLOCKED,
                        error="A dependency did not complete successfully",
                    )
                )
                self._emit_task(AuditEventType.TASK_FAILED, plan, "dependency blocked task")
                return PlanExecutionResponse(
                    plan_id=plan.plan_id,
                    status=ExecutionStatus.BLOCKED,
                    results=results,
                )

            action_type = str(action.type)
            if action_type in _LEGACY_ACTION_TYPES:
                result = await self._run_legacy(action, approved_action_ids)
            elif self._structured_enabled and action_type in STRUCTURED_ACTION_TYPES:
                result = await self._run_structured(
                    plan,
                    action,
                    prior_by_key,
                    approved_hashes,
                )
            elif "." in action_type:
                result = ActionResult(
                    action_id=action.action_id,
                    status=ActionStatus.FAILED,
                    error=f"Unknown or disabled structured action: {action_type}",
                )
            else:
                result = ActionResult(
                    action_id=action.action_id,
                    status=ActionStatus.FAILED,
                    error=f"Unknown action type: {action_type}",
                )

            results.append(result)
            if action.step_key:
                prior_by_key[action.step_key] = result
            if result.status is not ActionStatus.SUCCEEDED:
                status = (
                    ExecutionStatus.BLOCKED
                    if result.status is ActionStatus.BLOCKED
                    else ExecutionStatus.FAILED
                )
                self._emit_task(AuditEventType.TASK_FAILED, plan, result.error or "action failed")
                return PlanExecutionResponse(
                    plan_id=plan.plan_id,
                    status=status,
                    results=results,
                )
            succeeded.add(action.action_id)

        self._emit_task(AuditEventType.TASK_COMPLETED, plan, "all actions completed")
        return PlanExecutionResponse(
            plan_id=plan.plan_id,
            status=ExecutionStatus.COMPLETED,
            results=results,
        )

    async def _run_legacy(
        self,
        action: Action,
        approved_action_ids: set[UUID],
    ) -> ActionResult:
        if action.requires_confirmation and action.action_id not in approved_action_ids:
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.BLOCKED,
                error="Explicit user confirmation is required",
            )
        return await asyncio.to_thread(self._desktop._execute_action, action)

    async def _run_structured(
        self,
        plan: ActionPlan,
        action: Action,
        prior_by_key: dict[str, ActionResult],
        approved_action_hashes: dict[UUID, str],
    ) -> ActionResult:
        try:
            parameters = _resolve_references(action.parameters, prior_by_key)
            target = _resolve_local_path(action.target)
            parameters = _resolve_path_parameters(parameters)
        except (KeyError, TypeError, ValueError) as exc:
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.FAILED,
                error=f"Structured action validation failed: {exc}",
            )

        structured = StructuredAction(
            action_id=str(action.action_id),
            task_id=str(plan.plan_id),
            sequence=action.sequence - 1,
            type=str(action.type),
            target=target,
            parameters=parameters,
            expected_result=action.expected_result,
            reason=action.description or f"Execute {action.type}",
        )
        context = ExecutionContext(task_id=str(plan.plan_id))
        provided_hash = approved_action_hashes.get(action.action_id)
        context.approved_action_hashes = {}
        if (
            action.requires_confirmation
            and action.confirmation_hash
            and provided_hash == action.confirmation_hash
        ):
            context.approved_action_hashes[str(action.action_id)] = structured_action_hash(
                structured
            )
        structured_result = await self._dispatcher.dispatch(structured, context)
        self._detect_untrusted_content(structured, structured_result)
        return _to_app_result(action, structured_result)

    async def _wait_for_control(
        self,
        plan: ActionPlan,
        action: Action,
        control_state: Callable[[], ExecutionStatus | None] | None,
    ) -> ActionResult | None:
        if control_state is None:
            return None
        state = control_state()
        while state is ExecutionStatus.PAUSED:
            await asyncio.sleep(0.05)
            state = control_state()
        if state is ExecutionStatus.CANCELLED:
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.CANCELLED,
                error="Cancelled by the user",
            )
        return None

    def _detect_untrusted_content(
        self,
        action: StructuredAction,
        result: StructuredResult,
    ) -> None:
        if action.type not in UNTRUSTED_CONTENT_ACTIONS:
            return
        text = result.evidence.get("text") or result.evidence.get("content")
        if not isinstance(text, str):
            return
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(text):
                self._audit.emit(
                    AuditEvent(
                        task_id=action.task_id,
                        action_id=action.action_id,
                        sequence=action.sequence,
                        event_type=AuditEventType.UNTRUSTED_CONTENT_DETECTED,
                        component="hybrid_executor",
                        outcome="detected",
                        summary="Untrusted document instructions were treated as data.",
                        details={
                            "source_action": action.type,
                            "matched_pattern": pattern.pattern[:120],
                        },
                    )
                )
                return

    def _emit_task(
        self,
        event_type: AuditEventType,
        plan: ActionPlan,
        summary: str,
    ) -> None:
        self._audit.emit(
            AuditEvent(
                task_id=str(plan.plan_id),
                event_type=event_type,
                component="hybrid_executor",
                summary=summary[:300],
            )
        )


def _resolve_references(
    value: Any,
    prior_by_key: dict[str, ActionResult],
    *,
    depth: int = 0,
) -> Any:
    if depth > 8:
        raise ValueError("Result reference nesting is too deep")
    if isinstance(value, list):
        return [_resolve_references(item, prior_by_key, depth=depth + 1) for item in value[:100]]
    if not isinstance(value, dict):
        return value
    if "$ref" not in value:
        return {
            key: _resolve_references(item, prior_by_key, depth=depth + 1)
            for key, item in list(value.items())[:100]
        }
    if not set(value).issubset(_REFERENCE_KEYS):
        raise ValueError("Result reference contains unsupported keys")
    reference = value.get("$ref")
    if not isinstance(reference, str):
        raise TypeError("$ref must be a string")
    parts = reference.split(".")
    if len(parts) < 3 or parts[1] != "evidence":
        raise ValueError("$ref must have the form step_key.evidence.field")
    result = prior_by_key.get(parts[0])
    if result is None or result.status is not ActionStatus.SUCCEEDED:
        raise KeyError(f"No successful earlier result named {parts[0]!r}")
    resolved: Any = result.evidence
    for part in parts[2:]:
        if isinstance(resolved, dict) and part in resolved:
            resolved = resolved[part]
        elif isinstance(resolved, list) and part.isdigit():
            resolved = resolved[int(part)]
        else:
            raise KeyError(f"Result reference field not found: {reference}")
    regex = value.get("regex")
    if regex is not None:
        if not isinstance(regex, str) or len(regex) > 300:
            raise ValueError("Reference regex must be a bounded string")
        match = re.search(regex, str(resolved)[:5000])
        if match is None:
            raise ValueError(f"Reference regex did not match {reference}")
        group = value.get("group", 1)
        if not isinstance(group, (int, str)):
            raise ValueError("Reference regex group must be an integer or name")
        resolved = match.group(group)
    coerce = value.get("coerce")
    if coerce == "number":
        number = float(resolved)
        resolved = int(number) if number.is_integer() else number
    elif coerce == "string":
        resolved = str(resolved)
    elif coerce is not None:
        raise ValueError(f"Unsupported reference coercion: {coerce}")
    return resolved


def _resolve_local_path(value: str | None) -> str | None:
    if value is None:
        return None
    expanded = Path(value).expanduser()
    if expanded.is_absolute():
        return str(expanded.resolve(strict=False))

    parts = [part for part in re.split(r"[\\/]+", value) if part]
    if not parts:
        raise ValueError("Local path is empty")
    known = {
        "desktop": Path.home() / "Desktop",
        "documents": Path.home() / "Documents",
        "downloads": Path.home() / "Downloads",
    }
    root = known.get(parts[0].casefold())
    if root is None:
        raise ValueError("Relative local paths must begin with Desktop, Documents, or Downloads")
    return str(root.joinpath(*parts[1:]).resolve(strict=False))


def _resolve_path_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(parameters)
    for key in ("destination", "save_as"):
        value = resolved.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise TypeError(f"{key} must be a path string")
            resolved[key] = _resolve_local_path(value)
    return resolved


def _to_app_result(action: Action, result: StructuredResult) -> ActionResult:
    status_map = {
        StructuredStatus.SUCCESS: ActionStatus.SUCCEEDED,
        StructuredStatus.CANCELLED: ActionStatus.CANCELLED,
        StructuredStatus.DENIED: ActionStatus.BLOCKED,
        StructuredStatus.NEEDS_CONFIRMATION: ActionStatus.BLOCKED,
        StructuredStatus.CLARIFY: ActionStatus.BLOCKED,
        StructuredStatus.FAILED: ActionStatus.FAILED,
    }
    verification = None
    if result.verification is not None:
        passed = {
            VerificationStatus.PASSED: True,
            VerificationStatus.FAILED: False,
            VerificationStatus.SKIPPED: None,
        }[result.verification.status]
        verification = VerificationResult(
            passed=passed,
            message=result.verification.message or result.verification.method,
            evidence={
                "method": result.verification.method,
                "expected": result.verification.expected,
                "observed": result.verification.observed,
            },
        )
    return ActionResult(
        action_id=action.action_id,
        status=status_map[result.status],
        evidence=result.evidence,
        error=result.error.message if result.error else None,
        verification=verification,
    )
