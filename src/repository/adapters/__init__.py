"""Adapters for backward compatibility with existing code."""

from .plugin_storage import (
    RepositoryMemoStore,
    RepositoryPluginDatabase,
    RepositoryTodoStore,
    RepositoryWeatherLocationStore,
)
from .schedule_adapter import ScheduleManagerAdapter
from .workspace_adapter import WorkspaceRegistryAdapter

__all__ = [
    "RepositoryMemoStore",
    "RepositoryPluginDatabase",
    "RepositoryTodoStore",
    "RepositoryWeatherLocationStore",
    "ScheduleManagerAdapter",
    "WorkspaceRegistryAdapter",
]
