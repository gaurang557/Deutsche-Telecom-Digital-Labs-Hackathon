"""Verification registry.

After an executor runs, the dispatcher asks the VerificationRegistry to
independently re-observe state and confirm the action actually did what was
intended. `file_verifiers.py` covers the `file.*` actions (M2);
`spreadsheet_verifiers.py` covers the modifying `spreadsheet.*` action (M4).
Read-only actions register no verifier (→ SKIPPED).
"""

from .base import Verifier
from .registry import VerificationRegistry
from .file_verifiers import (
    FILE_VERIFIERS,
    FileCopyVerifier,
    FileDeleteVerifier,
    FileMkdirVerifier,
    FileMoveVerifier,
    FileWriteVerifier,
    register_file_verifiers,
)
from .spreadsheet_verifiers import (
    SPREADSHEET_VERIFIERS,
    SpreadsheetWriteCellVerifier,
    register_spreadsheet_verifiers,
)

__all__ = [
    "Verifier",
    "VerificationRegistry",
    "FILE_VERIFIERS",
    "FileCopyVerifier",
    "FileMoveVerifier",
    "FileWriteVerifier",
    "FileMkdirVerifier",
    "FileDeleteVerifier",
    "register_file_verifiers",
    "SPREADSHEET_VERIFIERS",
    "SpreadsheetWriteCellVerifier",
    "register_spreadsheet_verifiers",
]
