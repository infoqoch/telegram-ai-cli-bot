"""Todo 플러그인 - Repository 기반 할일 관리."""

import re
from datetime import date, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply

from src.plugins.loader import Plugin, PluginResult
from src.logging_config import logger


class TodoPlugin(Plugin):
    """버튼 기반 할일 관리 플러그인."""

    name = "todo"
    description = "버튼 기반 할일 관리 (오전/오후/저녁)"
    usage = (
        "📋 <b>할일 플러그인 사용법</b>\n\n"
        "<b>시작하기</b>\n"
        "• <code>/todo</code> 또는 <code>할일</code> 입력\n\n"
        "<b>기능</b>\n"
        "• 📄 리스트 - 오늘 할일 보기\n"
        "• ➕ 추가 - 시간대 선택 후 할일 입력\n"
        "• 항목 클릭 - 완료/삭제/이동"
    )

    TRIGGER_KEYWORDS = ["todo", "할일", "투두"]

    EXCLUDE_PATTERNS = [
        r"(란|이란|가|이)\s*(뭐|무엇|뭔)",
        r"영어로|번역|translate",
        r"어떻게|왜|언제|어디",
        r"알려줘|설명|뜻",
    ]

    CALLBACK_PREFIX = "td:"

    SLOTS = ["morning", "afternoon", "evening"]
    SLOT_NAMES = {
        "morning": "🌅 오전",
        "afternoon": "☀️ 오후",
        "evening": "🌙 저녁",
    }
    SLOT_CODES = {"morning": "m", "afternoon": "a", "evening": "e"}
    CODE_TO_SLOT = {"m": "morning", "a": "afternoon", "e": "evening"}

    def __init__(self):
        super().__init__()
        # 멀티 선택 상태 (chat_id -> set of todo_ids)
        self._multi_selections: dict[int, set[int]] = {}

    async def can_handle(self, message: str, chat_id: int) -> bool:
        """할일 관련 메시지인지 확인."""
        msg = message.strip().lower()

        for pattern in self.EXCLUDE_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return False

        for keyword in self.TRIGGER_KEYWORDS:
            if msg.startswith(keyword):
                return True

        return False

    async def handle(self, message: str, chat_id: int) -> PluginResult:
        """메시지 처리 - 리스트 표시."""
        logger.info(f"Todo 플러그인 처리: '{message[:50]}' (chat_id={chat_id})")
        result = self._handle_list(chat_id)
        return PluginResult(
            handled=True,
            response=result["text"],
            reply_markup=result.get("reply_markup"),
        )

    def handle_callback(self, callback_data: str, chat_id: int) -> dict:
        """callback_data 처리."""
        logger.info(f"Todo callback: {callback_data} (chat_id={chat_id})")

        parts = callback_data.split(":")
        if len(parts) < 2:
            return {"text": "❌ 잘못된 요청", "edit": True}

        action = parts[1]
        handlers = {
            "list": lambda: self._handle_list(chat_id),
            "add": lambda: self._handle_add_menu(chat_id),
            "add_slot": lambda: self._handle_add_slot(chat_id, parts[2] if len(parts) > 2 else "m"),
            "item": lambda: self._handle_item_menu(chat_id, int(parts[2]) if len(parts) > 2 else 0),
            "done": lambda: self._handle_done(chat_id, int(parts[2]) if len(parts) > 2 else 0),
            "del": lambda: self._handle_delete(chat_id, int(parts[2]) if len(parts) > 2 else 0),
            "move": lambda: self._handle_move(chat_id, int(parts[2]) if len(parts) > 2 else 0, parts[3] if len(parts) > 3 else "a"),
            "tomorrow": lambda: self._handle_tomorrow(chat_id, int(parts[2]) if len(parts) > 2 else 0),
            "back": lambda: self._handle_list(chat_id),
            "multi": lambda: self._handle_multi_select(chat_id),
            "multi_toggle": lambda: self._handle_multi_toggle(chat_id, int(parts[2]) if len(parts) > 2 else 0),
            "multi_done": lambda: self._handle_multi_done(chat_id),
            "multi_del": lambda: self._handle_multi_delete(chat_id),
            "multi_carry": lambda: self._handle_multi_carry(chat_id),
            "multi_clear": lambda: self._handle_multi_clear(chat_id),
            "date": lambda: self._handle_date_view(chat_id, parts[2] if len(parts) > 2 else None),
            "week": lambda: self._handle_week_view(chat_id, parts[2] if len(parts) > 2 else None),
        }

        handler = handlers.get(action)
        if handler:
            return handler()
        return {"text": "❌ 알 수 없는 명령", "edit": True}

    def _today(self) -> str:
        return date.today().isoformat()

    def _handle_list(self, chat_id: int) -> dict:
        """할일 리스트 표시."""
        today = self._today()
        todos = self.repository.list_todos_by_date(chat_id, today)

        lines = [f"📋 <b>{today} 할일</b>\n"]
        buttons = []

        # 슬롯별 그룹화
        by_slot = {s: [] for s in self.SLOTS}
        for todo in todos:
            by_slot[todo.slot].append(todo)

        idx = 0
        for slot in self.SLOTS:
            items = by_slot[slot]
            if items:
                lines.append(f"\n<b>{self.SLOT_NAMES[slot]}</b>")
                for todo in items:
                    idx += 1
                    status = "✅" if todo.done else "⬜"
                    lines.append(f"{status} {idx}. {todo.text}")

                    if not todo.done:
                        preview = todo.text[:20] + "..." if len(todo.text) > 20 else todo.text
                        buttons.append([
                            InlineKeyboardButton(
                                f"{idx}. {preview}",
                                callback_data=f"td:item:{todo.id}"
                            )
                        ])

        stats = self.repository.get_todo_stats(chat_id, today)
        if stats["total"] == 0:
            lines.append("\n등록된 할일이 없어요.")
        else:
            lines.append(f"\n📊 {stats['done']}/{stats['total']} 완료")

        if stats["pending"] > 0:
            buttons.append([
                InlineKeyboardButton("📋 멀티선택", callback_data="td:multi"),
            ])

        # 날짜 이동
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        buttons.append([
            InlineKeyboardButton("◀️ 어제", callback_data=f"td:date:{yesterday}"),
            InlineKeyboardButton("📅 주간", callback_data=f"td:week:{today}"),
            InlineKeyboardButton("내일 ▶️", callback_data=f"td:date:{tomorrow}"),
        ])
        buttons.append([
            InlineKeyboardButton("➕ 추가", callback_data="td:add"),
            InlineKeyboardButton("🔄 새로고침", callback_data="td:list"),
        ])

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_add_menu(self, chat_id: int) -> dict:
        """시간대 선택 메뉴."""
        keyboard = [
            [
                InlineKeyboardButton("🌅 오전", callback_data="td:add_slot:m"),
                InlineKeyboardButton("☀️ 오후", callback_data="td:add_slot:a"),
                InlineKeyboardButton("🌙 저녁", callback_data="td:add_slot:e"),
            ],
            [InlineKeyboardButton("⬅️ 뒤로", callback_data="td:list")],
        ]
        return {
            "text": "⏰ <b>시간대 선택</b>\n\n할일을 추가할 시간대를 선택하세요.",
            "reply_markup": InlineKeyboardMarkup(keyboard),
            "edit": True,
        }

    def _handle_add_slot(self, chat_id: int, slot_code: str) -> dict:
        """할일 추가 ForceReply."""
        slot = self.CODE_TO_SLOT.get(slot_code, "morning")
        slot_name = self.SLOT_NAMES[slot]
        return {
            "text": f"{slot_name} <b>할일 입력</b>\n\n여러 개 입력 시 줄바꿈으로 구분하세요.",
            "force_reply": ForceReply(selective=True, input_field_placeholder="할일 입력..."),
            "slot_code": slot_code,
            "edit": False,
        }

    def _handle_item_menu(self, chat_id: int, todo_id: int) -> dict:
        """항목 상세 메뉴."""
        todo = self.repository.get_todo(todo_id)
        if not todo:
            return {"text": "❌ 항목을 찾을 수 없어요.", "edit": True}

        slot_name = self.SLOT_NAMES.get(todo.slot, todo.slot)
        other_slots = [c for c in ["m", "a", "e"] if self.CODE_TO_SLOT[c] != todo.slot]
        move_buttons = [
            InlineKeyboardButton(
                f"➡️ {self.SLOT_NAMES[self.CODE_TO_SLOT[c]]}",
                callback_data=f"td:move:{todo_id}:{c}"
            )
            for c in other_slots
        ]

        keyboard = [
            [
                InlineKeyboardButton("✅ 완료", callback_data=f"td:done:{todo_id}"),
                InlineKeyboardButton("🗑️ 삭제", callback_data=f"td:del:{todo_id}"),
            ],
            move_buttons,
            [InlineKeyboardButton("📅 내일로", callback_data=f"td:tomorrow:{todo_id}")],
            [InlineKeyboardButton("⬅️ 뒤로", callback_data="td:list")],
        ]

        return {
            "text": f"{slot_name} 할일\n\n<b>{todo.text}</b>",
            "reply_markup": InlineKeyboardMarkup(keyboard),
            "edit": True,
        }

    def _handle_done(self, chat_id: int, todo_id: int) -> dict:
        """완료 처리."""
        if self.repository.mark_todo_done(todo_id):
            result = self._handle_list(chat_id)
            result["text"] = "✅ 완료 처리됨!\n\n" + result["text"]
            return result
        return {"text": "❌ 처리 실패", "edit": True}

    def _handle_delete(self, chat_id: int, todo_id: int) -> dict:
        """삭제 처리."""
        if self.repository.delete_todo(todo_id):
            result = self._handle_list(chat_id)
            result["text"] = "🗑️ 삭제됨!\n\n" + result["text"]
            return result
        return {"text": "❌ 삭제 실패", "edit": True}

    def _handle_move(self, chat_id: int, todo_id: int, target_code: str) -> dict:
        """시간대 이동."""
        target_slot = self.CODE_TO_SLOT.get(target_code, "afternoon")
        todo = self.repository.get_todo(todo_id)
        if not todo:
            return {"text": "❌ 항목을 찾을 수 없어요.", "edit": True}

        # 삭제 후 새로 추가 (슬롯 변경)
        self.repository.delete_todo(todo_id)
        self.repository.add_todo(chat_id, todo.date, target_slot, todo.text)

        result = self._handle_list(chat_id)
        result["text"] = f"➡️ {self.SLOT_NAMES[target_slot]}으로 이동!\n\n" + result["text"]
        return result

    def _handle_tomorrow(self, chat_id: int, todo_id: int) -> dict:
        """내일로 이동."""
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        if self.repository.move_todos_to_date([todo_id], tomorrow):
            result = self._handle_list(chat_id)
            result["text"] = "📅 내일로 이동!\n\n" + result["text"]
            return result
        return {"text": "❌ 이동 실패", "edit": True}

    # ==================== 멀티 선택 ====================

    def _handle_multi_select(self, chat_id: int) -> dict:
        """멀티 선택 모드."""
        self._multi_selections[chat_id] = set()
        return self._render_multi_view(chat_id)

    def _render_multi_view(self, chat_id: int) -> dict:
        """멀티 선택 화면."""
        today = self._today()
        pending = self.repository.get_pending_todos(chat_id, today)
        selections = self._multi_selections.get(chat_id, set())

        if not pending:
            return {
                "text": "✅ 미완료 할일이 없어요!",
                "reply_markup": InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ 뒤로", callback_data="td:list")
                ]]),
                "edit": True,
            }

        lines = ["📋 <b>멀티 선택</b>\n", "항목을 터치해서 선택/해제하세요.\n"]
        buttons = []
        current_slot = None

        for todo in pending:
            if todo.slot != current_slot:
                current_slot = todo.slot
                lines.append(f"\n<b>{self.SLOT_NAMES[todo.slot]}</b>")

            selected = todo.id in selections
            mark = "☑️" if selected else "⬜"
            lines.append(f"{mark} {todo.text}")

            preview = todo.text[:18] + "..." if len(todo.text) > 18 else todo.text
            buttons.append([
                InlineKeyboardButton(
                    f"{'☑️' if selected else '⬜'} {preview}",
                    callback_data=f"td:multi_toggle:{todo.id}"
                )
            ])

        count = len(selections)
        lines.append(f"\n📌 {count}개 선택됨")

        if count > 0:
            buttons.append([
                InlineKeyboardButton(f"✅ 완료({count})", callback_data="td:multi_done"),
                InlineKeyboardButton(f"🗑️ 삭제({count})", callback_data="td:multi_del"),
                InlineKeyboardButton(f"📅 내일({count})", callback_data="td:multi_carry"),
            ])

        buttons.append([
            InlineKeyboardButton("🔄 선택해제", callback_data="td:multi_clear"),
            InlineKeyboardButton("⬅️ 뒤로", callback_data="td:list"),
        ])

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_multi_toggle(self, chat_id: int, todo_id: int) -> dict:
        """선택 토글."""
        if chat_id not in self._multi_selections:
            self._multi_selections[chat_id] = set()

        if todo_id in self._multi_selections[chat_id]:
            self._multi_selections[chat_id].discard(todo_id)
        else:
            self._multi_selections[chat_id].add(todo_id)

        return self._render_multi_view(chat_id)

    def _handle_multi_done(self, chat_id: int) -> dict:
        """선택 항목 완료."""
        selections = self._multi_selections.get(chat_id, set())
        count = 0
        for todo_id in selections:
            if self.repository.mark_todo_done(todo_id):
                count += 1

        self._multi_selections.pop(chat_id, None)
        result = self._handle_list(chat_id)
        result["text"] = f"✅ {count}개 완료 처리!\n\n" + result["text"]
        return result

    def _handle_multi_delete(self, chat_id: int) -> dict:
        """선택 항목 삭제."""
        selections = self._multi_selections.get(chat_id, set())
        count = 0
        for todo_id in selections:
            if self.repository.delete_todo(todo_id):
                count += 1

        self._multi_selections.pop(chat_id, None)
        result = self._handle_list(chat_id)
        result["text"] = f"🗑️ {count}개 삭제됨!\n\n" + result["text"]
        return result

    def _handle_multi_carry(self, chat_id: int) -> dict:
        """선택 항목 내일로."""
        selections = self._multi_selections.get(chat_id, set())
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        count = self.repository.move_todos_to_date(list(selections), tomorrow)

        self._multi_selections.pop(chat_id, None)
        result = self._handle_list(chat_id)
        result["text"] = f"📅 {count}개 내일로 이동!\n\n" + result["text"]
        return result

    def _handle_multi_clear(self, chat_id: int) -> dict:
        """선택 초기화."""
        self._multi_selections.pop(chat_id, None)
        return self._render_multi_view(chat_id)

    # ==================== 날짜 이동 ====================

    def _handle_date_view(self, chat_id: int, date_str: str | None) -> dict:
        """특정 날짜 조회."""
        try:
            target = date.fromisoformat(date_str) if date_str else date.today()
        except ValueError:
            target = date.today()

        target_str = target.isoformat()
        todos = self.repository.list_todos_by_date(chat_id, target_str)
        is_today = target == date.today()
        date_label = "오늘" if is_today else target.strftime("%m/%d")

        lines = [f"📋 <b>{target_str} ({date_label}) 할일</b>\n"]

        by_slot = {s: [] for s in self.SLOTS}
        for todo in todos:
            by_slot[todo.slot].append(todo)

        total, done_count = 0, 0
        for slot in self.SLOTS:
            items = by_slot[slot]
            if items:
                lines.append(f"\n<b>{self.SLOT_NAMES[slot]}</b>")
                for todo in items:
                    total += 1
                    status = "✅" if todo.done else "⬜"
                    if todo.done:
                        done_count += 1
                    lines.append(f"{status} {todo.text}")

        if total == 0:
            lines.append("\n등록된 할일이 없어요.")
        else:
            lines.append(f"\n📊 {done_count}/{total} 완료")

        prev_date = (target - timedelta(days=1)).isoformat()
        next_date = (target + timedelta(days=1)).isoformat()

        buttons = [
            [
                InlineKeyboardButton("◀️ 이전", callback_data=f"td:date:{prev_date}"),
                InlineKeyboardButton("📅 오늘", callback_data="td:list"),
                InlineKeyboardButton("다음 ▶️", callback_data=f"td:date:{next_date}"),
            ],
            [InlineKeyboardButton("📅 주간", callback_data=f"td:week:{target_str}")],
        ]

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_week_view(self, chat_id: int, date_str: str | None) -> dict:
        """주간 뷰."""
        try:
            center = date.fromisoformat(date_str) if date_str else date.today()
        except ValueError:
            center = date.today()

        start = center - timedelta(days=3)
        end = center + timedelta(days=3)
        today = date.today()

        todos_by_date = self.repository.get_todos_by_date_range(
            chat_id, start.isoformat(), end.isoformat()
        )

        lines = [f"📅 <b>주간 할일</b> ({start.strftime('%m/%d')} ~ {end.strftime('%m/%d')})\n"]
        buttons = []
        row = []

        current = start
        while current <= end:
            d_str = current.isoformat()
            is_today = current == today
            day_mark = "👉 " if is_today else ""
            weekday = ["월", "화", "수", "목", "금", "토", "일"][current.weekday()]

            todos = todos_by_date.get(d_str, [])
            total = len(todos)
            done = sum(1 for t in todos if t.done)

            if total == 0:
                status = "—"
            elif done == total:
                status = f"✅ {done}/{total}"
            else:
                status = f"⬜ {done}/{total}"

            lines.append(f"{day_mark}<b>{current.strftime('%m/%d')}({weekday})</b>: {status}")

            label = f"{'📍' if is_today else ''}{current.day}({weekday})"
            row.append(InlineKeyboardButton(label, callback_data=f"td:date:{d_str}"))
            if len(row) == 4:
                buttons.append(row)
                row = []

            current += timedelta(days=1)

        if row:
            buttons.append(row)

        prev_week = (center - timedelta(days=7)).isoformat()
        next_week = (center + timedelta(days=7)).isoformat()
        buttons.append([
            InlineKeyboardButton("◀️ 이전 주", callback_data=f"td:week:{prev_week}"),
            InlineKeyboardButton("📅 오늘", callback_data="td:list"),
            InlineKeyboardButton("다음 주 ▶️", callback_data=f"td:week:{next_week}"),
        ])

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    # ==================== ForceReply 처리 ====================

    def handle_force_reply(self, message: str, chat_id: int, slot_code: str) -> dict:
        """ForceReply 응답 - 할일 추가."""
        slot = self.CODE_TO_SLOT.get(slot_code, "morning")
        slot_name = self.SLOT_NAMES[slot]
        today = self._today()

        tasks = [t.strip() for t in message.split("\n") if t.strip()]
        if not tasks:
            return {"text": "❌ 할일이 입력되지 않았어요.", "reply_markup": None}

        for task_text in tasks:
            self.repository.add_todo(chat_id, today, slot, task_text)

        lines = [f"✅ {slot_name}에 {len(tasks)}개 추가됨!\n"]
        for task in tasks:
            lines.append(f"• {task}")

        keyboard = [[
            InlineKeyboardButton("📄 리스트 보기", callback_data="td:list"),
            InlineKeyboardButton("➕ 더 추가", callback_data="td:add"),
        ]]

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(keyboard),
        }
