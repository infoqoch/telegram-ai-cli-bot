"""메모 플러그인 - 버튼 기반 메모 저장/조회/삭제."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.plugins.loader import Plugin, PluginResult


class MemoPlugin(Plugin):
    """버튼 기반 메모 저장/조회/삭제 플러그인."""

    name = "memo"
    description = "메모 저장, 조회, 삭제"
    usage = (
        "📝 <b>메모 플러그인 사용법</b>\n\n"
        "<b>저장</b>\n"
        "• <code>OOO 메모해줘</code>\n"
        "• <code>메모: OOO</code>\n\n"
        "<b>조회</b>\n"
        "• <code>메모 보여줘</code>\n"
        "• <code>메모 목록</code>\n\n"
        "<b>삭제</b>\n"
        "• 목록에서 버튼 클릭"
    )

    # callback_data 접두사
    CALLBACK_PREFIX = "memo:"

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
    # 제외 패턴 - AI에게 넘겨야 하는 경우
    EXCLUDE_PATTERNS = [
        r"(란|이란|가|이)\s*(뭐|무엇|뭔)",  # "메모란 뭐야", "메모가 뭐야"
        r"영어로|번역|translate",            # 번역 요청
        r"어떻게|왜|언제|어디",              # 질문
        r"알려줘|설명|뜻",                   # 설명 요청
    ]

    async def can_handle(self, message: str, chat_id: int) -> bool:
        """메모 관련 메시지인지 확인."""
        msg = message.strip()

        # 제외 패턴 먼저 체크 - AI에게 넘김
        for pattern in self.EXCLUDE_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return False

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
                return self._delete_memo(chat_id, memo_id)

        # 목록 확인
        for pattern in self.LIST_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return self._list_memos(chat_id)

        # 저장 확인
        for pattern in self.SAVE_PATTERNS:
            match = re.search(pattern, msg, re.IGNORECASE)
            if match:
                content = match.group(1).strip()
                if content:
                    return self._save_memo(chat_id, content)

        return PluginResult(handled=False)

    # ==================== Callback 처리 ====================

    def handle_callback(self, callback_data: str, chat_id: int) -> dict:
        """callback_data 처리.

        Returns:
            dict with keys:
            - text: 응답 텍스트
            - reply_markup: InlineKeyboardMarkup (optional)
            - edit: bool - 기존 메시지 수정 여부
        """
        # memo:xxx 형식 파싱
        parts = callback_data.split(":")
        if len(parts) < 2:
            return {"text": "❌ 잘못된 요청", "edit": True}

        action = parts[1]

        if action == "list":
            return self._handle_list(chat_id)
        elif action == "del":
            # memo:del:1
            memo_id = int(parts[2]) if len(parts) > 2 else 0
            return self._handle_delete(chat_id, memo_id)
        elif action == "confirm_del":
            # memo:confirm_del:1
            memo_id = int(parts[2]) if len(parts) > 2 else 0
            return self._handle_confirm_delete(chat_id, memo_id)
        elif action == "cancel":
            return self._handle_list(chat_id)
        else:
            return {"text": "❌ 알 수 없는 명령", "edit": True}

    def _handle_list(self, chat_id: int) -> dict:
        """버튼 기반 메모 목록."""
        memos = self._load_memos(chat_id)

        if not memos:
            return {
                "text": "📭 저장된 메모가 없습니다.\n\n<code>OOO 메모해줘</code>로 메모를 저장하세요.",
                "edit": True,
            }

        lines = ["📝 <b>메모 목록</b>\n"]
        buttons = []

        for memo in memos:
            created = memo.get("created_at", "")[:10]  # YYYY-MM-DD
            lines.append(f"<b>#{memo['id']}</b> {memo['content']}\n<i>{created}</i>")

            # 각 메모에 삭제 버튼
            buttons.append([
                InlineKeyboardButton(
                    f"🗑️ #{memo['id']} 삭제",
                    callback_data=f"memo:del:{memo['id']}"
                )
            ])

        # 새로고침 버튼
        buttons.append([
            InlineKeyboardButton("🔄 새로고침", callback_data="memo:list")
        ])

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_delete(self, chat_id: int, memo_id: int) -> dict:
        """삭제 확인 화면."""
        memos = self._load_memos(chat_id)
        target = next((m for m in memos if m["id"] == memo_id), None)

        if not target:
            return {"text": f"❌ 메모 #{memo_id}을(를) 찾을 수 없습니다.", "edit": True}

        keyboard = [
            [
                InlineKeyboardButton("✅ 삭제", callback_data=f"memo:confirm_del:{memo_id}"),
                InlineKeyboardButton("❌ 취소", callback_data="memo:cancel"),
            ]
        ]

        return {
            "text": f"🗑️ <b>메모 삭제 확인</b>\n\n<b>#{target['id']}</b> {target['content']}\n\n정말 삭제하시겠습니까?",
            "reply_markup": InlineKeyboardMarkup(keyboard),
            "edit": True,
        }

    def _handle_confirm_delete(self, chat_id: int, memo_id: int) -> dict:
        """실제 삭제 수행."""
        memos = self._load_memos(chat_id)

        target = None
        for i, memo in enumerate(memos):
            if memo["id"] == memo_id:
                target = memos.pop(i)
                break

        if not target:
            return {"text": f"❌ 메모 #{memo_id}을(를) 찾을 수 없습니다.", "edit": True}

        self._save_memos(chat_id, memos)

        # 삭제 후 목록 표시
        result = self._handle_list(chat_id)
        result["text"] = f"🗑️ 삭제됨: <s>{target['content']}</s>\n\n" + result["text"]
        return result

    # ==================== 기존 메서드 (PluginResult 반환) ====================

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

    def _save_memo(self, chat_id: int, content: str) -> PluginResult:
        """메모 저장."""
        memos = self._load_memos(chat_id)

        memo = {
            "id": len(memos) + 1,
            "content": content,
            "created_at": datetime.now().isoformat(),
        }
        memos.append(memo)
        self._save_memos(chat_id, memos)

        # 저장 후 목록 버튼 추가
        keyboard = [[
            InlineKeyboardButton("📝 목록 보기", callback_data="memo:list")
        ]]

        return PluginResult(
            handled=True,
            response=f"📝 메모 저장됨!\n\n<b>#{memo['id']}</b> {content}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    def _list_memos(self, chat_id: int) -> PluginResult:
        """메모 목록 조회 (버튼 포함)."""
        result = self._handle_list(chat_id)
        return PluginResult(
            handled=True,
            response=result["text"],
            reply_markup=result.get("reply_markup")
        )

    def _delete_memo(self, chat_id: int, memo_id: int) -> PluginResult:
        """메모 삭제 (텍스트 명령용)."""
        memos = self._load_memos(chat_id)

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

        # 삭제 후 목록 버튼 추가
        keyboard = [[
            InlineKeyboardButton("📝 목록 보기", callback_data="memo:list")
        ]]

        return PluginResult(
            handled=True,
            response=f"🗑️ 메모 삭제됨\n\n<s>#{target['id']} {target['content']}</s>",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
