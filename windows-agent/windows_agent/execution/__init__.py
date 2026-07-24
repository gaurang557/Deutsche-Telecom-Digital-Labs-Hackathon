"""Execution wiring: registry + dispatcher + execution context."""

from .registry import ActionRegistration, ActionRegistry
from .dispatcher import Dispatcher
from .context import ExecutionContext

__all__ = ["ActionRegistration", "ActionRegistry", "Dispatcher", "ExecutionContext"]
