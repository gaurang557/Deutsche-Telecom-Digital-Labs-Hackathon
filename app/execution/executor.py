import asyncio
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from app.execution.gmail import capture_open_gmail_email, summarize_email
from app.schemas import (
    Action,
    ActionPlan,
    ActionResult,
    ActionStatus,
    ActionType,
    ExecutionStatus,
    PlanExecutionResponse,
)

_UNSUPPORTED_EXTERNAL_ACTIONS = {
    ActionType.SEND_MESSAGE,
    ActionType.SUBMIT_FORM,
    ActionType.PUBLISH_CONTENT,
}
_WINDOWS_APPLICATIONS = {
    "calculator": "calc.exe",
    "file explorer": "explorer.exe",
    "google chrome": "chrome.exe",
    "microsoft edge": "msedge.exe",
    "notepad": "notepad.exe",
    "paint": "mspaint.exe",
    "text editor": "notepad.exe",
}
_MACOS_APPLICATIONS = {
    "notepad": "TextEdit",
    "text editor": "TextEdit",
}
_MACOS_PROTECTED_APPLICATIONS = {
    "Codex",
    "Finder",
    "iTerm2",
    "Terminal",
    "Visual Studio Code",
}


class DesktopExecutor:
    """Execute the allow-listed MVP actions on macOS and Windows."""

    async def execute_plan(
        self,
        plan: ActionPlan,
        approved_action_ids: set[UUID],
    ) -> PlanExecutionResponse:
        results: list[ActionResult] = []
        succeeded: set[UUID] = set()

        for action in plan.actions:
            if any(dependency not in succeeded for dependency in action.depends_on):
                results.append(
                    ActionResult(
                        action_id=action.action_id,
                        status=ActionStatus.BLOCKED,
                        error="A dependency did not complete successfully",
                    )
                )
                return PlanExecutionResponse(
                    plan_id=plan.plan_id,
                    status=ExecutionStatus.BLOCKED,
                    results=results,
                )

            if action.requires_confirmation and action.action_id not in approved_action_ids:
                results.append(
                    ActionResult(
                        action_id=action.action_id,
                        status=ActionStatus.BLOCKED,
                        error="Explicit user confirmation is required",
                    )
                )
                return PlanExecutionResponse(
                    plan_id=plan.plan_id,
                    status=ExecutionStatus.BLOCKED,
                    results=results,
                )

            result = await asyncio.to_thread(self._execute_action, action)
            results.append(result)
            if result.status is not ActionStatus.SUCCEEDED:
                return PlanExecutionResponse(
                    plan_id=plan.plan_id,
                    status=ExecutionStatus.FAILED,
                    results=results,
                )
            succeeded.add(action.action_id)

        return PlanExecutionResponse(
            plan_id=plan.plan_id,
            status=ExecutionStatus.COMPLETED,
            results=results,
        )

    def _execute_action(self, action: Action) -> ActionResult:
        try:
            if action.type in _UNSUPPORTED_EXTERNAL_ACTIONS:
                return self._unsupported(action)
            evidence = self._dispatch(action)
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCEEDED,
                evidence=evidence,
            )
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.FAILED,
                error=str(exc),
            )

    def _dispatch(self, action: Action) -> dict[str, Any]:
        handlers = {
            ActionType.OPEN_APPLICATION: self._open_application,
            ActionType.OPEN_FILE: self._open_file,
            ActionType.OPEN_URL: self._open_url,
            ActionType.FOCUS_APPLICATION: self._focus_application,
            ActionType.CLOSE_APPLICATION: self._close_application,
            ActionType.CLOSE_ALL_APPLICATIONS: self._close_all_applications,
            ActionType.CLICK_ELEMENT: self._click_element,
            ActionType.TYPE_TEXT: self._type_text,
            ActionType.PRESS_KEY: self._press_key,
            ActionType.READ_FILE: self._read_file,
            ActionType.COPY_FILE_CONTENT: self._copy_file_content,
            ActionType.CREATE_FILE: self._create_file,
            ActionType.MOVE_FILE: self._move_file,
            ActionType.OVERWRITE_FILE: self._overwrite_file,
            ActionType.DELETE_FILE: self._delete_file,
            ActionType.SUMMARIZE_GMAIL_EMAIL: self._summarize_gmail_email,
        }
        handler = handlers.get(action.type)
        if handler is None:
            raise ValueError(f"Unsupported action type: {action.type}")
        return handler(action)

    @staticmethod
    def _open_application(action: Action) -> dict[str, Any]:
        system = platform.system()
        if system == "Darwin":
            application = _MACOS_APPLICATIONS.get(
                action.target.casefold(),
                action.target,
            )
            subprocess.run(
                ["open", "-a", application],
                check=True,
                capture_output=True,
                text=True,
            )
        elif system == "Windows":
            if not hasattr(os, "startfile"):
                raise RuntimeError("Windows application launcher is unavailable")
            application = _WINDOWS_APPLICATIONS.get(
                action.target.casefold(),
                action.target,
            )
            os.startfile(application)  # type: ignore[attr-defined]
        else:
            raise RuntimeError(f"Desktop execution is unsupported on {system}")
        return {"application": application, "launched": True}

    @staticmethod
    def _open_file(action: Action) -> dict[str, Any]:
        path = DesktopExecutor._resolve_file(action)
        system = platform.system()
        if system == "Darwin":
            subprocess.run(
                ["open", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
        elif system == "Windows":
            if not hasattr(os, "startfile"):
                raise RuntimeError("Windows file launcher is unavailable")
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            raise RuntimeError(f"Desktop execution is unsupported on {system}")
        return {"path": str(path), "opened": True}

    @staticmethod
    def _open_url(action: Action) -> dict[str, Any]:
        target = action.target.strip()
        if "://" not in target:
            target = f"https://{target}"
        parsed = urlparse(target)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("open_url requires a valid HTTP or HTTPS URL")

        browser = action.parameters.get("browser")
        if browser is not None and not isinstance(browser, str):
            raise ValueError("open_url browser must be text")

        system = platform.system()
        if system == "Darwin":
            command = ["open", target]
            if browser:
                command = ["open", "-a", browser, target]
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
        elif system == "Windows":
            if browser:
                executable = _WINDOWS_APPLICATIONS.get(
                    browser.casefold(),
                    browser,
                )
                subprocess.Popen([executable, target])
            elif hasattr(os, "startfile"):
                os.startfile(target)  # type: ignore[attr-defined]
            else:
                raise RuntimeError("Windows URL launcher is unavailable")
        else:
            raise RuntimeError(f"Desktop execution is unsupported on {system}")
        return {"url": target, "browser": browser or "system default", "opened": True}

    @staticmethod
    def _resolve_file(action: Action) -> Path:
        target = action.target.strip()
        known_folders = {
            "desktop": Path.home() / "Desktop",
            "documents": Path.home() / "Documents",
            "downloads": Path.home() / "Downloads",
        }
        path = known_folders.get(target.casefold(), Path(target).expanduser())
        if not path.is_absolute():
            path = path.resolve()

        if path.is_file():
            return path
        if not path.is_dir():
            raise ValueError(f"File or folder does not exist: {path}")

        selection = action.parameters.get("selection")
        if selection != "latest":
            raise ValueError(
                "open_file target is a folder; selection must be 'latest'"
            )

        extension = action.parameters.get("extension")
        if extension is not None and not isinstance(extension, str):
            raise ValueError("open_file extension must be text")
        if isinstance(extension, str) and extension and not extension.startswith("."):
            extension = f".{extension}"

        candidates = [
            candidate
            for candidate in path.iterdir()
            if candidate.is_file()
            and (not extension or candidate.suffix.casefold() == extension.casefold())
        ]
        if not candidates:
            qualifier = f" with extension {extension}" if extension else ""
            raise ValueError(f"No files found in {path}{qualifier}")
        return max(candidates, key=lambda candidate: candidate.stat().st_mtime)

    @staticmethod
    def _focus_application(action: Action) -> dict[str, Any]:
        system = platform.system()
        if system == "Darwin":
            safe_name = action.target.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(
                ["osascript", "-e", f'tell application "{safe_name}" to activate'],
                check=True,
            )
        elif system == "Windows":
            import pygetwindow

            windows = pygetwindow.getWindowsWithTitle(action.target)
            if not windows:
                raise RuntimeError(f"No window found for {action.target!r}")
            windows[0].activate()
        else:
            raise RuntimeError(f"Desktop execution is unsupported on {system}")
        return {"application": action.target, "focused": True}

    @staticmethod
    def _close_application(action: Action) -> dict[str, Any]:
        if platform.system() != "Darwin":
            raise RuntimeError("close_application is currently supported on macOS")
        application = _MACOS_APPLICATIONS.get(
            action.target.casefold(),
            action.target,
        )
        safe_name = application.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(
            ["osascript", "-e", f'tell application "{safe_name}" to quit'],
            check=True,
            capture_output=True,
            text=True,
        )
        return {"application": application, "closed": True}

    @staticmethod
    def _close_all_applications(action: Action) -> dict[str, Any]:
        if platform.system() != "Darwin":
            raise RuntimeError(
                "close_all_applications is currently supported on macOS"
            )
        result = subprocess.run(
            [
                "osascript",
                "-e",
                (
                    'tell application "System Events" to get name of every '
                    "application process whose background only is false"
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        applications = [
            name.strip()
            for name in result.stdout.strip().split(",")
            if name.strip()
        ]
        closed: list[str] = []
        protected: list[str] = []
        for application in applications:
            if application in _MACOS_PROTECTED_APPLICATIONS:
                protected.append(application)
                continue
            safe_name = application.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(
                [
                    "osascript",
                    "-e",
                    (
                        "ignoring application responses\n"
                        f'tell application "{safe_name}" to quit\n'
                        "end ignoring"
                    ),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            closed.append(application)
        return {
            "closed_applications": closed,
            "protected_host_applications": protected,
        }

    @staticmethod
    def _click_element(action: Action) -> dict[str, Any]:
        import pyautogui

        x = action.parameters.get("x")
        y = action.parameters.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            raise ValueError("click_element requires integer x and y parameters")
        pyautogui.click(x=x, y=y)
        return {"clicked": {"x": x, "y": y}}

    @staticmethod
    def _type_text(action: Action) -> dict[str, Any]:
        import pyautogui

        text = action.parameters.get("text")
        if not isinstance(text, str):
            raise ValueError("type_text requires a text parameter")
        pyautogui.write(text, interval=0.02)
        return {"characters_typed": len(text)}

    @staticmethod
    def _press_key(action: Action) -> dict[str, Any]:
        import pyautogui

        keys = action.parameters.get("keys", action.parameters.get("key"))
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
            raise ValueError("press_key requires key or keys parameters")
        pyautogui.hotkey(*keys)
        return {"keys_pressed": keys}

    @staticmethod
    def _read_file(action: Action) -> dict[str, Any]:
        path = Path(action.target).expanduser()
        content = path.read_text(encoding="utf-8")
        return {
            "path": str(path),
            "content": content[:50_000],
            "size": len(content),
            "truncated": len(content) > 50_000,
        }

    @staticmethod
    def _copy_file_content(action: Action) -> dict[str, Any]:
        source = Path(action.target).expanduser()
        destination_value = action.parameters.get("destination")
        if not isinstance(destination_value, str):
            raise ValueError("copy_file_content requires a destination parameter")
        destination = Path(destination_value).expanduser()
        overwrite = action.parameters.get("overwrite", False)
        if not isinstance(overwrite, bool):
            raise ValueError("copy_file_content overwrite must be true or false")
        if destination.exists() and not overwrite:
            raise RuntimeError(
                "The destination already exists; explicitly request overwrite"
            )
        content = source.read_text(encoding="utf-8")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        return {
            "source": str(source),
            "destination": str(destination),
            "characters_copied": len(content),
        }

    @staticmethod
    def _create_file(action: Action) -> dict[str, Any]:
        path = Path(action.target).expanduser()
        if path.exists():
            raise RuntimeError("Refusing to replace an existing file with create_file")
        content = action.parameters.get("content", "")
        if not isinstance(content, str):
            raise ValueError("create_file content must be text")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"path": str(path), "created": True}

    @staticmethod
    def _move_file(action: Action) -> dict[str, Any]:
        source = Path(action.target).expanduser()
        destination_value = action.parameters.get("destination")
        if not isinstance(destination_value, str):
            raise ValueError("move_file requires a destination parameter")
        destination = Path(destination_value).expanduser()
        if destination.exists():
            raise RuntimeError("Refusing to overwrite the move destination")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return {"source": str(source), "destination": str(destination)}

    @staticmethod
    def _overwrite_file(action: Action) -> dict[str, Any]:
        path = Path(action.target).expanduser()
        content = action.parameters.get("content")
        if not isinstance(content, str):
            raise ValueError("overwrite_file requires text content")
        path.write_text(content, encoding="utf-8")
        return {"path": str(path), "overwritten": True}

    @staticmethod
    def _delete_file(action: Action) -> dict[str, Any]:
        from send2trash import send2trash

        path = Path(action.target).expanduser()
        if not path.is_file():
            raise ValueError("delete_file target must be an existing file")
        send2trash(str(path))
        return {"path": str(path), "moved_to_trash": True}

    @staticmethod
    def _summarize_gmail_email(action: Action) -> dict[str, Any]:
        if platform.system() != "Darwin":
            raise RuntimeError(
                "Gmail browser summarization is currently supported on macOS"
            )
        content = capture_open_gmail_email()
        return {
            "summary": summarize_email(content),
            "source": "Active Gmail message in Google Chrome",
        }

    @staticmethod
    def _unsupported(action: Action) -> ActionResult:
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.BLOCKED,
            error=(
                f"{action.type.value} requires a dedicated application adapter "
                "and is not enabled"
            ),
        )
