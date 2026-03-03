"""Todo 플러그인 - 자연어 기반 할일 관리."""

import re
from pathlib import Path
from typing import Optional

from src.plugins.loader import Plugin, PluginResult
from src.logging_config import logger

from .manager import TodoManager, TimeSlot, DailyTodo


class TodoPlugin(Plugin):
    """할일 관리 플러그인."""

    name = "todo"
    description = "스케줄 기반 할일 관리 (오전/오후/저녁)"
    usage = (
        "📋 <b>할일 플러그인 사용법</b>\n\n"
        "<b>🕐 자동 알림</b>\n"
        "• 08:00 - 오늘 할일 질문\n"
        "• 10:00 - 오전 할일 체크\n"
        "• 15:00 - 오후 할일 체크\n"
        "• 19:00 - 저녁 할일 체크\n\n"
        "<b>📝 할일 등록</b>\n"
        "• 아침에 봇이 물어보면 자유롭게 답변\n"
        "• <code>할일 추가: 회의하기</code>\n"
        "• <code>오전에 회의 추가</code>\n\n"
        "<b>✅ 완료 처리</b>\n"
        "• <code>회의 끝났어</code>\n"
        "• <code>1번 완료</code>\n"
        "• <code>오전 1번 done</code>\n\n"
        "<b>📊 조회</b>\n"
        "• <code>할일 보여줘</code>\n"
        "• <code>오늘 할일</code>\n"
        "• <code>오전 할일</code>"
    )

    # 제외 패턴 - AI에게 넘겨야 하는 경우
    EXCLUDE_PATTERNS = [
        r"(란|이란|가|이)\s*(뭐|무엇|뭔)",  # "할일이란 뭐야"
        r"영어로|번역|translate",
        r"어떻게|왜|언제|어디",
        r"알려줘|설명|뜻",
    ]

    # 트리거 패턴
    TRIGGER_PATTERNS = [
        # 조회
        r"(오늘|내일)?\s*할\s*일\s*(보여|목록|리스트|확인|뭐)",
        r"(오전|오후|저녁)\s*할\s*일",
        r"todo\s*(list|보여|확인)?",
        # 추가
        r"할\s*일\s*(추가|등록)",
        r"(오전|오후|저녁)에?\s*.+\s*(추가|등록|해야)",
        # 완료
        r"(\d+)번?\s*(완료|끝|done|했어|함)",
        r"(오전|오후|저녁)\s*(\d+)번?\s*(완료|끝|done)",
        r".+\s*(끝났어|완료|했어|done)",
        # 입력 대기 상태에서의 자유 입력 (별도 처리)
    ]

    # 시간대 키워드 매핑
    SLOT_KEYWORDS = {
        TimeSlot.MORNING: ["오전", "아침", "morning", "am"],
        TimeSlot.AFTERNOON: ["오후", "점심", "afternoon", "pm", "낮"],
        TimeSlot.EVENING: ["저녁", "밤", "evening", "night"],
    }

    # 시간대 키워드 제거용 패턴 (문두에서만 제거, "점심"은 할일로도 쓰이므로 "점심에"만 제거)
    SLOT_REMOVE_PATTERNS = [
        r"^오전에?\s*",
        r"^아침에?\s*",
        r"^오후에\s+",  # "오후에 " (뒤에 공백 필수)
        r"^점심에\s+",  # "점심에 " (뒤에 공백 필수, "점심" 자체는 할일일 수 있음)
        r"^낮에?\s*",
        r"^저녁에?[는은]?\s*",
        r"^밤에?\s*",
    ]

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

    async def can_handle(self, message: str, chat_id: int) -> bool:
        """할일 관련 메시지인지 확인."""
        msg = message.strip()

        # 제외 패턴 체크
        for pattern in self.EXCLUDE_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return False

        # 입력 대기 상태면 처리
        if self.manager.is_pending_input(chat_id):
            return True

        # 트리거 패턴 체크
        for pattern in self.TRIGGER_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return True

        return False

    async def handle(self, message: str, chat_id: int) -> PluginResult:
        """메시지 처리."""
        msg = message.strip()
        logger.info(f"Todo 플러그인 처리: '{msg[:50]}...' (chat_id={chat_id})")

        # 1. 입력 대기 상태 처리 (아침 질문에 대한 답변)
        if self.manager.is_pending_input(chat_id):
            return await self._handle_pending_input(msg, chat_id)

        # 2. 추가 패턴 (조회보다 먼저 - "할일 추가"가 조회에 매칭되지 않도록)
        add_result = await self._handle_add(msg, chat_id)
        if add_result:
            return add_result

        # 3. 완료 패턴
        done_result = await self._handle_done(msg, chat_id)
        if done_result:
            return done_result

        # 4. 조회 패턴
        if re.search(r"(오늘|내일)?\s*할\s*일\s*(보여|목록|리스트|확인|뭐)?", msg):
            # 특정 시간대 조회
            slot = self._detect_slot(msg)
            if slot:
                summary = self.manager.get_slot_summary(chat_id, slot)
                slot_name = self._get_slot_name(slot)
                return PluginResult(
                    handled=True,
                    response=f"<b>{slot_name} 할일</b>\n\n{summary}"
                )
            # 전체 조회
            summary = self.manager.get_daily_summary(chat_id)
            return PluginResult(handled=True, response=summary)

        # 5. 기본 - 전체 조회
        summary = self.manager.get_daily_summary(chat_id)
        return PluginResult(handled=True, response=summary)

    async def _handle_pending_input(self, message: str, chat_id: int) -> PluginResult:
        """입력 대기 상태 처리 (자유 형식 할일 입력)."""
        logger.info(f"할일 입력 처리: {message[:100]}")

        # AI 파싱 대신 간단한 규칙 기반 파싱
        tasks_by_slot = self._parse_tasks_simple(message)

        if not any(tasks_by_slot.values()):
            # 파싱 실패 - 전체를 오전으로 분류
            tasks_by_slot[TimeSlot.MORNING] = [message.strip()]

        # 저장
        daily = self.manager.add_tasks_from_text(chat_id, tasks_by_slot)

        # 응답 생성
        lines = ["✅ 할일 등록 완료!\n"]

        slot_names = {
            TimeSlot.MORNING: "🌅 오전",
            TimeSlot.AFTERNOON: "☀️ 오후",
            TimeSlot.EVENING: "🌙 저녁",
        }

        for slot, tasks in tasks_by_slot.items():
            if tasks:
                lines.append(f"\n<b>{slot_names[slot]}</b>")
                for task in tasks:
                    lines.append(f"• {task}")

        lines.append("\n\n시간대별로 리마인더 보내드릴게요!")

        return PluginResult(handled=True, response="\n".join(lines))

    def _parse_tasks_simple(self, text: str) -> dict[TimeSlot, list[str]]:
        """간단한 규칙 기반 할일 파싱."""
        tasks = {
            TimeSlot.MORNING: [],
            TimeSlot.AFTERNOON: [],
            TimeSlot.EVENING: [],
        }

        # 전처리: "저녁엔", "오후엔" 등을 "저녁에", "오후에"로 정규화
        text = re.sub(r'(오전|아침|오후|점심|저녁|밤)엔\s*', r'\1에 ', text)

        # 쉼표, 줄바꿈, "그리고" 등으로 분리
        parts = re.split(r'[,\n]|그리고|하고', text)

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # 시간대 감지
            slot = self._detect_slot(part)

            # 시간대 키워드 제거
            clean_part = part
            for pattern in self.SLOT_REMOVE_PATTERNS:
                clean_part = re.sub(pattern, '', clean_part, flags=re.IGNORECASE)

            clean_part = clean_part.strip()
            if not clean_part:
                continue

            # "~해야해", "~하기" 등 접미사 정리
            clean_part = re.sub(r'(해야\s*(해|돼|함)?|하기|할\s*거야?)$', '', clean_part).strip()

            if clean_part:
                if slot:
                    tasks[slot].append(clean_part)
                else:
                    # 시간대 불명 - 기본값 또는 순서대로 분배
                    # 간단하게: 첫 번째는 오전, 두 번째는 오후, 세 번째는 저녁
                    total = sum(len(t) for t in tasks.values())
                    if total % 3 == 0:
                        tasks[TimeSlot.MORNING].append(clean_part)
                    elif total % 3 == 1:
                        tasks[TimeSlot.AFTERNOON].append(clean_part)
                    else:
                        tasks[TimeSlot.EVENING].append(clean_part)

        return tasks

    async def _handle_done(self, message: str, chat_id: int) -> Optional[PluginResult]:
        """완료 처리."""
        msg = message.lower()

        # 패턴: "N번 완료", "오전 N번 완료"
        match = re.search(r'(오전|오후|저녁)?\s*(\d+)번?\s*(완료|끝|done|했어|함)', msg)
        if match:
            slot_text = match.group(1)
            index = int(match.group(2)) - 1  # 0-based

            slot = self._text_to_slot(slot_text) if slot_text else None

            if slot:
                if self.manager.mark_done_by_index(chat_id, slot, index):
                    return PluginResult(
                        handled=True,
                        response=f"✅ {self._get_slot_name(slot)} {index + 1}번 완료!"
                    )
            else:
                # 시간대 없으면 전체에서 검색
                for s in [TimeSlot.MORNING, TimeSlot.AFTERNOON, TimeSlot.EVENING]:
                    if self.manager.mark_done_by_index(chat_id, s, index):
                        return PluginResult(
                            handled=True,
                            response=f"✅ {index + 1}번 완료!"
                        )

        # 패턴: "회의 끝났어", "운동 완료"
        match = re.search(r'(.+?)\s*(끝났어|완료|했어|done)', msg)
        if match:
            task_text = match.group(1).strip()
            if self.manager.mark_done_by_text(chat_id, task_text):
                return PluginResult(
                    handled=True,
                    response=f"✅ '{task_text}' 완료!"
                )

        return None

    async def _handle_add(self, message: str, chat_id: int) -> Optional[PluginResult]:
        """할일 추가."""
        msg = message.strip()

        # 패턴: "할일 추가: XXX", "할일 추가 XXX"
        match = re.search(r'할\s*일\s*(추가|등록)[:\s]*(.+)', msg)
        if match:
            task_text = match.group(2).strip()
            slot = self._detect_slot(task_text) or TimeSlot.MORNING

            # 시간대 키워드 제거
            for keywords in self.SLOT_KEYWORDS.values():
                for kw in keywords:
                    task_text = re.sub(rf'\b{kw}에?\s*', '', task_text, flags=re.IGNORECASE)

            task_text = task_text.strip()
            if task_text:
                daily = self.manager.get_today(chat_id)
                daily.add_task(slot, task_text)
                self.manager.save_today(chat_id, daily)

                return PluginResult(
                    handled=True,
                    response=f"✅ {self._get_slot_name(slot)}에 추가됨!\n• {task_text}"
                )

        # 패턴: "오전에 회의 추가"
        match = re.search(r'(오전|오후|저녁)에?\s*(.+?)\s*(추가|등록|해야)', msg)
        if match:
            slot = self._text_to_slot(match.group(1))
            task_text = match.group(2).strip()

            if slot and task_text:
                daily = self.manager.get_today(chat_id)
                daily.add_task(slot, task_text)
                self.manager.save_today(chat_id, daily)

                return PluginResult(
                    handled=True,
                    response=f"✅ {self._get_slot_name(slot)}에 추가됨!\n• {task_text}"
                )

        return None

    def _detect_slot(self, text: str) -> Optional[TimeSlot]:
        """텍스트에서 시간대 감지."""
        text_lower = text.lower()
        for slot, keywords in self.SLOT_KEYWORDS.items():
            for kw in keywords:
                if kw in text_lower:
                    return slot
        return None

    def _text_to_slot(self, text: Optional[str]) -> Optional[TimeSlot]:
        """텍스트를 TimeSlot으로 변환."""
        if not text:
            return None
        text = text.lower()
        if text in ["오전", "아침"]:
            return TimeSlot.MORNING
        elif text in ["오후", "점심", "낮"]:
            return TimeSlot.AFTERNOON
        elif text in ["저녁", "밤"]:
            return TimeSlot.EVENING
        return None

    def _get_slot_name(self, slot: TimeSlot) -> str:
        """시간대 이름."""
        names = {
            TimeSlot.MORNING: "🌅 오전",
            TimeSlot.AFTERNOON: "☀️ 오후",
            TimeSlot.EVENING: "🌙 저녁",
        }
        return names.get(slot, str(slot))
