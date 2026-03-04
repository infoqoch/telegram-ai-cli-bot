"""Todo 플러그인 - 버튼 기반 할일 관리."""

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply

from src.plugins.loader import Plugin, PluginResult
from src.logging_config import logger

from plugins.builtin.todo.manager import TodoManager, TimeSlot, DailyTodo


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
        "• 항목 클릭 - 완료/삭제/이동\n\n"
        "<b>🕐 자동 알림</b>\n"
        "• 10:00 - 오전 할일 리마인더\n"
        "• 15:00 - 오후 할일 리마인더\n"
        "• 19:00 - 저녁 할일 리마인더"
    )

    # 트리거 패턴 - 명시적 키워드만
    TRIGGER_KEYWORDS = ["todo", "할일", "투두"]

    # 제외 패턴 - AI에게 넘김
    EXCLUDE_PATTERNS = [
        r"(란|이란|가|이)\s*(뭐|무엇|뭔)",
        r"영어로|번역|translate",
        r"어떻게|왜|언제|어디",
        r"알려줘|설명|뜻",
    ]

    # callback_data 접두사
    CALLBACK_PREFIX = "td:"

    # 시간대 매핑
    SLOT_MAP = {
        "m": TimeSlot.MORNING,
        "a": TimeSlot.AFTERNOON,
        "e": TimeSlot.EVENING,
    }
    SLOT_NAMES = {
        TimeSlot.MORNING: "🌅 오전",
        TimeSlot.AFTERNOON: "☀️ 오후",
        TimeSlot.EVENING: "🌙 저녁",
    }
    SLOT_CODES = {
        TimeSlot.MORNING: "m",
        TimeSlot.AFTERNOON: "a",
        TimeSlot.EVENING: "e",
    }

    def __init__(self):
        super().__init__()
        self._manager: Optional[TodoManager] = None

    @property
    def manager(self) -> TodoManager:
        """TodoManager 인스턴스 (지연 초기화)."""
        if self._manager is None:
            data_dir = self.get_data_dir(self._base_dir)
            self._manager = TodoManager(data_dir)
        return self._manager

    def set_manager(self, manager: TodoManager) -> None:
        """외부에서 매니저 주입 (스케줄러와 공유용)."""
        self._manager = manager

    async def can_handle(self, message: str, chat_id: int) -> bool:
        """할일 관련 메시지인지 확인 - 명시적 키워드만."""
        msg = message.strip().lower()

        # 제외 패턴 체크
        for pattern in self.EXCLUDE_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return False

        # 명시적 키워드로 시작하는지 체크
        for keyword in self.TRIGGER_KEYWORDS:
            if msg.startswith(keyword):
                return True

        return False

    async def handle(self, message: str, chat_id: int) -> PluginResult:
        """메시지 처리 - 바로 리스트 표시."""
        logger.info(f"Todo 플러그인 처리: '{message[:50]}' (chat_id={chat_id})")

        # 바로 리스트 표시
        result = self._handle_list(chat_id)
        return PluginResult(
            handled=True,
            response=result["text"],
            reply_markup=result.get("reply_markup"),
        )

    def get_main_menu_result(self, chat_id: int) -> PluginResult:
        """메인 메뉴 결과 반환."""
        keyboard = [
            [
                InlineKeyboardButton("📄 리스트", callback_data="td:list"),
                InlineKeyboardButton("➕ 추가", callback_data="td:add"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        return PluginResult(
            handled=True,
            response="📋 <b>할일 관리</b>",
            reply_markup=reply_markup,
        )

    # ==================== Callback 처리 메서드 ====================

    def handle_callback(self, callback_data: str, chat_id: int) -> dict:
        """callback_data 처리.

        Returns:
            dict with keys:
            - text: 응답 텍스트
            - reply_markup: InlineKeyboardMarkup (optional)
            - force_reply: ForceReply (optional)
            - edit: bool - 기존 메시지 수정 여부
        """
        logger.info(f"Todo callback: {callback_data} (chat_id={chat_id})")

        # td:xxx 형식 파싱
        parts = callback_data.split(":")
        if len(parts) < 2:
            return {"text": "❌ 잘못된 요청", "edit": True}

        action = parts[1]

        # 라우팅
        if action == "list":
            return self._handle_list(chat_id)
        elif action == "add":
            return self._handle_add_menu(chat_id)
        elif action == "add_slot":
            # td:add_slot:m
            slot_code = parts[2] if len(parts) > 2 else "m"
            return self._handle_add_slot(chat_id, slot_code)
        elif action == "item":
            # td:item:m:0
            slot_code = parts[2] if len(parts) > 2 else "m"
            index = int(parts[3]) if len(parts) > 3 else 0
            return self._handle_item_menu(chat_id, slot_code, index)
        elif action == "done":
            # td:done:m:0
            slot_code = parts[2] if len(parts) > 2 else "m"
            index = int(parts[3]) if len(parts) > 3 else 0
            return self._handle_done(chat_id, slot_code, index)
        elif action == "del":
            # td:del:m:0
            slot_code = parts[2] if len(parts) > 2 else "m"
            index = int(parts[3]) if len(parts) > 3 else 0
            return self._handle_delete(chat_id, slot_code, index)
        elif action == "move":
            # td:move:m:0:a
            slot_code = parts[2] if len(parts) > 2 else "m"
            index = int(parts[3]) if len(parts) > 3 else 0
            target_slot = parts[4] if len(parts) > 4 else "a"
            return self._handle_move(chat_id, slot_code, index, target_slot)
        elif action == "back":
            return self._handle_back(chat_id)
        # 멀티 선택 관련
        elif action == "multi":
            return self._handle_multi_select(chat_id)
        elif action == "multi_toggle":
            # td:multi_toggle:m:0
            slot_code = parts[2] if len(parts) > 2 else "m"
            index = int(parts[3]) if len(parts) > 3 else 0
            return self._handle_multi_toggle(chat_id, slot_code, index)
        elif action == "multi_done":
            return self._handle_multi_done(chat_id)
        elif action == "multi_del":
            return self._handle_multi_delete(chat_id)
        elif action == "multi_carry":
            return self._handle_multi_carry(chat_id)
        elif action == "multi_clear":
            return self._handle_multi_clear(chat_id)
        # 하루 마무리 관련
        elif action == "carry_all":
            return self._handle_carry_all(chat_id)
        elif action == "wrap_done":
            return self._handle_wrap_done(chat_id)
        # 날짜 이동 관련
        elif action == "date":
            # td:date:2026-03-04
            date_str = parts[2] if len(parts) > 2 else None
            return self._handle_date_view(chat_id, date_str)
        elif action == "week":
            # td:week:2026-03-04 (해당 날짜 기준 ±3일)
            date_str = parts[2] if len(parts) > 2 else None
            return self._handle_week_view(chat_id, date_str)
        elif action == "today_full":
            return self._handle_today_full(chat_id)
        else:
            return {"text": "❌ 알 수 없는 명령", "edit": True}

    # ==================== 멀티 선택 상태 관리 ====================

    _multi_selections: dict[int, set[tuple[str, int]]] = {}  # chat_id -> {(slot, index), ...}

    def _get_selections(self, chat_id: int) -> set[tuple[str, int]]:
        """현재 선택 상태."""
        return self._multi_selections.get(chat_id, set())

    def _toggle_selection(self, chat_id: int, slot_code: str, index: int) -> bool:
        """선택 토글. 새 상태 반환."""
        if chat_id not in self._multi_selections:
            self._multi_selections[chat_id] = set()

        key = (slot_code, index)
        if key in self._multi_selections[chat_id]:
            self._multi_selections[chat_id].discard(key)
            return False
        else:
            self._multi_selections[chat_id].add(key)
            return True

    def _clear_selections(self, chat_id: int) -> None:
        """선택 초기화."""
        self._multi_selections.pop(chat_id, None)

    def _handle_list(self, chat_id: int) -> dict:
        """할일 리스트 표시."""
        daily = self.manager.get_today(chat_id)
        all_tasks = daily.get_all_tasks()

        lines = [f"📋 <b>{daily.date} 할일</b>\n"]
        buttons = []
        global_index = 0

        for slot in [TimeSlot.MORNING, TimeSlot.AFTERNOON, TimeSlot.EVENING]:
            tasks = all_tasks.get(slot.value, [])
            slot_name = self.SLOT_NAMES[slot]
            slot_code = self.SLOT_CODES[slot]

            if tasks:
                lines.append(f"\n<b>{slot_name}</b>")
                for i, task in enumerate(tasks):
                    global_index += 1
                    status = "✅" if task.done else "⬜"
                    lines.append(f"{status} {global_index}. {task.text}")

                    # 미완료 항목만 버튼 추가
                    if not task.done:
                        buttons.append([
                            InlineKeyboardButton(
                                f"{global_index}. {task.text[:20]}{'...' if len(task.text) > 20 else ''}",
                                callback_data=f"td:item:{slot_code}:{i}"
                            )
                        ])

        pending = daily.get_pending_count()
        done = daily.get_done_count()
        total = pending + done

        if total == 0:
            lines.append("\n등록된 할일이 없어요.")
        else:
            lines.append(f"\n📊 {done}/{total} 완료")

        # 하단 버튼
        if pending > 0:
            buttons.append([
                InlineKeyboardButton("📋 멀티선택", callback_data="td:multi"),
            ])

        # 날짜 이동 버튼
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        tomorrow = (today + timedelta(days=1)).isoformat()
        buttons.append([
            InlineKeyboardButton("◀️ 어제", callback_data=f"td:date:{yesterday}"),
            InlineKeyboardButton("📅 주간", callback_data=f"td:week:{today.isoformat()}"),
            InlineKeyboardButton("내일 ▶️", callback_data=f"td:date:{tomorrow}"),
        ])
        buttons.append([
            InlineKeyboardButton("📋 오늘 전체", callback_data="td:today_full"),
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
            [
                InlineKeyboardButton("⬅️ 뒤로", callback_data="td:list"),
            ]
        ]

        return {
            "text": "⏰ <b>시간대 선택</b>\n\n할일을 추가할 시간대를 선택하세요.",
            "reply_markup": InlineKeyboardMarkup(keyboard),
            "edit": True,
        }

    def _handle_add_slot(self, chat_id: int, slot_code: str) -> dict:
        """특정 시간대에 할일 추가 - ForceReply 반환."""
        slot = self.SLOT_MAP.get(slot_code, TimeSlot.MORNING)
        slot_name = self.SLOT_NAMES[slot]

        return {
            "text": f"{slot_name} <b>할일 입력</b>\n\n여러 개 입력 시 줄바꿈으로 구분하세요.",
            "force_reply": ForceReply(
                selective=True,
                input_field_placeholder="할일 입력... (엔터로 구분)"
            ),
            "slot_code": slot_code,  # 저장용
            "edit": False,  # 새 메시지로 전송
        }

    def _handle_item_menu(self, chat_id: int, slot_code: str, index: int) -> dict:
        """항목 상세 메뉴."""
        slot = self.SLOT_MAP.get(slot_code, TimeSlot.MORNING)
        daily = self.manager.get_today(chat_id)
        tasks = daily.get_tasks(slot)

        if index >= len(tasks):
            return {"text": "❌ 항목을 찾을 수 없어요.", "edit": True}

        task = tasks[index]
        slot_name = self.SLOT_NAMES[slot]

        # 이동 대상 시간대
        other_slots = [s for s in ["m", "a", "e"] if s != slot_code]
        move_buttons = [
            InlineKeyboardButton(
                f"➡️ {self.SLOT_NAMES[self.SLOT_MAP[s]]}",
                callback_data=f"td:move:{slot_code}:{index}:{s}"
            )
            for s in other_slots
        ]

        keyboard = [
            [
                InlineKeyboardButton("✅ 완료", callback_data=f"td:done:{slot_code}:{index}"),
                InlineKeyboardButton("🗑️ 삭제", callback_data=f"td:del:{slot_code}:{index}"),
            ],
            move_buttons,
            [
                InlineKeyboardButton("⬅️ 뒤로", callback_data="td:list"),
            ]
        ]

        return {
            "text": f"{slot_name} 할일\n\n<b>{task.text}</b>",
            "reply_markup": InlineKeyboardMarkup(keyboard),
            "edit": True,
        }

    def _handle_done(self, chat_id: int, slot_code: str, index: int) -> dict:
        """완료 처리."""
        slot = self.SLOT_MAP.get(slot_code, TimeSlot.MORNING)

        if self.manager.mark_done_by_index(chat_id, slot, index):
            # 리스트로 돌아가기
            result = self._handle_list(chat_id)
            result["text"] = "✅ 완료 처리됨!\n\n" + result["text"]
            return result
        else:
            return {"text": "❌ 처리 실패", "edit": True}

    def _handle_delete(self, chat_id: int, slot_code: str, index: int) -> dict:
        """삭제 처리."""
        slot = self.SLOT_MAP.get(slot_code, TimeSlot.MORNING)

        if self.manager.delete_by_index(chat_id, slot, index):
            result = self._handle_list(chat_id)
            result["text"] = "🗑️ 삭제됨!\n\n" + result["text"]
            return result
        else:
            return {"text": "❌ 삭제 실패", "edit": True}

    def _handle_move(self, chat_id: int, slot_code: str, index: int, target_code: str) -> dict:
        """다른 시간대로 이동."""
        src_slot = self.SLOT_MAP.get(slot_code, TimeSlot.MORNING)
        dst_slot = self.SLOT_MAP.get(target_code, TimeSlot.AFTERNOON)

        daily = self.manager.get_today(chat_id)
        tasks = daily.get_tasks(src_slot)

        if index >= len(tasks):
            return {"text": "❌ 항목을 찾을 수 없어요.", "edit": True}

        task_text = tasks[index].text

        # 삭제 후 추가
        if self.manager.delete_by_index(chat_id, src_slot, index):
            daily = self.manager.get_today(chat_id)
            daily.add_task(dst_slot, task_text)
            self.manager.save_today(chat_id, daily)

            result = self._handle_list(chat_id)
            result["text"] = f"➡️ {self.SLOT_NAMES[dst_slot]}으로 이동!\n\n" + result["text"]
            return result
        else:
            return {"text": "❌ 이동 실패", "edit": True}

    def _handle_back(self, chat_id: int) -> dict:
        """메인 메뉴로."""
        self._clear_selections(chat_id)  # 선택 초기화
        keyboard = [
            [
                InlineKeyboardButton("📄 리스트", callback_data="td:list"),
                InlineKeyboardButton("➕ 추가", callback_data="td:add"),
            ]
        ]

        return {
            "text": "📋 <b>할일 관리</b>",
            "reply_markup": InlineKeyboardMarkup(keyboard),
            "edit": True,
        }

    # ==================== 멀티 선택 핸들러 ====================

    def _handle_multi_select(self, chat_id: int) -> dict:
        """멀티 선택 모드 진입."""
        self._clear_selections(chat_id)
        return self._render_multi_view(chat_id)

    def _render_multi_view(self, chat_id: int) -> dict:
        """멀티 선택 화면 렌더링."""
        pending = self.manager.get_pending_tasks_flat(chat_id)
        selections = self._get_selections(chat_id)

        if not pending:
            return {
                "text": "✅ 미완료 할일이 없어요!",
                "reply_markup": InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ 뒤로", callback_data="td:list")
                ]]),
                "edit": True,
            }

        lines = ["📋 <b>멀티 선택</b>\n", "항목을 터치해서 선택/해제하세요.\n"]

        slot_names = {"m": "🌅 오전", "a": "☀️ 오후", "e": "🌙 저녁"}
        current_slot = None
        buttons = []

        for item in pending:
            slot = item["slot"]
            if slot != current_slot:
                current_slot = slot
                lines.append(f"\n<b>{slot_names[slot]}</b>")

            key = (slot, item["index"])
            selected = key in selections
            mark = "☑️" if selected else "⬜"
            lines.append(f"{mark} {item['text']}")

            # 버튼
            btn_text = f"{'☑️' if selected else '⬜'} {item['text'][:18]}{'...' if len(item['text']) > 18 else ''}"
            buttons.append([
                InlineKeyboardButton(
                    btn_text,
                    callback_data=f"td:multi_toggle:{slot}:{item['index']}"
                )
            ])

        # 선택 개수
        count = len(selections)
        lines.append(f"\n📌 {count}개 선택됨")

        # 액션 버튼
        action_row = []
        if count > 0:
            action_row = [
                InlineKeyboardButton(f"✅ 완료({count})", callback_data="td:multi_done"),
                InlineKeyboardButton(f"🗑️ 삭제({count})", callback_data="td:multi_del"),
                InlineKeyboardButton(f"📅 내일({count})", callback_data="td:multi_carry"),
            ]
            buttons.append(action_row)

        buttons.append([
            InlineKeyboardButton("🔄 선택해제", callback_data="td:multi_clear"),
            InlineKeyboardButton("⬅️ 뒤로", callback_data="td:list"),
        ])

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_multi_toggle(self, chat_id: int, slot_code: str, index: int) -> dict:
        """항목 선택 토글."""
        self._toggle_selection(chat_id, slot_code, index)
        return self._render_multi_view(chat_id)

    def _handle_multi_done(self, chat_id: int) -> dict:
        """선택 항목 완료 처리."""
        selections = self._get_selections(chat_id)
        if not selections:
            return self._render_multi_view(chat_id)

        count = 0
        for slot_code, index in sorted(selections, key=lambda x: (x[0], -x[1])):
            slot = self.SLOT_MAP.get(slot_code)
            if slot and self.manager.mark_done_by_index(chat_id, slot, index):
                count += 1

        self._clear_selections(chat_id)

        result = self._handle_list(chat_id)
        result["text"] = f"✅ {count}개 완료 처리!\n\n" + result["text"]
        return result

    def _handle_multi_delete(self, chat_id: int) -> dict:
        """선택 항목 삭제."""
        selections = self._get_selections(chat_id)
        if not selections:
            return self._render_multi_view(chat_id)

        count = 0
        for slot_code, index in sorted(selections, key=lambda x: (x[0], -x[1])):
            slot = self.SLOT_MAP.get(slot_code)
            if slot and self.manager.delete_by_index(chat_id, slot, index):
                count += 1

        self._clear_selections(chat_id)

        result = self._handle_list(chat_id)
        result["text"] = f"🗑️ {count}개 삭제됨!\n\n" + result["text"]
        return result

    def _handle_multi_carry(self, chat_id: int) -> dict:
        """선택 항목 내일로 넘기기."""
        selections = self._get_selections(chat_id)
        if not selections:
            return self._render_multi_view(chat_id)

        items = list(selections)
        count = self.manager.carry_to_tomorrow(chat_id, items)

        self._clear_selections(chat_id)

        result = self._handle_list(chat_id)
        result["text"] = f"📅 {count}개 내일로 이동!\n\n" + result["text"]
        return result

    def _handle_multi_clear(self, chat_id: int) -> dict:
        """선택 초기화."""
        self._clear_selections(chat_id)
        return self._render_multi_view(chat_id)

    # ==================== 하루 마무리 핸들러 ====================

    def _handle_carry_all(self, chat_id: int) -> dict:
        """모든 미완료 항목 내일로 넘기기."""
        count = self.manager.carry_all_pending(chat_id)

        if count == 0:
            return {
                "text": "✅ 넘길 항목이 없어요!",
                "reply_markup": InlineKeyboardMarkup([[
                    InlineKeyboardButton("📄 리스트", callback_data="td:list")
                ]]),
                "edit": True,
            }

        return {
            "text": f"📅 <b>{count}개 항목을 내일로 넘겼어요!</b>\n\n오늘 수고하셨어요 🌙",
            "reply_markup": InlineKeyboardMarkup([[
                InlineKeyboardButton("📄 리스트", callback_data="td:list")
            ]]),
            "edit": True,
        }

    def _handle_wrap_done(self, chat_id: int) -> dict:
        """하루 마무리 완료."""
        daily = self.manager.get_today(chat_id)
        done = daily.get_done_count()
        pending = daily.get_pending_count()
        total = done + pending

        lines = ["🌙 <b>오늘 하루 마무리!</b>\n"]

        if total > 0:
            lines.append(f"📊 완료율: {done}/{total} ({int(done/total*100)}%)")

        if pending > 0:
            lines.append(f"\n⚠️ {pending}개 미완료 항목은 내일 다시 도전해요!")
        else:
            lines.append("\n🎉 완벽한 하루였어요!")

        lines.append("\n좋은 밤 되세요 ✨")

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup([[
                InlineKeyboardButton("📄 리스트", callback_data="td:list")
            ]]),
            "edit": True,
        }

    # ==================== 날짜 이동 핸들러 ====================

    def _handle_date_view(self, chat_id: int, date_str: str) -> dict:
        """특정 날짜 할일 조회."""
        try:
            target = date.fromisoformat(date_str)
        except (ValueError, TypeError):
            target = date.today()

        daily = self.manager.get_by_date(chat_id, target)
        all_tasks = daily.get_all_tasks()

        # 오늘인지 확인
        is_today = (target == date.today())
        date_label = "오늘" if is_today else target.strftime("%m/%d")

        lines = [f"📋 <b>{daily.date} ({date_label}) 할일</b>\n"]

        total = 0
        done_count = 0
        for slot in [TimeSlot.MORNING, TimeSlot.AFTERNOON, TimeSlot.EVENING]:
            tasks = all_tasks.get(slot.value, [])
            slot_name = self.SLOT_NAMES[slot]

            if tasks:
                lines.append(f"\n<b>{slot_name}</b>")
                for task in tasks:
                    total += 1
                    status = "✅" if task.done else "⬜"
                    if task.done:
                        done_count += 1
                    lines.append(f"{status} {task.text}")

        if total == 0:
            lines.append("\n등록된 할일이 없어요.")
        else:
            lines.append(f"\n📊 {done_count}/{total} 완료")

        # 날짜 이동 버튼
        prev_date = (target - timedelta(days=1)).isoformat()
        next_date = (target + timedelta(days=1)).isoformat()
        today_str = date.today().isoformat()

        buttons = [
            [
                InlineKeyboardButton("◀️ 이전", callback_data=f"td:date:{prev_date}"),
                InlineKeyboardButton("📅 오늘", callback_data="td:list"),
                InlineKeyboardButton("다음 ▶️", callback_data=f"td:date:{next_date}"),
            ],
            [
                InlineKeyboardButton("📅 주간", callback_data=f"td:week:{target.isoformat()}"),
            ]
        ]

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_week_view(self, chat_id: int, date_str: str) -> dict:
        """주간 뷰 (기준일 ±3일 = 7일)."""
        try:
            center = date.fromisoformat(date_str)
        except (ValueError, TypeError):
            center = date.today()

        start = center - timedelta(days=3)
        end = center + timedelta(days=3)

        dailies = self.manager.get_date_range(chat_id, start, end)
        today = date.today()

        lines = [f"📅 <b>주간 할일</b> ({start.strftime('%m/%d')} ~ {end.strftime('%m/%d')})\n"]

        for daily in dailies:
            d = date.fromisoformat(daily.date)
            is_today = (d == today)
            day_mark = "👉 " if is_today else ""
            weekday = ["월", "화", "수", "목", "금", "토", "일"][d.weekday()]

            all_tasks = daily.get_all_tasks()
            total = sum(len(tasks) for tasks in all_tasks.values())
            done = sum(1 for tasks in all_tasks.values() for t in tasks if t.done)

            if total == 0:
                status = "—"
            elif done == total:
                status = f"✅ {done}/{total}"
            else:
                status = f"⬜ {done}/{total}"

            date_label = f"{d.strftime('%m/%d')}({weekday})"
            lines.append(f"{day_mark}<b>{date_label}</b>: {status}")

        # 날짜 선택 버튼 (각 날짜로 이동)
        buttons = []
        row = []
        for daily in dailies:
            d = date.fromisoformat(daily.date)
            weekday = ["월", "화", "수", "목", "금", "토", "일"][d.weekday()]
            is_today = (d == today)
            label = f"{'📍' if is_today else ''}{d.day}({weekday})"
            row.append(InlineKeyboardButton(label, callback_data=f"td:date:{daily.date}"))
            if len(row) == 4:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        # 주간 이동 버튼
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

    def _handle_today_full(self, chat_id: int) -> dict:
        """오늘 할일 전체 보기 (버튼 없이 깔끔하게)."""
        daily = self.manager.get_today(chat_id)
        all_tasks = daily.get_all_tasks()

        lines = [f"📋 <b>{daily.date} (오늘) 전체 할일</b>\n"]

        total = 0
        done_count = 0

        for slot in [TimeSlot.MORNING, TimeSlot.AFTERNOON, TimeSlot.EVENING]:
            tasks = all_tasks.get(slot.value, [])
            slot_name = self.SLOT_NAMES[slot]

            if tasks:
                lines.append(f"\n<b>{slot_name}</b>")
                for task in tasks:
                    total += 1
                    status = "✅" if task.done else "⬜"
                    if task.done:
                        done_count += 1
                    lines.append(f"{status} {task.text}")
            else:
                lines.append(f"\n<b>{slot_name}</b>")
                lines.append("• (없음)")

        if total == 0:
            lines.append("\n등록된 할일이 없어요.")
        else:
            lines.append(f"\n📊 <b>진행 상황</b>: {done_count}/{total} 완료 ({int(done_count/total*100)}%)")

        # 간단한 버튼만
        buttons = [
            [
                InlineKeyboardButton("⬅️ 돌아가기", callback_data="td:list"),
                InlineKeyboardButton("➕ 추가", callback_data="td:add"),
            ]
        ]

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    # ==================== ForceReply 응답 처리 ====================

    def handle_force_reply(self, message: str, chat_id: int, slot_code: str) -> dict:
        """ForceReply 응답 처리 - 할일 추가."""
        slot = self.SLOT_MAP.get(slot_code, TimeSlot.MORNING)
        slot_name = self.SLOT_NAMES[slot]

        # 줄바꿈으로 분리
        tasks = [t.strip() for t in message.split("\n") if t.strip()]

        if not tasks:
            return {
                "text": "❌ 할일이 입력되지 않았어요.",
                "reply_markup": None,
            }

        daily = self.manager.get_today(chat_id)
        for task_text in tasks:
            daily.add_task(slot, task_text)
        self.manager.save_today(chat_id, daily)

        # 결과 메시지
        lines = [f"✅ {slot_name}에 {len(tasks)}개 추가됨!\n"]
        for task in tasks:
            lines.append(f"• {task}")

        # 리스트 버튼
        keyboard = [
            [
                InlineKeyboardButton("📄 리스트 보기", callback_data="td:list"),
                InlineKeyboardButton("➕ 더 추가", callback_data="td:add"),
            ]
        ]

        return {
            "text": "\n".join(lines),
            "reply_markup": InlineKeyboardMarkup(keyboard),
        }
