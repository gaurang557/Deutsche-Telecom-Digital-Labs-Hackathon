"""Execution wiring: registry + dispatcher."""

from .registry import ActionRegistry
from .dispatcher import Dispatcher

__all__ = ["ActionRegistry", "Dispatcher"]
