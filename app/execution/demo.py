import time
from pathlib import Path
from typing import Any

from app.config import Settings
from app.execution.executor import DesktopExecutor
from app.schemas import (
    Action,
    ActionResult,
    ActionStatus,
    ActionType,
    VerificationResult,
)


class DemoDesktopExecutor(DesktopExecutor):
    """Safe executor for the public deployment; it never controls the host OS."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._sandbox = Path(settings.demo_sandbox_dir)
        self._downloads = self._sandbox / "Downloads"
        self._prepare_samples()

    def _prepare_samples(self) -> None:
        self._downloads.mkdir(parents=True, exist_ok=True)
        samples = {
            "Hackathon-brief.pdf": "Voice desk hackathon demonstration PDF.",
            "team-notes.txt": "Demo notes: planning, approval, execution, verification.",
            "quarterly-summary.txt": "Revenue grew while support response time improved.",
        }
        for name, content in samples.items():
            path = self._downloads / name
            if not path.exists():
                path.write_text(content, encoding="utf-8")

    def _execute_action(self, action: Action) -> ActionResult:
        time.sleep(0.65)
        evidence = self._simulate(action)
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCEEDED,
            evidence=evidence,
            verification=VerificationResult(
                passed=True,
                message="Safely verified inside the demo sandbox",
                evidence={"environment": "demo"},
            ),
        )

    def _simulate(self, action: Action) -> dict[str, Any]:
        base = {
            "environment": "demo",
            "simulated": True,
            "action": action.type.value,
            "target": action.target,
        }
        if action.type is ActionType.LIST_DIRECTORY:
            entries = sorted(path.name for path in self._downloads.iterdir())
            return {
                **base,
                "path": "Demo workspace/Downloads",
                "entries": [{"name": name, "type": "file"} for name in entries],
                "count": len(entries),
                "content": "\n".join(f"File: {name}" for name in entries),
            }
        if action.type is ActionType.READ_FILE:
            return {
                **base,
                "path": f"Demo workspace/{Path(action.target).name}",
                "content": "This is safe sample content from the public demo workspace.",
            }
        if action.type is ActionType.OPEN_FILE:
            return {
                **base,
                "path": "Demo workspace/Downloads/Hackathon-brief.pdf",
                "opened": True,
            }
        if action.type is ActionType.SUMMARIZE_GMAIL_EMAIL:
            return {
                **base,
                "summary": (
                    "The demo email confirms the judging session and asks the team "
                    "to share its deployment link before the presentation."
                ),
            }
        if action.type in {
            ActionType.COPY_FILE_CONTENT,
            ActionType.CREATE_FILE,
            ActionType.MOVE_FILE,
            ActionType.OVERWRITE_FILE,
            ActionType.DELETE_FILE,
        }:
            return {**base, "sandbox_change": "completed", "path": "Demo workspace"}
        return {**base, "completed": True}
