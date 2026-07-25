from pathlib import Path

from app.paths import (
    discover_system_folders,
    planner_folder_context,
    resolve_user_path,
)


def test_discovers_and_resolves_existing_standard_folder(tmp_path: Path) -> None:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    source = downloads / "report.txt"
    source.write_text("content")

    folders = discover_system_folders(tmp_path)
    resolved = resolve_user_path("Downloads/report.txt", home=tmp_path)

    assert folders["downloads"] == downloads
    assert resolved == source


def test_bare_destination_is_resolved_next_to_source(tmp_path: Path) -> None:
    source_folder = tmp_path / "Downloads"
    destination = resolve_user_path(
        "report-copy.txt",
        relative_to=source_folder,
        home=tmp_path,
    )

    assert destination == source_folder / "report-copy.txt"


def test_planner_context_only_lists_folders_that_exist(tmp_path: Path) -> None:
    (tmp_path / "Downloads").mkdir()

    context = planner_folder_context(tmp_path)

    assert f"Downloads={tmp_path / 'Downloads'}" in context
    assert "Documents=" not in context
