"""AI Work handlers - contextual AI assistance for each domain."""

from pathlib import Path

from telegram import ForceReply

from src.logging_config import logger
from .base import BaseHandler


DOMAIN_LABELS = {
    "scheduler": "스케줄러",
    "workspace": "워크스페이스",
    "calendar": "캘린더",
    "tasks": "작업 현황",
    "todo": "할일",
    "memo": "메모",
    "weather": "날씨",
    "diary": "일기",
}

# Domains handled by plugins (via plugin.get_ai_context)
PLUGIN_DOMAINS = {"todo", "memo", "weather", "diary", "calendar"}

# Domains handled by core handlers (via md file + _ctx_ methods)
CORE_DOMAINS = {"scheduler", "workspace", "tasks"}


class AiWorkHandlers(BaseHandler):
    """Contextual AI assistance - '✨ AI와 작업하기' feature."""

    def _load_core_context(self, domain: str) -> str:
        """Load static AI context markdown for a core domain."""
        context_dir = Path(__file__).parent.parent / "ai_contexts"
        context_path = context_dir / f"{domain}.md"
        if context_path.exists():
            return context_path.read_text(encoding="utf-8")
        return ""

    async def _handle_aiwork_callback(self, query, chat_id: int, callback_data: str) -> None:
        """Handle aiwork:{domain} callback - show ForceReply prompt."""
        domain = callback_data.split(":", 1)[1] if ":" in callback_data else ""
        label = DOMAIN_LABELS.get(domain, domain)

        await query.message.reply_text(
            f"✨ <b>{label} - AI와 작업하기</b>\n\n"
            f"무엇을 도와드릴까요?\n"
            f"<i>현재 {label} 데이터를 AI에게 전달합니다.</i>\n\n"
            f"<code>aiwork:{domain}</code>",
            parse_mode="HTML",
            reply_markup=ForceReply(
                selective=True,
                input_field_placeholder=f"{label} 관련 질문을 입력하세요",
            ),
        )

    async def _handle_aiwork_force_reply(
        self, update, chat_id: int, message: str, domain: str
    ) -> None:
        """Gather domain context and dispatch to AI."""
        user_id = str(chat_id)
        label = DOMAIN_LABELS.get(domain, domain)
        context_text = await self._gather_domain_context(chat_id, domain)

        augmented_message = (
            f"[참고 정보 - {label}]\n"
            f"{context_text}\n\n"
            f"위 정보를 참고하여 다음 요청에 답해주세요:\n"
            f"{message}"
        )

        await self._dispatch_to_ai(update, chat_id, user_id, augmented_message)

    async def _gather_domain_context(self, chat_id: int, domain: str) -> str:
        """Gather context: plugin domains delegate to plugin, core domains use md + dynamic."""
        try:
            if domain in PLUGIN_DOMAINS:
                return await self._gather_plugin_context(chat_id, domain)
            if domain in CORE_DOMAINS:
                return await self._gather_core_context(chat_id, domain)
            return "(알 수 없는 도메인)"
        except Exception as e:
            logger.error(f"Context gathering error for {domain}: {e}", exc_info=True)
            return f"(데이터 수집 중 오류: {e})"

    async def _gather_plugin_context(self, chat_id: int, domain: str) -> str:
        """Delegate context gathering to the plugin."""
        if not self.plugins:
            return "(플러그인 없음)"
        plugin = self.plugins.get_plugin_by_name(domain)
        if not plugin:
            return f"({domain} 플러그인 없음)"
        return await plugin.get_ai_context(chat_id)

    async def _gather_core_context(self, chat_id: int, domain: str) -> str:
        """Load core context: static md file + dynamic data."""
        static = self._load_core_context(domain)

        dynamic_gatherers = {
            "scheduler": self._ctx_scheduler,
            "workspace": self._ctx_workspace,
            "tasks": self._ctx_tasks,
        }
        gatherer = dynamic_gatherers.get(domain)
        dynamic = await gatherer(chat_id) if gatherer else ""

        if dynamic:
            return f"{static}\n\n[현재 데이터]\n{dynamic}"
        return static

    # --- Core domain dynamic data gatherers ---

    async def _ctx_scheduler(self, chat_id: int) -> str:
        repo = self._repository
        if not repo:
            return "(데이터 없음)"
        schedules = repo.list_schedules_by_user(str(chat_id))
        if not schedules:
            return "등록된 스케줄이 없습니다."
        lines = []
        for s in schedules:
            status = "ON" if s.enabled else "OFF"
            lines.append(f"- [{status}] {s.name} (유형: {s.schedule_type}, 시간: {s.trigger_summary})")
        return "\n".join(lines)

    async def _ctx_workspace(self, chat_id: int) -> str:
        repo = self._repository
        if not repo:
            return "(데이터 없음)"
        workspaces = repo.list_workspaces_by_user(str(chat_id))
        if not workspaces:
            return "등록된 워크스페이스가 없습니다."
        lines = []
        for ws in workspaces:
            lines.append(f"- {ws.name} ({ws.short_path})")
        return "\n".join(lines)

    async def _ctx_tasks(self, chat_id: int) -> str:
        repo = self._repository
        if not repo:
            return "(데이터 없음)"
        processing = repo.list_processing_messages_by_user(str(chat_id))
        queued = repo.list_queued_messages_by_user(str(chat_id))
        lines = []
        if processing:
            lines.append(f"진행 중인 작업: {len(processing)}개")
            for msg in processing:
                lines.append(f"  - {msg.get('request', '')[:50]}")
        else:
            lines.append("진행 중인 작업 없음")
        if queued:
            lines.append(f"대기 중인 메시지: {len(queued)}개")
        else:
            lines.append("대기 중인 메시지 없음")
        return "\n".join(lines)
