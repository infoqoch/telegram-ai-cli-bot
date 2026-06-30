"""Adapters for backward compatibility with existing code."""

from .plugin_storage import (
    RepositoryDiaryStore,
    RepositoryCommandScheduleDraftStore,
    RepositoryMemoStore,
    RepositoryPluginDatabase,
    RepositoryTodoStore,
    RepositoryWeatherLocationStore,
)
from .schedule_adapter import ScheduleManagerAdapter
from .workspace_adapter import WorkspaceRegistryAdapter

__all__ = [
    "RepositoryDiaryStore",
    "RepositoryCommandScheduleDraftStore",
    "RepositoryMemoStore",
    "RepositoryPluginDatabase",
    "RepositoryTodoStore",
    "RepositoryWeatherLocationStore",
    "ScheduleManagerAdapter",
    "WorkspaceRegistryAdapter",
]
