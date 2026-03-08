"""Workspace registry adapter for backward compatibility."""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

logger = logging.getLogger(__name__)

from ..repository import Repository, Workspace

if TYPE_CHECKING:
    from src.ai.client_types import AIClient


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
    """Repository-backed workspace registry plus recommendation helper."""

    def __init__(self, repo: Repository, recommendation_client: Optional["AIClient"] = None):
        self._repo = repo
        self._recommendation_client = recommendation_client

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
            return "No workspaces registered."

        lines = []
        for w in workspaces:
            use_indicator = "🔥" if w.use_count > 5 else "📂"
            lines.append(f"{use_indicator} <b>{w.name}</b>\n   <code>{w.short_path}</code>")

        return "\n\n".join(lines)

    def get_status_text(self, user_id: str) -> str:
        """Get workspace status text (alias for get_workspace_summary)."""
        return self.get_workspace_summary(user_id)

    async def recommend_paths(
        self,
        user_id: str,
        purpose: str,
        allowed_patterns: list[str],
    ) -> list[dict[str, str]]:
        """Recommend workspace paths based on purpose.

        1. Search registered workspaces by keywords/name/description
        2. Use the injected recommendation client for directory-name matching
        """
        purpose_lower = purpose.lower()
        results = []
        seen_paths = set()

        # 1. Search registered workspaces
        workspaces = self._repo.list_workspaces_by_user(user_id)
        for w in workspaces:
            score = 0
            for keyword in w.keywords:
                if keyword.lower() in purpose_lower:
                    score += 2
            if w.name.lower() in purpose_lower:
                score += 1
            if purpose_lower in w.description.lower():
                score += 1
            if score > 0:
                seen_paths.add(w.path)
                results.append({
                    "path": w.path,
                    "name": w.name,
                    "description": w.description,
                    "reason": "Registered workspace match"
                })

        if results:
            return results[:3]

        # 2. Use Claude AI to match purpose against directory names
        dir_list = []
        for dir_path in allowed_patterns:
            path = Path(dir_path)
            if path.is_dir() and str(path) not in seen_paths:
                dir_list.append(str(path))

        if not dir_list:
            return []

        ai_results = await self._ai_recommend(purpose, dir_list)
        return ai_results[:3]

    async def _ai_recommend(
        self, purpose: str, directories: list[str]
    ) -> list[dict[str, str]]:
        """Use the injected AI client to recommend directories matching purpose."""
        if not self._recommendation_client:
            logger.warning("AI recommend skipped: recommendation client not configured")
            return []

        dir_names = "\n".join(f"- {d}" for d in directories)
        prompt = (
            f"사용자가 '{purpose}' 목적의 워크스페이스를 찾고 있습니다.\n"
            f"아래 디렉토리 목록에서 가장 적합한 것을 최대 3개 골라주세요.\n"
            f"매칭되는 것이 없으면 빈 배열을 반환하세요.\n\n"
            f"디렉토리 목록:\n{dir_names}\n\n"
            f"JSON 배열로만 응답하세요. 다른 텍스트 없이:\n"
            f'[{{"path": "/full/path", "name": "표시이름", "description": "설명", "reason": "선택이유"}}]'
        )

        try:
            response = await self._recommendation_client.chat(
                message=prompt,
                session_id=None,
                model="sonnet",
                workspace_path=None,
            )
            if response.error or not response.text.strip():
                logger.warning(
                    "AI recommend failed: %s",
                    response.error.value if response.error else "empty response",
                )
                return []

            output = response.text.strip()

            # Extract JSON array from output
            start = output.find("[")
            end = output.rfind("]")
            if start == -1 or end == -1:
                logger.warning(f"AI recommend: no JSON array in output: {output[:100]}")
                return []

            parsed = json.loads(output[start:end + 1])
            # Validate paths exist
            valid = []
            for item in parsed:
                if Path(item["path"]).is_dir():
                    valid.append({
                        "path": item["path"],
                        "name": item.get("name", Path(item["path"]).name),
                        "description": item.get("description", ""),
                        "reason": item.get("reason", "AI recommendation"),
                    })
            return valid
        except Exception as e:
            logger.warning(f"AI recommend failed: {e}")
            return []
