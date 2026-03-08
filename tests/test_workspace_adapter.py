"""Workspace adapter tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.claude.client import ChatResponse
from src.repository.adapters.workspace_adapter import WorkspaceRegistryAdapter


class TestWorkspaceRegistryAdapter:
    """Workspace recommendation behavior tests."""

    @pytest.mark.asyncio
    async def test_recommend_paths_uses_injected_recommendation_client(self, tmp_path):
        repo = MagicMock()
        repo.list_workspaces_by_user.return_value = []

        candidate = tmp_path / "project-alpha"
        candidate.mkdir()

        recommendation_client = MagicMock()
        recommendation_client.chat = AsyncMock(
            return_value=ChatResponse(
                text=(
                    '[{"path": "'
                    f'{candidate}'
                    '", "name": "project-alpha", "description": "추천", "reason": "match"}]'
                ),
                error=None,
                session_id=None,
            )
        )

        adapter = WorkspaceRegistryAdapter(
            repo,
            recommendation_client=recommendation_client,
        )

        results = await adapter.recommend_paths(
            user_id="12345",
            purpose="alpha project",
            allowed_patterns=[str(candidate)],
        )

        assert results == [
            {
                "path": str(candidate),
                "name": "project-alpha",
                "description": "추천",
                "reason": "match",
            }
        ]
        recommendation_client.chat.assert_awaited_once()
