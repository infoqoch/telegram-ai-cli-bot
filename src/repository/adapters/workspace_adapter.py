"""Workspace registry adapter for backward compatibility."""

import os
from dataclasses import dataclass
from typing import Any, Optional

from ..repository import Repository, Workspace


@dataclass
class WorkspaceData:
    """Workspace data for backward compatibility."""
    id: str
    user_id: str
    path: str
    name: str
    description: str
    keywords: list[str]
    created_at: str
    last_used: Optional[str]
    use_count: int

    @property
    def short_path(self) -> str:
        """Return path with ~ for home directory."""
        home = os.path.expanduser("~")
        if self.path.startswith(home):
            return "~" + self.path[len(home):]
        return self.path

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "path": self.path,
            "name": self.name,
            "description": self.description,
            "keywords": self.keywords,
            "created_at": self.created_at,
            "last_used": self.last_used,
            "use_count": self.use_count,
        }

    @classmethod
    def from_repo_workspace(cls, w: Workspace) -> "WorkspaceData":
        return cls(
            id=w.id,
            user_id=w.user_id,
            path=w.path,
            name=w.name,
            description=w.description,
            keywords=w.keywords,
            created_at=w.created_at,
            last_used=w.last_used,
            use_count=w.use_count,
        )


class WorkspaceRegistryAdapter:
    """Adapter that provides WorkspaceRegistry-compatible interface over Repository.

    This adapter maintains the same API as the original WorkspaceRegistry class
    to ensure backward compatibility with existing code.
    """

    def __init__(self, repo: Repository):
        self._repo = repo

    def add(
        self,
        user_id: str,
        path: str,
        name: str,
        description: str = "",
        keywords: Optional[list[str]] = None
    ) -> WorkspaceData:
        """Add a new workspace."""
        # Check for duplicate
        existing = self._repo.get_workspace_by_path(path, user_id)
        if existing:
            raise ValueError(f"Workspace already exists: {path}")

        workspace = self._repo.add_workspace(
            user_id=user_id,
            path=path,
            name=name,
            description=description,
            keywords=keywords
        )

        return WorkspaceData.from_repo_workspace(workspace)

    def remove(self, workspace_id: str) -> bool:
        """Remove workspace."""
        return self._repo.remove_workspace(workspace_id)

    def get(self, workspace_id: str) -> Optional[WorkspaceData]:
        """Get workspace by ID."""
        workspace = self._repo.get_workspace(workspace_id)
        return WorkspaceData.from_repo_workspace(workspace) if workspace else None

    def get_by_path(
        self,
        path: str,
        user_id: Optional[str] = None
    ) -> Optional[WorkspaceData]:
        """Get workspace by path."""
        workspace = self._repo.get_workspace_by_path(path, user_id)
        return WorkspaceData.from_repo_workspace(workspace) if workspace else None

    def list_by_user(self, user_id: str) -> list[WorkspaceData]:
        """List workspaces for user."""
        workspaces = self._repo.list_workspaces_by_user(user_id)
        return [WorkspaceData.from_repo_workspace(w) for w in workspaces]

    def mark_used(self, workspace_id: str) -> None:
        """Mark workspace as used."""
        self._repo.mark_workspace_used(workspace_id)

    def update(
        self,
        workspace_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        keywords: Optional[list[str]] = None
    ) -> bool:
        """Update workspace details."""
        return self._repo.update_workspace(
            workspace_id=workspace_id,
            name=name,
            description=description,
            keywords=keywords
        )

    def get_workspace_summary(self, user_id: str) -> str:
        """Get workspace summary for display."""
        workspaces = self._repo.list_workspaces_by_user(user_id)

        if not workspaces:
            return "등록된 워크스페이스가 없습니다."

        lines = []
        for w in workspaces:
            use_indicator = "🔥" if w.use_count > 5 else "📂"
            lines.append(f"{use_indicator} <b>{w.name}</b>\n   <code>{w.short_path}</code>")

        return "\n\n".join(lines)

    async def recommend_paths(
        self,
        user_id: str,
        purpose: str,
        allowed_patterns: list[str],
        claude_client: Any = None
    ) -> list[dict[str, str]]:
        """Recommend workspace paths based on purpose.

        This is a simplified version that returns existing workspaces.
        The original implementation used AI to recommend paths.
        """
        workspaces = self._repo.list_workspaces_by_user(user_id)

        # Filter by keywords if any match purpose
        purpose_lower = purpose.lower()
        recommendations = []

        for w in workspaces:
            score = 0
            # Check keywords
            for keyword in w.keywords:
                if keyword.lower() in purpose_lower:
                    score += 2
            # Check name
            if w.name.lower() in purpose_lower:
                score += 1
            # Check description
            if purpose_lower in w.description.lower():
                score += 1

            if score > 0:
                recommendations.append((score, w))

        # Sort by score and return top 3
        recommendations.sort(key=lambda x: x[0], reverse=True)

        return [
            {
                "path": w.path,
                "name": w.name,
                "reason": f"키워드 매칭: {', '.join(w.keywords)}" if w.keywords else w.description
            }
            for _, w in recommendations[:3]
        ]
