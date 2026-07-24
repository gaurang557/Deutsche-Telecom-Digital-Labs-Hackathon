"""Executor implementations.

`base.py` defines the generic async executor contract (BaseExecutor).
`common/` holds cross-platform test doubles (mock executors).
`file_ops.py` is the first real executor (the `file.*` vocabulary, Milestone 2).
`pdf_ops.py` is the read-only PDF executor (the `pdf.*` vocabulary, Milestone 3).
Spreadsheet/document executors and the platform-specific `desktop/` adapters
land in later milestones.
"""

from .base import BaseExecutor
from .file_ops import FILE_ACTION_TYPES, FileExecutor, register_file_executor
from .pdf_ops import PDF_ACTION_TYPES, PdfExecutor, register_pdf_executor

__all__ = [
    "BaseExecutor",
    "FileExecutor",
    "FILE_ACTION_TYPES",
    "register_file_executor",
    "PdfExecutor",
    "PDF_ACTION_TYPES",
    "register_pdf_executor",
]
