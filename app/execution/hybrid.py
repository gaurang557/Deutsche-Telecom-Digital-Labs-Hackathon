"""Exact legacy/structured routing for the integrated MVP runtime."""

# ruff: noqa: E402, I001

from __future__ import annotations

import asyncio
import os
import re
import stat
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
    READ_ONLY_STRUCTURED_ACTIONS,
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
    VerificationResult as StructuredVerification,
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
        requested_target: str | None = None
        try:
            parameters = _resolve_references(action.parameters, prior_by_key)
            target = _resolve_local_path(action.target)
            parameters = _resolve_path_parameters(parameters)
            # Only a read may be pointed at a file it did not name exactly. A
            # write or a create keeps the destination the plan actually gave.
            if target is not None and str(action.type) in READ_ONLY_STRUCTURED_ACTIONS:
                discovered = _discover_readable_file(target)
                if discovered is not None:
                    requested_target, target = target, discovered
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
        self._note_sheet_substitution(structured, structured_result)
        if requested_target is not None:
            self._note_path_substitution(structured, structured_result, requested_target)
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

    def _note_sheet_substitution(
        self,
        action: StructuredAction,
        result: StructuredResult,
    ) -> None:
        """Record a deterministic sheet substitution in the audit trail.

        A plan cannot always know a workbook's sheet names, so the spreadsheet
        executor resolves a requested name onto a real sheet when only one sheet
        could have been meant. That silently rewrites a step's parameter, which
        is exactly the kind of thing the trail exists to show.
        """
        if not result.evidence.get("sheet_substituted"):
            return
        requested = result.evidence.get("requested_sheet")
        used = result.evidence.get("sheet")
        self._audit.emit(
            AuditEvent(
                task_id=action.task_id,
                action_id=action.action_id,
                sequence=action.sequence,
                event_type=AuditEventType.PLAN_REVISED,
                component="hybrid_executor",
                outcome="sheet_substituted",
                summary=(
                    f"Used the workbook's own sheet {used!r} for a step that "
                    f"asked for {requested!r}."
                ),
                details={
                    "source_action": action.type,
                    "requested_sheet": requested,
                    "sheet": used,
                },
            )
        )

    def _note_path_substitution(
        self,
        action: StructuredAction,
        result: StructuredResult,
        requested: str,
    ) -> None:
        """Record that a read was pointed at a file the plan did not name exactly.

        Same reasoning as the sheet substitution above: leniency that left no
        trace would be indistinguishable from the plan having been right, so the
        path the step asked for is carried in the evidence and in the trail. The
        two added keys are short paths, so this stays within the spirit of the
        dispatcher's evidence bound.
        """
        result.evidence["requested_path"] = requested
        result.evidence["path_substituted"] = True
        self._audit.emit(
            AuditEvent(
                task_id=action.task_id,
                action_id=action.action_id,
                sequence=action.sequence,
                event_type=AuditEventType.PLAN_REVISED,
                component="hybrid_executor",
                outcome="path_substituted",
                summary=(
                    f"Read {action.target!r} for a step that asked for "
                    f"{requested!r}, which does not exist."
                ),
                details={
                    "source_action": action.type,
                    "requested_path": requested,
                    "path": action.target,
                },
            )
        )

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


def _known_roots() -> dict[str, Path]:
    """The folder aliases a plan may name.

    These are also the ONLY directories a search for a misplaced file is ever
    allowed to look inside, which is why there is one definition of them. Built
    per call rather than at import so the home directory is read when it is used.
    """
    home = Path.home()
    return {
        "desktop": home / "Desktop",
        "documents": home / "Documents",
        "downloads": home / "Downloads",
    }


#: How many directory levels below the root are opened at all. A user who says
#: "the file in my reports folder" means a couple of hops, not a disk scan, so
#: this is small on purpose: it keeps the walk cheap, and it keeps unrelated
#: copies of a common file name (a second unpacked archive, a backup tree) from
#: turning a resolvable request into an ambiguous one.
_DISCOVERY_MAX_DEPTH = 2
#: How many directories the whole walk may open, so a wide tree cannot become a
#: crawl even within the depth limit.
_DISCOVERY_MAX_DIRECTORIES = 250
#: How many ambiguous candidates a failure message may name.
_DISCOVERY_MAX_CANDIDATES = 8

#: Windows directory attributes we refuse to walk into: hidden and system trees
#: are not where a user keeps the file they just asked about, and a reparse point
#: is how a junction (which `is_symlink` does NOT report) would lead the walk out
#: of the root it is confined to.
_SKIPPED_DIRECTORY_ATTRIBUTES = (
    getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0)
    | getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0)
    | getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
)


def _is_within(candidate: Path, root: Path) -> bool:
    """Is `candidate` at or below `root`, comparing the way the OS does?

    `normcase` makes the comparison case- and separator-insensitive on Windows
    and is the identity elsewhere, so a differently spelled but identical path is
    not mistaken for an escape. Anything unanswerable is answered "no": this
    guards a boundary, so it fails closed.
    """
    try:
        return Path(os.path.normcase(str(candidate))).is_relative_to(
            Path(os.path.normcase(str(root)))
        )
    except (OSError, RuntimeError, ValueError):
        return False


