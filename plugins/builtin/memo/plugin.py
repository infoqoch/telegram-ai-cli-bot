"""메모 플러그인 - 버튼 기반 단일 진입점."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply

from src.plugins.loader import Plugin, PluginResult


class MemoPlugin(Plugin):
    """버튼 기반 메모 플러그인 - 단일 진입점."""

    name = "memo"
    description = "메모 저장, 조회, 삭제"
    usage = (
        "📝 <b>메모 플러그인</b>\n\n"
        "<code>메모</code> 또는 <code>/memo</code> 입력"
    )

    # callback_data 접두사
    CALLBACK_PREFIX = "memo:"

    # 트리거 - 단일 진입점만
    TRIGGER_KEYWORDS = ["메모", "memo"]

    # 제외 패턴 - AI에게 넘겨야 하는 경우
    EXCLUDE_PATTERNS = [
        r"(란|이란|가|이)\s*(뭐|무엇|뭔)",
        r"영어로|번역|translate",
        r"어떻게|왜|언제|어디",
        r"알려줘|설명|뜻",
    ]

    async def can_handle(self, message: str, chat_id: int) -> bool:
        """메모 관련 메시지인지 확인 - 단일 키워드만."""
        msg = message.strip().lower()

        # 제외 패턴 체크
        for pattern in self.EXCLUDE_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return False

        # 정확히 키워드만
        for keyword in self.TRIGGER_KEYWORDS:
            if msg == keyword:
                return True

        return False

    async def handle(self, message: str, chat_id: int) -> PluginResult:
        """메모 메인 화면 표시."""
        result = self._handle_main(chat_id)
        return PluginResult(
            handled=True,
            response=result["text"],
            reply_markup=result.get("reply_markup")
        )

    # ==================== Callback 처리 ====================

    def handle_callback(self, callback_data: str, chat_id: int) -> dict:
        """callback_data 처리."""
        parts = callback_data.split(":")
        if len(parts) < 2:
            return {"text": "❌ 잘못된 요청", "edit": True}

        action = parts[1]

        if action == "main":
            return self._handle_main(chat_id)
        elif action == "list":
            return self._handle_list(chat_id)
        elif action == "add":
            return self._handle_add_prompt(chat_id)
        elif action == "del":
            memo_id = int(parts[2]) if len(parts) > 2 else 0
            return self._handle_delete(chat_id, memo_id)
        elif action == "confirm_del":
            memo_id = int(parts[2]) if len(parts) > 2 else 0
            return self._handle_confirm_delete(chat_id, memo_id)
        elif action == "cancel":
            return self._handle_list(chat_id)
        else:
            return {"text": "❌ 알 수 없는 명령", "edit": True}

    def _handle_main(self, chat_id: int) -> dict:
        """메인 메뉴."""
        memos = self._load_memos(chat_id)
        count = len(memos)

        buttons = [
            [
                InlineKeyboardButton("📄 목록", callback_data="memo:list"),
                InlineKeyboardButton("➕ 추가", callback_data="memo:add"),
            ]
        ]

        return {
            "text": f"📝 <b>메모</b>\n\n저장된 메모: {count}개",
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_list(self, chat_id: int) -> dict:
        """메모 목록."""
        memos = self._load_memos(chat_id)

        if not memos:
            buttons = [
                [InlineKeyboardButton("➕ 추가", callback_data="memo:add")],
                [InlineKeyboardButton("⬅️ 뒤로", callback_data="memo:main")],
            ]
            return {
                "text": "📭 저장된 메모가 없습니다.",
                "reply_markup": InlineKeyboardMarkup(buttons),
                "edit": True,
            }

        lines = ["📝 <b>메모 목록</b>\n"]
        buttons = []

        for memo in memos:
            created = memo.get("created_at", "")[:10]
            content_preview = memo['content'][:30] + "..." if len(memo['content']) > 30 else memo['content']
            lines.append(f"<b>#{memo['id']}</b> {memo['content']}\n<i>{created}</i>")

            buttons.append([
                InlineKeyboardButton(
                    f"🗑️ #{memo['id']} {content_preview[:15]}",
                    callback_data=f"memo:del:{memo['id']}"
                )
            ])

        buttons.append([
            InlineKeyboardButton("➕ 추가", callback_data="memo:add"),
            InlineKeyboardButton("🔄 새로고침", callback_data="memo:list"),
        ])
        buttons.append([
            InlineKeyboardButton("⬅️ 뒤로", callback_data="memo:main"),
        ])

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_add_prompt(self, chat_id: int) -> dict:
        """메모 추가 - ForceReply."""
        return {
            "text": "📝 <b>메모 추가</b>\n\n아래에 메모 내용을 입력하세요.",
            "force_reply": ForceReply(
                selective=True,
                input_field_placeholder="메모 내용 입력..."
            ),
            "force_reply_marker": "memo_add",
            "edit": False,
        }

    def _handle_delete(self, chat_id: int, memo_id: int) -> dict:
        """삭제 확인."""
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
            "text": f"🗑️ <b>삭제 확인</b>\n\n<b>#{target['id']}</b> {target['content']}\n\n정말 삭제?",
            "reply_markup": InlineKeyboardMarkup(keyboard),
            "edit": True,
        }

    def _handle_confirm_delete(self, chat_id: int, memo_id: int) -> dict:
        """삭제 실행."""
        memos = self._load_memos(chat_id)
        target = next((m for m in memos if m["id"] == memo_id), None)

        if not target:
            return {"text": f"❌ 메모 #{memo_id}을(를) 찾을 수 없습니다.", "edit": True}

        self._delete_memo(chat_id, memo_id)

        result = self._handle_list(chat_id)
        result["text"] = f"🗑️ 삭제됨: <s>{target['content'][:20]}</s>\n\n" + result["text"]
        return result

    # ==================== ForceReply 처리 ====================

    def handle_force_reply(self, message: str, chat_id: int) -> dict:
        """ForceReply 응답 처리 - 메모 추가."""
        content = message.strip()

        if not content:
            return {
                "text": "❌ 메모 내용이 비어있습니다.",
                "reply_markup": InlineKeyboardMarkup([[
                    InlineKeyboardButton("📝 다시 시도", callback_data="memo:add"),
                ]]),
            }

        memo = self._add_memo(chat_id, content)

        keyboard = [
            [
                InlineKeyboardButton("📄 목록", callback_data="memo:list"),
                InlineKeyboardButton("➕ 추가", callback_data="memo:add"),
            ]
        ]

        return {
            "text": f"✅ 메모 저장됨!\n\n<b>#{memo['id']}</b> {content}",
            "reply_markup": InlineKeyboardMarkup(keyboard),
        }

    # ==================== 유틸리티 ====================

    def _get_memo_file(self, chat_id: int) -> Path:
        """메모 파일 경로 (레거시 지원)."""
        data_dir = self.get_data_dir(self._base_dir)
        return data_dir / f"{chat_id}.json"

    def _load_memos(self, chat_id: int) -> list[dict]:
        """메모 로드 - Repository 우선, 폴백으로 JSON."""
        # Repository 사용 가능하면 사용
        if self.repository:
            memos = self.repository.list_memos(chat_id)
            return [{"id": m.id, "content": m.content, "created_at": m.created_at} for m in memos]

        # 레거시 JSON 폴백
        memo_file = self._get_memo_file(chat_id)
        if not memo_file.exists():
            return []
        try:
            return json.loads(memo_file.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_memos(self, chat_id: int, memos: list[dict]) -> None:
        """메모 저장 - Repository 사용 시 무시됨 (개별 add/delete 사용)."""
        # Repository 사용 시에는 이 메서드 호출 안됨
        if self.repository:
            return

        # 레거시 JSON 폴백
        memo_file = self._get_memo_file(chat_id)
        memo_file.write_text(
            json.dumps(memos, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def _add_memo(self, chat_id: int, content: str) -> dict:
        """메모 추가."""
        if self.repository:
            memo = self.repository.add_memo(chat_id, content)
            return {"id": memo.id, "content": memo.content, "created_at": memo.created_at}

        # 레거시
        memos = self._load_memos(chat_id)
        new_id = max([m["id"] for m in memos], default=0) + 1
        memo = {
            "id": new_id,
            "content": content,
            "created_at": datetime.now().isoformat(),
        }
        memos.append(memo)
        self._save_memos(chat_id, memos)
        return memo

    def _delete_memo(self, chat_id: int, memo_id: int) -> bool:
        """메모 삭제."""
        if self.repository:
            return self.repository.delete_memo(memo_id)

        # 레거시
        memos = self._load_memos(chat_id)
        for i, memo in enumerate(memos):
            if memo["id"] == memo_id:
                memos.pop(i)
                self._save_memos(chat_id, memos)
                return True
        return False
