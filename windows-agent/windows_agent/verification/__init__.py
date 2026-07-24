"""Verification registry.

After an executor runs, the dispatcher asks the VerificationRegistry to
independently re-observe state and confirm the action actually did what was
intended. Real verifiers (file/spreadsheet/...) arrive with their executors in
later milestones; the registry + interface are established here.
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
]
