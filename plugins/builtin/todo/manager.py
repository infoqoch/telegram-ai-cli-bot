"""Todo 데이터 관리자."""

import json
from datetime import datetime, date
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict
from enum import Enum

from src.logging_config import logger


class TimeSlot(str, Enum):
    """시간대 구분."""
    MORNING = "morning"      # 오전 (08:00 ~ 12:00)
    AFTERNOON = "afternoon"  # 오후 (12:00 ~ 18:00)
    EVENING = "evening"      # 저녁 (18:00 ~ 24:00)


@dataclass
class TodoItem:
    """할일 항목."""
    text: str
    done: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None

    def mark_done(self) -> None:
        """완료 처리."""
        self.done = True
        self.completed_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TodoItem":
        return cls(**data)


@dataclass
class DailyTodo:
    """일일 할일 데이터."""
    date: str  # YYYY-MM-DD
    tasks: dict[str, list[dict]] = field(default_factory=lambda: {
        TimeSlot.MORNING.value: [],
        TimeSlot.AFTERNOON.value: [],
        TimeSlot.EVENING.value: [],
    })
    pending_input: bool = False  # 할일 입력 대기 중
    pending_input_timestamp: Optional[str] = None  # 입력 대기 시작 시간
    last_reminder: Optional[str] = None  # 마지막 리마인더 시간대

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "tasks": self.tasks,
            "pending_input": self.pending_input,
            "pending_input_timestamp": self.pending_input_timestamp,
            "last_reminder": self.last_reminder,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DailyTodo":
        return cls(
            date=data.get("date", ""),
            tasks=data.get("tasks", {
                TimeSlot.MORNING.value: [],
                TimeSlot.AFTERNOON.value: [],
                TimeSlot.EVENING.value: [],
            }),
            pending_input=data.get("pending_input", False),
            pending_input_timestamp=data.get("pending_input_timestamp"),
            last_reminder=data.get("last_reminder"),
        )

    def add_task(self, slot: TimeSlot, text: str) -> TodoItem:
        """할일 추가."""
        item = TodoItem(text=text)
        if slot.value not in self.tasks:
            self.tasks[slot.value] = []
        self.tasks[slot.value].append(item.to_dict())
        return item

    def get_tasks(self, slot: TimeSlot) -> list[TodoItem]:
        """시간대별 할일 조회."""
        items = self.tasks.get(slot.value, [])
        return [TodoItem.from_dict(item) for item in items]

    def get_all_tasks(self) -> dict[str, list[TodoItem]]:
        """전체 할일 조회."""
        return {
            slot: [TodoItem.from_dict(item) for item in items]
            for slot, items in self.tasks.items()
        }

    def mark_task_done(self, slot: TimeSlot, index: int) -> bool:
        """할일 완료 처리."""
        items = self.tasks.get(slot.value, [])
        if 0 <= index < len(items):
            items[index]["done"] = True
            items[index]["completed_at"] = datetime.now().isoformat()
            return True
        return False

    def get_pending_count(self, slot: Optional[TimeSlot] = None) -> int:
        """미완료 할일 수."""
        if slot:
            items = self.tasks.get(slot.value, [])
            return sum(1 for item in items if not item.get("done", False))
        return sum(
            1 for items in self.tasks.values()
            for item in items if not item.get("done", False)
        )

    def get_done_count(self, slot: Optional[TimeSlot] = None) -> int:
        """완료된 할일 수."""
        if slot:
            items = self.tasks.get(slot.value, [])
            return sum(1 for item in items if item.get("done", False))
        return sum(
            1 for items in self.tasks.values()
            for item in items if item.get("done", False)
        )


