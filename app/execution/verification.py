from pathlib import Path
from typing import Any

from app.execution.semantic import SemanticDesktop
from app.schemas import Action, ActionType, VerificationResult


def verify_action(
    action: Action,
    evidence: dict[str, Any],
    desktop: SemanticDesktop,
) -> VerificationResult:
    """Verify observable effects. Unknown is distinct from a failed check."""
    if action.type in {
        ActionType.CREATE_FILE,
        ActionType.OVERWRITE_FILE,
        ActionType.COPY_FILE_CONTENT,
    }:
        path = evidence.get("path") or evidence.get("destination")
        passed = bool(path and Path(str(path)).is_file())
        return VerificationResult(
            passed=passed,
            message="Destination file exists" if passed else "Destination file was not found",
            evidence={"path": path},
        )
    if action.type is ActionType.MOVE_FILE:
        destination = evidence.get("destination")
        source = evidence.get("source")
        passed = bool(
            destination
            and Path(str(destination)).exists()
            and (not source or not Path(str(source)).exists())
        )
        return VerificationResult(
            passed=passed,
            message=(
                "File moved to the destination"
                if passed
                else "File move could not be verified"
            ),
            evidence={"source": source, "destination": destination},
        )
    if action.type is ActionType.DELETE_FILE:
        path = evidence.get("path") or action.target
        passed = not Path(str(path)).exists()
        return VerificationResult(
            passed=passed,
            message="File no longer exists" if passed else "File still exists",
            evidence={"path": path},
        )
    if action.type is ActionType.READ_FILE:
        passed = isinstance(evidence.get("content"), str)
        return VerificationResult(
            passed=passed,
            message="File content was read" if passed else "No file content was returned",
        )
    if action.type in {ActionType.OPEN_APPLICATION, ActionType.FOCUS_APPLICATION}:
        running = desktop.is_application_running(
            str(evidence.get("application", action.target))
        )
        return VerificationResult(
            passed=None,
            message=(
                "Application process was observed"
                if running is True
                else "Launch command completed; process visibility is advisory"
            ),
            evidence={"process_observed": running},
        )
    if action.type is ActionType.CLOSE_APPLICATION:
        running = desktop.is_application_running(
            str(evidence.get("application", action.target))
        )
        return VerificationResult(
            passed=None,
            message=(
                "Application process is no longer visible"
                if running is False
                else "Close command completed; process visibility is advisory"
            ),
            evidence={"process_observed": running},
        )
    return VerificationResult(
        passed=None,
        message="The action completed, but no deterministic check is available",
    )
