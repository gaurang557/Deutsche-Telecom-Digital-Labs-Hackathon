"""Executor implementations.

`base.py` defines the generic async executor contract (BaseExecutor).
`common/` holds cross-platform executors; Milestone 0 only ships mock/test
doubles. Real file/pdf/spreadsheet/... executors and the platform-specific
`desktop/` adapters land in later milestones.
"""

from .base import BaseExecutor

__all__ = ["BaseExecutor"]
