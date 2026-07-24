"""Execution wiring: registry + dispatcher + execution context."""

from .registry import ActionRegistry
from .dispatcher import Dispatcher
from .context import ExecutionContext

__all__ = ["ActionRegistry", "Dispatcher", "ExecutionContext"]