class TodoManager:
    """할일 관리자."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _get_file_path(self, chat_id: int) -> Path:
        """사용자별 데이터 파일 경로."""
        return self.data_dir / f"{chat_id}.json"

    def _load_data(self, chat_id: int) -> dict:
        """데이터 로드."""
        file_path = self._get_file_path(chat_id)
        if not file_path.exists():
            return {}
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_data(self, chat_id: int, data: dict) -> None:
        """데이터 저장."""
        file_path = self._get_file_path(chat_id)
        file_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def get_today(self, chat_id: int) -> DailyTodo:
        """오늘 할일 조회 (없으면 생성, 내일로 넘긴 항목 자동 로드)."""
        today_str = date.today().isoformat()
        data = self._load_data(chat_id)

        if data.get("date") != today_str:
            # 날짜가 다르면 새로 생성
            daily = DailyTodo(date=today_str)
            self._save_data(chat_id, daily.to_dict())

            # 내일로 넘긴 항목 자동 로드
            self._load_carried_tasks(chat_id)

            # 다시 로드 (carried tasks 반영)
            data = self._load_data(chat_id)
            return DailyTodo.from_dict(data)

        return DailyTodo.from_dict(data)

    def _load_carried_tasks(self, chat_id: int) -> None:
        """내일로 넘긴 항목을 오늘로 로드 (내부용)."""
        tomorrow_file = self.data_dir / f"{chat_id}_tomorrow.json"
        if not tomorrow_file.exists():
            return

        try:
            tomorrow_data = json.loads(tomorrow_file.read_text(encoding="utf-8"))
            today_str = date.today().isoformat()

            # 날짜가 맞으면 오늘로 병합
            if tomorrow_data.get("date") == today_str:
                daily = DailyTodo.from_dict(self._load_data(chat_id))

                for slot_value, tasks in tomorrow_data.get("tasks", {}).items():
                    if slot_value not in daily.tasks:
                        daily.tasks[slot_value] = []
                    daily.tasks[slot_value].extend(tasks)

                self._save_data(chat_id, daily.to_dict())
                tomorrow_file.unlink()  # 파일 삭제
                logger.info(f"내일 할일 로드됨: chat_id={chat_id}, tasks={len(tomorrow_data.get('tasks', {}))}")

        except Exception as e:
            logger.error(f"내일 할일 로드 실패: {e}")

    def save_today(self, chat_id: int, daily: DailyTodo) -> None:
        """오늘 할일 저장."""
        self._save_data(chat_id, daily.to_dict())

    def set_pending_input(self, chat_id: int, pending: bool) -> None:
        """입력 대기 상태 설정."""
        daily = self.get_today(chat_id)
        daily.pending_input = pending
        if pending:
            daily.pending_input_timestamp = datetime.now().isoformat()
        else:
            daily.pending_input_timestamp = None
        self.save_today(chat_id, daily)

    def is_pending_input(self, chat_id: int) -> bool:
        """입력 대기 상태 확인 (2시간 타임아웃)."""
        daily = self.get_today(chat_id)

        if not daily.pending_input:
            return False

        # 타임스탬프가 없으면 레거시 데이터 - 무효화
        if not daily.pending_input_timestamp:
            daily.pending_input = False
            self.save_today(chat_id, daily)
            return False

        # 2시간 경과 체크
        try:
            pending_time = datetime.fromisoformat(daily.pending_input_timestamp)
            elapsed = datetime.now() - pending_time

            # 2시간(7200초) 경과 시 자동 만료
            if elapsed.total_seconds() > 7200:
                daily.pending_input = False
                daily.pending_input_timestamp = None
                self.save_today(chat_id, daily)
                return False
        except Exception:
            # 파싱 실패 시 안전하게 만료
            daily.pending_input = False
            daily.pending_input_timestamp = None
            self.save_today(chat_id, daily)
            return False

        return True

    def add_tasks_from_text(self, chat_id: int, tasks_by_slot: dict[TimeSlot, list[str]]) -> DailyTodo:
        """파싱된 할일 추가."""
        daily = self.get_today(chat_id)
        for slot, texts in tasks_by_slot.items():
            for text in texts:
                daily.add_task(slot, text)
        daily.pending_input = False
        self.save_today(chat_id, daily)
        return daily

    def mark_done_by_text(self, chat_id: int, text: str) -> bool:
        """텍스트로 할일 완료 처리 (개선된 매칭)."""
        daily = self.get_today(chat_id)
        text_lower = text.lower().strip()

        for slot_name, items in daily.tasks.items():
            for i, item in enumerate(items):
                if item.get("done"):
                    continue

                item_text_lower = item["text"].lower()

                # 매칭 조건 (우선순위):
                # 1. 정확히 일치
                if text_lower == item_text_lower:
                    items[i]["done"] = True
                    items[i]["completed_at"] = datetime.now().isoformat()
                    self.save_today(chat_id, daily)
                    return True

                # 2. 단어 시작 부분 일치 (공백으로 구분된 단어의 시작)
                if item_text_lower.startswith(text_lower + " ") or \
                   " " + text_lower in item_text_lower:
                    items[i]["done"] = True
                    items[i]["completed_at"] = datetime.now().isoformat()
                    self.save_today(chat_id, daily)
                    return True

                # 3. 50% 이상 매칭 (짧은 문자열은 제외)
                if len(text_lower) >= 2:
                    # 간단한 유사도: 검색어가 할일 텍스트의 50% 이상 포함
                    match_count = sum(1 for char in text_lower if char in item_text_lower)
                    if match_count / len(text_lower) >= 0.7:
                        items[i]["done"] = True
                        items[i]["completed_at"] = datetime.now().isoformat()
                        self.save_today(chat_id, daily)
                        return True

        return False

    def mark_done_by_index(self, chat_id: int, slot: TimeSlot, index: int) -> bool:
        """인덱스로 할일 완료 처리."""
        daily = self.get_today(chat_id)
        if daily.mark_task_done(slot, index):
            self.save_today(chat_id, daily)
            return True
        return False

    def mark_done_by_global_index(self, chat_id: int, global_index: int) -> Optional[tuple[str, str]]:
        """전역 인덱스로 할일 완료 처리.

        Args:
            chat_id: 채팅 ID
            global_index: 전체 목록에서의 번호 (1-based)

        Returns:
            성공 시 (시간대명, 할일텍스트), 실패 시 None
        """
        daily = self.get_today(chat_id)

        # 전체 할일을 시간대 순서대로 순회
        current_global = 0
        slot_names = {
            TimeSlot.MORNING.value: "🌅 오전",
            TimeSlot.AFTERNOON.value: "☀️ 오후",
            TimeSlot.EVENING.value: "🌙 저녁",
        }

        for slot_value in [TimeSlot.MORNING.value, TimeSlot.AFTERNOON.value, TimeSlot.EVENING.value]:
            items = daily.tasks.get(slot_value, [])
            for i, item in enumerate(items):
                current_global += 1
                if current_global == global_index:
                    # 찾았음!
                    if not item.get("done"):
                        items[i]["done"] = True
                        items[i]["completed_at"] = datetime.now().isoformat()
                        self.save_today(chat_id, daily)
                        return (slot_names.get(slot_value, slot_value), item["text"])
                    else:
                        # 이미 완료된 항목
                        return (slot_names.get(slot_value, slot_value), item["text"])

        return None

    def get_slot_summary(self, chat_id: int, slot: TimeSlot) -> str:
        """시간대별 요약."""
        daily = self.get_today(chat_id)
        tasks = daily.get_tasks(slot)

        if not tasks:
            return "등록된 할일이 없어요."

        lines = []
        for i, task in enumerate(tasks, 1):
            status = "✅" if task.done else "⬜"
            lines.append(f"{status} {i}. {task.text}")

        pending = daily.get_pending_count(slot)
        done = daily.get_done_count(slot)

        return "\n".join(lines) + f"\n\n📊 완료: {done}/{done + pending}"

    def get_daily_summary(self, chat_id: int) -> str:
        """일일 요약."""
        daily = self.get_today(chat_id)
        all_tasks = daily.get_all_tasks()

        slot_names = {
            TimeSlot.MORNING.value: "🌅 오전",
            TimeSlot.AFTERNOON.value: "☀️ 오후",
            TimeSlot.EVENING.value: "🌙 저녁",
        }

        lines = [f"📋 <b>{daily.date} 할일</b>\n"]

        for slot_value, tasks in all_tasks.items():
            slot_name = slot_names.get(slot_value, slot_value)
            if tasks:
                lines.append(f"\n<b>{slot_name}</b>")
                for i, task in enumerate(tasks, 1):
                    status = "✅" if task.done else "⬜"
                    lines.append(f"{status} {i}. {task.text}")

        pending = daily.get_pending_count()
        done = daily.get_done_count()
        total = pending + done

        if total == 0:
            lines.append("\n등록된 할일이 없어요.")
        else:
            lines.append(f"\n📊 전체: {done}/{total} 완료")

        return "\n".join(lines)

    def delete_by_index(self, chat_id: int, slot: TimeSlot, index: int) -> bool:
        """인덱스로 할일 삭제."""
        daily = self.get_today(chat_id)
        items = daily.tasks.get(slot.value, [])
        if 0 <= index < len(items):
            items.pop(index)
            self.save_today(chat_id, daily)
            return True
        return False

    def delete_by_global_index(self, chat_id: int, global_index: int) -> Optional[tuple[str, str]]:
        """전역 인덱스로 할일 삭제."""
        daily = self.get_today(chat_id)
        current_global = 0
        slot_names = {
            TimeSlot.MORNING.value: "🌅 오전",
            TimeSlot.AFTERNOON.value: "☀️ 오후",
            TimeSlot.EVENING.value: "🌙 저녁",
        }

        for slot_value in [TimeSlot.MORNING.value, TimeSlot.AFTERNOON.value, TimeSlot.EVENING.value]:
            items = daily.tasks.get(slot_value, [])
            for i, item in enumerate(items):
                current_global += 1
                if current_global == global_index:
                    task_text = item["text"]
                    items.pop(i)
                    self.save_today(chat_id, daily)
                    return (slot_names.get(slot_value, slot_value), task_text)

        return None

    def delete_by_text(self, chat_id: int, text: str) -> bool:
        """텍스트로 할일 삭제 (mark_done_by_text와 동일한 매칭 로직)."""
        daily = self.get_today(chat_id)
        text_lower = text.lower().strip()

        for slot_name, items in daily.tasks.items():
            for i, item in enumerate(items):
                item_text_lower = item["text"].lower()

                # 매칭 조건 (mark_done_by_text와 동일)
                if text_lower == item_text_lower:
                    items.pop(i)
                    self.save_today(chat_id, daily)
                    return True

                if item_text_lower.startswith(text_lower + " ") or \
                   " " + text_lower in item_text_lower:
                    items.pop(i)
                    self.save_today(chat_id, daily)
                    return True

                if len(text_lower) >= 2:
                    match_count = sum(1 for char in text_lower if char in item_text_lower)
                    if match_count / len(text_lower) >= 0.7:
                        items.pop(i)
                        self.save_today(chat_id, daily)
                        return True

        return False

    def get_registered_chat_ids(self) -> list[int]:
        """등록된 모든 chat_id 목록."""
        chat_ids = []
        for file_path in self.data_dir.glob("*.json"):
            try:
                chat_id = int(file_path.stem)
                chat_ids.append(chat_id)
            except ValueError:
                continue
        return chat_ids

    def get_pending_tasks_flat(self, chat_id: int) -> list[dict]:
        """미완료 할일을 flat list로 반환 (멀티 선택용).

        Returns:
            [{"slot": "m", "index": 0, "text": "할일1"}, ...]
        """
        daily = self.get_today(chat_id)
        result = []
        slot_codes = {
            TimeSlot.MORNING.value: "m",
            TimeSlot.AFTERNOON.value: "a",
            TimeSlot.EVENING.value: "e",
        }

        for slot in [TimeSlot.MORNING, TimeSlot.AFTERNOON, TimeSlot.EVENING]:
            tasks = daily.get_tasks(slot)
            for i, task in enumerate(tasks):
                if not task.done:
                    result.append({
                        "slot": slot_codes[slot.value],
                        "index": i,
                        "text": task.text,
                    })
        return result

    def carry_to_tomorrow(self, chat_id: int, items: list[tuple[str, int]]) -> int:
        """미완료 항목을 내일로 넘기기.

        Args:
            chat_id: 채팅 ID
            items: [(slot_code, index), ...] 넘길 항목들

        Returns:
            넘긴 항목 수
        """
        from datetime import timedelta

        daily = self.get_today(chat_id)
        tomorrow_str = (date.today() + timedelta(days=1)).isoformat()

        # 내일 데이터 로드 또는 생성
        tomorrow_file = self.data_dir / f"{chat_id}_tomorrow.json"
        if tomorrow_file.exists():
            try:
                tomorrow_data = json.loads(tomorrow_file.read_text(encoding="utf-8"))
                tomorrow_daily = DailyTodo.from_dict(tomorrow_data)
            except Exception:
                tomorrow_daily = DailyTodo(date=tomorrow_str)
        else:
            tomorrow_daily = DailyTodo(date=tomorrow_str)

        tomorrow_daily.date = tomorrow_str

        slot_map = {
            "m": TimeSlot.MORNING,
            "a": TimeSlot.AFTERNOON,
            "e": TimeSlot.EVENING,
        }

        carried = 0
        # 역순으로 처리 (삭제 시 인덱스 밀림 방지)
        for slot_code, index in sorted(items, key=lambda x: (x[0], -x[1])):
            slot = slot_map.get(slot_code)
            if not slot:
                continue

            tasks = daily.tasks.get(slot.value, [])
            if 0 <= index < len(tasks):
                task = tasks[index]
                if not task.get("done"):
                    # 내일에 추가
                    tomorrow_daily.add_task(slot, task["text"])
                    # 오늘에서 삭제
                    tasks.pop(index)
                    carried += 1

        # 저장
        self.save_today(chat_id, daily)
        tomorrow_file.write_text(
            json.dumps(tomorrow_daily.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        return carried

    def carry_all_pending(self, chat_id: int) -> int:
        """모든 미완료 항목을 내일로 넘기기."""
        pending = self.get_pending_tasks_flat(chat_id)
        items = [(p["slot"], p["index"]) for p in pending]
        return self.carry_to_tomorrow(chat_id, items)

