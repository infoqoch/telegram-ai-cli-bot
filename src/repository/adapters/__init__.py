"""Adapters for backward compatibility with existing code."""

from .schedule_adapter import ScheduleManagerAdapter
from .workspace_adapter import WorkspaceRegistryAdapter

__all__ = [
    "ScheduleManagerAdapter",
    "WorkspaceRegistryAdapter",
]
