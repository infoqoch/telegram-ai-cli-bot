"""Adapters for backward compatibility with existing code."""

from .session_adapter import SessionStoreAdapter
from .schedule_adapter import ScheduleManagerAdapter
from .workspace_adapter import WorkspaceRegistryAdapter

__all__ = [
    "SessionStoreAdapter",
    "ScheduleManagerAdapter",
    "WorkspaceRegistryAdapter",
]
