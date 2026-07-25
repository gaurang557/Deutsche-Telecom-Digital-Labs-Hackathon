import platform
from pathlib import Path

_SYSTEM_FOLDER_NAMES = {
    "desktop": "Desktop",
    "documents": "Documents",
    "downloads": "Downloads",
    "home": "",
}


def discover_system_folders(home: Path | None = None) -> dict[str, Path]:
    """Return user-facing system folders that actually exist on this machine."""
    user_home = (home or Path.home()).expanduser().resolve()
    folders: dict[str, Path] = {}
    for alias, child in _SYSTEM_FOLDER_NAMES.items():
        candidate = user_home / child if child else user_home
        if candidate.is_dir():
            folders[alias] = candidate
    return folders


def planner_folder_context(home: Path | None = None) -> str:
    """Describe available folders for the local planner without inventing paths."""
    folders = discover_system_folders(home)
    if not folders:
        return "No standard user folders could be discovered."
    paths = ", ".join(f"{name.title()}={path}" for name, path in folders.items())
    return (
        f"Available folders on this {platform.system()} computer: {paths}. "
        "When a file is inside one of these folders, use this absolute path."
    )


def resolve_user_path(
    value: str,
    *,
    relative_to: Path | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve paths such as Downloads/report.pdf against the user's home."""
    raw = value.strip()
    if not raw:
        raise ValueError("File path cannot be empty")

    # Accept either separator from an LLM, regardless of the current platform.
    normalized = raw.replace("\\", "/")
    candidate = Path(normalized).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    parts = candidate.parts
    folders = discover_system_folders(home)
    if parts:
        system_folder = folders.get(parts[0].casefold())
        if system_folder is not None:
            return system_folder.joinpath(*parts[1:]).resolve()

    base = relative_to or Path.cwd()
    return (base / candidate).resolve()
