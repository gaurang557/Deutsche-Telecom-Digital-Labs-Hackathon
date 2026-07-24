"""Executor implementations.

`base.py` defines the generic async executor contract (BaseExecutor).
`common/` holds cross-platform test doubles (mock executors).
`file_ops.py` is the first real executor (the `file.*` vocabulary, Milestone 2).
`pdf_ops.py` is the read-only PDF executor (the `pdf.*` vocabulary, Milestone 3).
`spreadsheet_ops.py` is the `.xlsx` spreadsheet executor (the `spreadsheet.*`
vocabulary, Milestone 4). `document_ops.py` is the `.docx` Word-document executor
(the `document.*` vocabulary, Milestone 6). Presentation executors and the
platform-specific `desktop/` adapters land in later milestones.
"""

from .base import BaseExecutor
from .document_ops import (
    DOCUMENT_ACTION_TYPES,
    DocumentExecutor,
    register_document_executor,
)
from .file_ops import FILE_ACTION_TYPES, FileExecutor, register_file_executor
from .pdf_ops import PDF_ACTION_TYPES, PdfExecutor, register_pdf_executor
from .spreadsheet_ops import (
    SPREADSHEET_ACTION_TYPES,
    SpreadsheetExecutor,
    register_spreadsheet_executor,
)

__all__ = [
    "BaseExecutor",
    "FileExecutor",
    "FILE_ACTION_TYPES",
    "register_file_executor",
    "PdfExecutor",
    "PDF_ACTION_TYPES",
    "register_pdf_executor",
    "SpreadsheetExecutor",
    "SPREADSHEET_ACTION_TYPES",
    "register_spreadsheet_executor",
    "DocumentExecutor",
    "DOCUMENT_ACTION_TYPES",
    "register_document_executor",
]
