"""Audit sink.

The dispatcher emits AuditEvents to an AuditSink. Milestone 1 provides an
in-memory sink (for tests) and a null sink (default). Central redaction and
SQLite persistence are wired in Milestone 11 — behind this same interface.
"""

from .sink import AuditSink, InMemoryAuditSink, NullAuditSink

__all__ = ["AuditSink", "InMemoryAuditSink", "NullAuditSink"]