def _containing_root(path: Path) -> Path | None:
    """The known folder `path` sits inside, or None if it sits inside none.

    The root itself is not a containing root: there is nothing above a root to
    search, and returning it for the root would let a bare alias be searched.
    """
    for root in _known_roots().values():
        resolved = root.resolve(strict=False)
        if not _is_within(path, resolved):
            continue
        if os.path.normcase(str(path)) == os.path.normcase(str(resolved)):
            continue
        return resolved
    return None


def _is_skippable_directory(entry: os.DirEntry[str]) -> bool:
    if entry.name.startswith("."):
        return True
    try:
        attributes = entry.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & _SKIPPED_DIRECTORY_ATTRIBUTES)


def _find_by_basename(root: Path, basename: str) -> list[Path]:
    """Files named `basename` within the bounded region below `root`.

    Breadth-first, so the cheap shallow answer is reached first and the visit
    budget is spent near the root rather than down one arbitrary branch. Every
    match is re-checked against `root` before it is returned, so no filesystem
    link can hand back a path outside the directory this walk is confined to.
    """
    wanted = basename.casefold()
    matches: list[Path] = []
    frontier: list[tuple[Path, int]] = [(root, 0)]
    visited = 0

    while frontier and visited < _DISCOVERY_MAX_DIRECTORIES:
        directory, depth = frontier.pop(0)
        visited += 1
        try:
            with os.scandir(directory) as scan:
                entries = sorted(scan, key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if depth < _DISCOVERY_MAX_DEPTH and not _is_skippable_directory(entry):
                        frontier.append((Path(entry.path), depth + 1))
                elif entry.name.casefold() == wanted and entry.is_file():
                    candidate = Path(entry.path)
                    if _is_within(candidate.resolve(strict=False), root):
                        matches.append(candidate)
            except OSError:
                continue
        if len(matches) > _DISCOVERY_MAX_CANDIDATES:
            break
    return matches


def _display_under(candidate: Path, root: Path) -> str:
    """A candidate named relative to its root, so a message leaks no home path."""
    try:
        return f"{root.name}/{candidate.relative_to(root).as_posix()}"
    except ValueError:
        return candidate.name


def _discover_readable_file(resolved: str) -> str | None:
    """Where the file a read step asked for actually is, or None to leave it be.

    A plan names a path from what the user said, and a spoken request routinely
    omits the folder the file sits in — so an exact path that does not exist is
    often the right file one level down rather than a file that is absent. This
    looks for it, deterministically and within bounds, instead of relying on the
    model to have guessed the whole path.

    Returns None whenever the request should stand as written: the path already
    exists, it is not inside one of the resolvable roots, or nothing below that
    root has that name (which leaves the executor's own "File not found" as the
    reported failure). Raises ValueError when SEVERAL files could have been
    meant, because picking one of them would risk quietly reading the wrong file.

    Read-only by construction: it only ever returns a path that already exists,
    it never looks outside the root the request already resolved under, and its
    only caller applies it to read actions. A create or write destination is
    never routed through here (see `_resolve_path_parameters`).
    """
    path = Path(resolved)
    if not path.name or path.exists():
        return None
    root = _containing_root(path)
    if root is None:
        return None

    matches = _find_by_basename(root, path.name)
    if not matches:
        return None
    if len(matches) == 1:
        return str(matches[0])

    listed = ", ".join(
        _display_under(match, root) for match in matches[:_DISCOVERY_MAX_CANDIDATES]
    )
    raise ValueError(
        f"There is no {path.name!r} at {_display_under(path, root)}, and several "
        f"files below {root.name} have that name: {listed}. "
        "Please say which folder you meant."
    )


def _resolve_local_path(value: str | None) -> str | None:
    if value is None:
        return None
    expanded = Path(value).expanduser()
    if expanded.is_absolute():
        return str(expanded.resolve(strict=False))

    parts = [part for part in re.split(r"[\\/]+", value) if part]
    if not parts:
        raise ValueError("Local path is empty")
    root = _known_roots().get(parts[0].casefold())
    if root is None:
        raise ValueError("Relative local paths must begin with Desktop, Documents, or Downloads")
    return str(root.joinpath(*parts[1:]).resolve(strict=False))


def _resolve_path_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Resolve the parameters that carry a second path.

    Deliberately plain alias expansion with no search: `destination` and
    `save_as` say where output should GO, and a path that does not exist yet is
    the normal case for them. Redirecting one onto an existing file that happens
    to share its name would write somewhere the plan never named.
    """
    resolved = dict(parameters)
    for key in ("destination", "save_as"):
        value = resolved.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise TypeError(f"{key} must be a path string")
            resolved[key] = _resolve_local_path(value)
    return resolved


def _verification_message(verification: StructuredVerification) -> str:
    """User-facing wording for a verification outcome.

    A read-only action registers no verifier on purpose, and the registry says so
    in implementer's terms ("No verifier registered for 'pdf.read_text'"). Shown
    next to a step that succeeded, that reads like a missing piece of the system
    rather than the intended answer, so the skipped case gets a sentence about
    the step instead. The method/expected/observed detail is still carried in the
    result's evidence.
    """
    if (
        verification.status is VerificationStatus.SKIPPED
        and verification.method == "none"
    ):
        return "Nothing to verify: this step only read, it changed nothing."
    return verification.message or verification.method


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
            message=_verification_message(result.verification),
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
