"""Create the demo `.xlsx` fixtures the scripted walkthrough expects.

This is a DEMO HELPER, not part of the agent. It is the one place in the repo
allowed to know fixture-specific names (`results_blank.xlsx`, `Region`,
`Revenue`, `North`, `South`); nothing under `app/` or `windows_agent/` may.

The bundled fixture set ships the PDFs but no workbook, so the "read a value
out of a PDF and record it in a workbook" walkthrough has nothing to write
into. This recreates the missing workbook deterministically.

Usage (from the repo root):

    .venv\\Scripts\\python.exe tools\\make_demo_fixtures.py

By default it writes into the `fixtures` folder on the current user's Desktop.
Point it somewhere else with `--dir`, and pass `--force` to rebuild a workbook
that already exists — without `--force` an existing file is left untouched, so
re-running is safe and never destroys a workbook mid-demo.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from openpyxl import Workbook

#: Each fixture: file name -> (sheet title, rows written from A1 down).
#: Row one is the header; the remaining rows are labels with an empty value
#: column for the agent to fill in.
_FIXTURES: dict[str, tuple[str, list[list[object]]]] = {
    "results_blank.xlsx": (
        "Results",
        [
            ["Region", "Revenue"],
            ["North", None],
            ["South", None],
        ],
    ),
}


def default_fixture_dir() -> Path:
    """The `fixtures` folder on the current user's Desktop."""
    return Path.home() / "Desktop" / "fixtures"


def build_workbook(sheet_title: str, rows: list[list[object]]) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    for row in rows:
        ws.append(row)
    return wb


def write_fixture(path: Path, sheet_title: str, rows: list[list[object]], *, force: bool) -> str:
    """Create one fixture workbook. Returns a short status word for reporting."""
    if path.exists() and not force:
        return "skipped"
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    wb = build_workbook(sheet_title, rows)
    try:
        wb.save(str(path))
    finally:
        wb.close()
    return "replaced" if existed else "created"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dir",
        dest="directory",
        type=Path,
        default=None,
        help="Directory to write fixtures into (default: the Desktop 'fixtures' folder).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild a fixture that already exists instead of leaving it alone.",
    )
    args = parser.parse_args(argv)

    target_dir = args.directory or default_fixture_dir()
    print(f"Fixture directory: {target_dir}")

    for name, (sheet_title, rows) in sorted(_FIXTURES.items()):
        path = target_dir / name
        status = write_fixture(path, sheet_title, rows, force=args.force)
        if status == "skipped":
            print(f"  {name}: already exists, left untouched (use --force to rebuild)")
        else:
            print(f"  {name}: {status} (sheet {sheet_title!r}, {len(rows)} rows)")

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
