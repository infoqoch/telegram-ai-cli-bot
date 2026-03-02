"""메모 플러그인 - 간단한 메모 저장/조회/삭제."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.plugins.loader import Plugin, PluginResult


class MemoPlugin(Plugin):
    """메모 저장/조회/삭제 플러그인."""

    name = "memo"
    description = "메모 저장, 조회, 삭제"

    # 트리거 패턴
    SAVE_PATTERNS = [
        r"(.+)\s*메모해줘",
        r"메모해줘\s*[:\-]?\s*(.+)",
        r"(.+)\s*저장해줘",
        r"메모\s*[:\-]\s*(.+)",
    ]
    LIST_PATTERNS = [
        r"메모\s*(목록|보여줘|리스트|확인)",
        r"메모들?\s*보여",
        r"저장된\s*메모",
    ]
    DELETE_PATTERNS = [
        r"메모\s*(\d+)\s*(삭제|지워)",
        r"(\d+)번?\s*메모\s*(삭제|지워)",
    ]

    async def can_handle(self, message: str, chat_id: int) -> bool:
        """메모 관련 메시지인지 확인."""
        msg = message.strip()

        # 저장 패턴
        for pattern in self.SAVE_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return True

        # 목록 패턴
        for pattern in self.LIST_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return True

        # 삭제 패턴
        for pattern in self.DELETE_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return True

        return False

    async def handle(self, message: str, chat_id: int) -> PluginResult:
        """메모 명령 처리."""
        msg = message.strip()

        # 삭제 확인 (목록보다 먼저)
        for pattern in self.DELETE_PATTERNS:
            match = re.search(pattern, msg, re.IGNORECASE)
            if match:
                memo_id = int(match.group(1))
                return await self._delete_memo(chat_id, memo_id)

        # 목록 확인
        for pattern in self.LIST_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return await self._list_memos(chat_id)

        # 저장 확인
        for pattern in self.SAVE_PATTERNS:
            match = re.search(pattern, msg, re.IGNORECASE)
            if match:
                content = match.group(1).strip()
                if content:
                    return await self._save_memo(chat_id, content)

        return PluginResult(handled=False)

    def _get_memo_file(self, chat_id: int) -> Path:
        """메모 파일 경로 반환."""
        data_dir = self.get_data_dir(self._base_dir)
        return data_dir / f"{chat_id}.json"

    def _load_memos(self, chat_id: int) -> list[dict]:
        """메모 목록 로드."""
        memo_file = self._get_memo_file(chat_id)
        if not memo_file.exists():
            return []
        try:
            return json.loads(memo_file.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_memos(self, chat_id: int, memos: list[dict]) -> None:
        """메모 목록 저장."""
        memo_file = self._get_memo_file(chat_id)
        memo_file.write_text(
            json.dumps(memos, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    async def _save_memo(self, chat_id: int, content: str) -> PluginResult:
        """메모 저장."""
        memos = self._load_memos(chat_id)

        memo = {
            "id": len(memos) + 1,
            "content": content,
            "created_at": datetime.now().isoformat(),
        }
        memos.append(memo)
        self._save_memos(chat_id, memos)

        return PluginResult(
            handled=True,
            response=f"📝 메모 저장됨!\n\n<b>#{memo['id']}</b> {content}"
        )

    async def _list_memos(self, chat_id: int) -> PluginResult:
        """메모 목록 조회."""
        memos = self._load_memos(chat_id)

        if not memos:
            return PluginResult(
                handled=True,
                response="📭 저장된 메모가 없습니다.\n\n<code>OOO 메모해줘</code>로 메모를 저장하세요."
            )

        lines = ["📝 <b>메모 목록</b>\n"]
        for memo in memos:
            created = memo.get("created_at", "")[:10]  # YYYY-MM-DD
            lines.append(f"<b>#{memo['id']}</b> {memo['content']}\n<i>{created}</i>")

        lines.append("\n<code>메모 N 삭제</code>로 삭제")

        return PluginResult(
            handled=True,
            response="\n".join(lines)
        )

    async def _delete_memo(self, chat_id: int, memo_id: int) -> PluginResult:
        """메모 삭제."""
        memos = self._load_memos(chat_id)

        # ID로 찾기
        target = None
        for i, memo in enumerate(memos):
            if memo["id"] == memo_id:
                target = memos.pop(i)
                break

        if not target:
            return PluginResult(
                handled=True,
                response=f"❌ 메모 #{memo_id}을(를) 찾을 수 없습니다."
            )

        self._save_memos(chat_id, memos)

        return PluginResult(
            handled=True,
            response=f"🗑️ 메모 삭제됨\n\n<s>#{target['id']} {target['content']}</s>"
        )
