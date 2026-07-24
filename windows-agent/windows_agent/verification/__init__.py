"""Verification registry.

After an executor runs, the dispatcher asks the VerificationRegistry to
independently re-observe state and confirm the action actually did what was
intended. Real verifiers (file/spreadsheet/...) arrive with their executors in
later milestones; the registry + interface are established here.
"""

from .base import Verifier
from .registry import VerificationRegistry

__all__ = ["Verifier", "VerificationRegistry"]
