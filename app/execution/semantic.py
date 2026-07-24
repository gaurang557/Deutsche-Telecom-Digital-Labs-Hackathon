import platform
import subprocess
from typing import Protocol


class SemanticDesktop(Protocol):
    """OS-level desktop observations used instead of screen coordinates."""

    def is_application_running(self, application: str) -> bool | None: ...


class NativeSemanticDesktop:
    def is_application_running(self, application: str) -> bool | None:
        system = platform.system()
        if system == "Darwin":
            try:
                result = subprocess.Popen(
                    ["ps", "-axo", "comm="],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                stdout, _ = result.communicate(timeout=3)
                return application.casefold() in stdout.casefold()
            except (OSError, subprocess.SubprocessError):
                return None
        if system == "Windows":
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {application}"],
                capture_output=True,
                text=True,
                check=True,
            )
            return application.casefold() in result.stdout.casefold()
        return None
