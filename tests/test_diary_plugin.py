"""일기(Diary) 플러그인 테스트 - Repository 어댑터 및 플러그인 동작."""

import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.repository.adapters.plugin_storage import RepositoryDiaryStore
from src.repository.repository import Diary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    """인메모리 SQLite 커넥션 생성 + 다이어리 스키마 적용."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # DiaryPlugin.get_schema()와 동일한 DDL
    conn.executescript("""
CREATE TABLE IF NOT EXISTS diaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_diaries_chat_date
    ON diaries(chat_id, date);
CREATE INDEX IF NOT EXISTS idx_diaries_chat_id
    ON diaries(chat_id);
CREATE TRIGGER IF NOT EXISTS update_diaries_timestamp
AFTER UPDATE ON diaries
BEGIN
    UPDATE diaries SET updated_at = datetime('now') WHERE id = NEW.id;
END;
""")
    return conn


def _make_store() -> tuple[RepositoryDiaryStore, sqlite3.Connection]:
    """테스트용 RepositoryDiaryStore와 커넥션 반환."""
    conn = _make_conn()
    repo = MagicMock()
    repo._conn = conn
    store = RepositoryDiaryStore(repo)
    return store, conn


def _make_plugin():
    """DiaryPlugin + mock store 조합 반환."""
    from plugins.custom.diary.plugin import DiaryPlugin

    plugin = DiaryPlugin()
    mock_store = MagicMock()
    plugin._storage = mock_store
    return plugin, mock_store


# ---------------------------------------------------------------------------
# TestDiaryRepository
# ---------------------------------------------------------------------------

class TestDiaryRepository:
    """RepositoryDiaryStore - 실제 SQLite 인메모리 DB로 CRUD 검증."""

    def test_add_diary(self):
        """일기 추가 후 필드 검증."""
        store, _ = _make_store()
        today = date.today().isoformat()

        diary = store.add(chat_id=1, date=today, content="오늘의 일기")

        assert diary.id > 0
        assert diary.chat_id == 1
        assert diary.date == today
        assert diary.content == "오늘의 일기"
        assert diary.created_at
        assert diary.updated_at

    def test_get_by_date(self):
        """chat_id + date로 조회."""
        store, _ = _make_store()
        today = date.today().isoformat()

        store.add(chat_id=1, date=today, content="내 일기")

        result = store.get_by_date(chat_id=1, date=today)

        assert result is not None
        assert result.content == "내 일기"
        assert result.chat_id == 1

    def test_get_by_date_wrong_chat(self):
        """다른 chat_id로 조회 시 None 반환."""
        store, _ = _make_store()
        today = date.today().isoformat()

        store.add(chat_id=1, date=today, content="내 일기")

        result = store.get_by_date(chat_id=999, date=today)

        assert result is None

    def test_update_diary(self):
        """일기 내용 수정."""
        store, _ = _make_store()
        today = date.today().isoformat()

        diary = store.add(chat_id=1, date=today, content="원본 내용")
        success = store.update(diary.id, "수정된 내용")

        assert success is True

        updated = store.get(diary.id)
        assert updated is not None
        assert updated.content == "수정된 내용"

    def test_delete_diary(self):
        """일기 삭제 후 None 반환 확인."""
        store, _ = _make_store()
        today = date.today().isoformat()

        diary = store.add(chat_id=1, date=today, content="삭제할 일기")
        success = store.delete(diary.id)

        assert success is True
        assert store.get(diary.id) is None

    def test_list_by_chat(self):
        """페이지네이션(offset/limit) 검증."""
        store, _ = _make_store()
        base = date.today()

        # 5개 추가 (날짜 내림차순으로 저장됨)
        for i in range(5):
            d = (base - timedelta(days=i)).isoformat()
            store.add(chat_id=1, date=d, content=f"일기 {i}")

        # 처음 3개
        page1 = store.list_by_chat(chat_id=1, limit=3, offset=0)
        assert len(page1) == 3

        # 다음 2개
        page2 = store.list_by_chat(chat_id=1, limit=3, offset=3)
        assert len(page2) == 2

        # 전체 ID 중복 없음
        ids_p1 = {d.id for d in page1}
        ids_p2 = {d.id for d in page2}
        assert ids_p1.isdisjoint(ids_p2)

    def test_list_by_chat_date_desc_order(self):
        """목록이 날짜 내림차순으로 반환됨."""
        store, _ = _make_store()
        base = date.today()

        dates = []
        for i in range(3):
            d = (base - timedelta(days=i)).isoformat()
            dates.append(d)
            store.add(chat_id=1, date=d, content=f"일기 {i}")

        entries = store.list_by_chat(chat_id=1, limit=10, offset=0)

        returned_dates = [e.date for e in entries]
        assert returned_dates == sorted(returned_dates, reverse=True)

    def test_count_by_chat(self):
        """chat_id별 일기 수 집계."""
        store, _ = _make_store()
        base = date.today()

        assert store.count_by_chat(chat_id=1) == 0

        for i in range(3):
            d = (base - timedelta(days=i)).isoformat()
            store.add(chat_id=1, date=d, content=f"일기 {i}")

        # chat_id=2는 별도 집계
        store.add(chat_id=2, date=base.isoformat(), content="다른 채팅 일기")

        assert store.count_by_chat(chat_id=1) == 3
        assert store.count_by_chat(chat_id=2) == 1

    def test_unique_constraint(self):
        """같은 chat_id + date에 두 번 삽입 시 IntegrityError."""
        store, _ = _make_store()
        today = date.today().isoformat()

        store.add(chat_id=1, date=today, content="첫 번째")

        with pytest.raises(Exception):  # sqlite3.IntegrityError (UNIQUE constraint)
            store.add(chat_id=1, date=today, content="중복 삽입")


# ---------------------------------------------------------------------------
# TestDiaryPlugin
# ---------------------------------------------------------------------------

class TestDiaryPlugin:
    """DiaryPlugin - mock store로 콜백/인터랙션/패턴 동작 검증."""

    # ---- can_handle --------------------------------------------------------

    @pytest.mark.asyncio
    async def test_can_handle_keywords(self):
        """트리거 키워드 인식 - 일기, 일기 쓰기, 일기 목록."""
        plugin, _ = _make_plugin()

        assert await plugin.can_handle("일기", 1) is True
        assert await plugin.can_handle("일기 쓰기", 1) is True
        assert await plugin.can_handle("일기 목록", 1) is True
        assert await plugin.can_handle("일기 보기", 1) is True

    @pytest.mark.asyncio
    async def test_can_handle_exclude_patterns(self):
        """제외 패턴 - AI에게 넘겨야 할 자연어 질문."""
        plugin, _ = _make_plugin()

        assert await plugin.can_handle("일기란 뭐야", 1) is False
        assert await plugin.can_handle("일기이란 뭐", 1) is False
        assert await plugin.can_handle("일기가 뭐야", 1) is False

    # ---- handle (menu) -----------------------------------------------------

    @pytest.mark.asyncio
    async def test_handle_returns_list(self):
        """'일기' 단독 입력 → 목록 응답 (첫 화면이 목록)."""
        plugin, mock_store = _make_plugin()
        mock_store.count_by_chat.return_value = 0
        mock_store.list_by_month.return_value = []

        result = await plugin.handle("일기", 1)

        assert result.handled is True
        assert result.response is not None
        assert result.reply_markup is not None

    # ---- handle_callback: write --------------------------------------------

    def test_callback_write_no_existing(self):
        """diary:write - 오늘 일기 없음 → ForceReply 반환."""
        plugin, mock_store = _make_plugin()
        mock_store.get_by_date.return_value = None

        result = plugin.handle_callback("diary:write", 1)

        assert "force_reply" in result
        assert result.get("interaction_action") == "write"

    def test_callback_write_existing(self):
        """diary:write - 오늘 일기 이미 있음 → 기존 내용 + 수정 버튼."""
        plugin, mock_store = _make_plugin()
        today = date.today().isoformat()
        existing = Diary(
            id=42,
            chat_id=1,
            date=today,
            content="이미 쓴 일기입니다.",
            created_at="2026-03-17T00:00:00",
            updated_at="2026-03-17T00:00:00",
        )
        mock_store.get_by_date.return_value = existing

        result = plugin.handle_callback("diary:write", 1)

        text = result["text"]
        assert "이미 작성" in text
        # 수정 버튼이 callback_data에 diary:edit:42 포함
        markup = result["reply_markup"]
        buttons_flat = [btn for row in markup.inline_keyboard for btn in row]
        cb_datas = [b.callback_data for b in buttons_flat]
        assert "diary:edit:42" in cb_datas

    # ---- handle_callback: list ---------------------------------------------

    def test_callback_list_empty(self):
        """diary:list - 일기 없음 → '작성된 일기가 없습니다' 메시지."""
        plugin, mock_store = _make_plugin()
        mock_store.count_by_chat.return_value = 0
        mock_store.list_by_month.return_value = []

        result = plugin.handle_callback("diary:list", 1)

        assert "없습니다" in result["text"]

    def test_callback_list_with_entries(self):
        """diary:list - 일기 있음 → 월별 목록 표시."""
        plugin, mock_store = _make_plugin()
        entries = [
            Diary(id=1, chat_id=1, date="2026-03-17", content="월요일 일기",
                  created_at="2026-03-17T10:00:00", updated_at="2026-03-17T10:00:00"),
            Diary(id=2, chat_id=1, date="2026-03-16", content="일요일 일기",
                  created_at="2026-03-16T10:00:00", updated_at="2026-03-16T10:00:00"),
        ]
        mock_store.count_by_chat.return_value = 2
        mock_store.list_by_month.return_value = entries

        result = plugin.handle_callback("diary:list", 1)

        assert "일기 목록" in result["text"]
        markup = result["reply_markup"]
        buttons_flat = [btn for row in markup.inline_keyboard for btn in row]
        cb_datas = [b.callback_data for b in buttons_flat]
        assert "diary:view:1" in cb_datas
        assert "diary:view:2" in cb_datas

    # ---- handle_callback: view ---------------------------------------------

    def test_callback_view(self):
        """diary:view:1 → 내용 + 수정/삭제 버튼."""
        plugin, mock_store = _make_plugin()
        diary = Diary(
            id=1, chat_id=1, date="2026-03-17", content="오늘의 기록",
            created_at="2026-03-17T10:00:00", updated_at="2026-03-17T10:00:00",
        )
        mock_store.get.return_value = diary

        result = plugin.handle_callback("diary:view:1", 1)

        assert "오늘의 기록" in result["text"]
        markup = result["reply_markup"]
        buttons_flat = [btn for row in markup.inline_keyboard for btn in row]
        cb_datas = [b.callback_data for b in buttons_flat]
        assert "diary:edit:1" in cb_datas
        assert "diary:del:1" in cb_datas

    # ---- handle_callback: delete -------------------------------------------

    def test_callback_delete_confirm(self):
        """diary:del:1 → 삭제 확인 프롬프트."""
        plugin, mock_store = _make_plugin()
        diary = Diary(
            id=1, chat_id=1, date="2026-03-17", content="삭제 대상 일기",
            created_at="2026-03-17T10:00:00", updated_at="2026-03-17T10:00:00",
        )
        mock_store.get.return_value = diary

        result = plugin.handle_callback("diary:del:1", 1)

        assert "삭제" in result["text"]
        markup = result["reply_markup"]
        buttons_flat = [btn for row in markup.inline_keyboard for btn in row]
        cb_datas = [b.callback_data for b in buttons_flat]
        assert "diary:del_confirm:1" in cb_datas

    def test_callback_delete_execute(self):
        """diary:del_confirm:1 → 삭제 실행 후 목록으로 이동."""
        plugin, mock_store = _make_plugin()
        diary = Diary(
            id=1, chat_id=1, date="2026-03-17", content="삭제될 일기",
            created_at="2026-03-17T10:00:00", updated_at="2026-03-17T10:00:00",
        )
        mock_store.get.return_value = diary
        mock_store.count_by_chat.return_value = 0
        mock_store.list_by_month.return_value = []

        result = plugin.handle_callback("diary:del_confirm:1", 1)

        mock_store.delete.assert_called_once_with(1)
        assert "삭제되었습니다" in result["text"]

    # ---- handle_interaction: write -----------------------------------------

    def test_process_write_via_interaction(self):
        """handle_interaction - write 액션 → 일기 저장."""
        from src.plugins.loader import PluginInteraction

        plugin, mock_store = _make_plugin()
        today = date.today().isoformat()
        saved = Diary(
            id=10, chat_id=1, date=today, content="새로 쓴 일기",
            created_at="2026-03-17T10:00:00", updated_at="2026-03-17T10:00:00",
        )
        mock_store.get_by_date.return_value = None  # no existing
        mock_store.add.return_value = saved

        interaction = PluginInteraction(plugin_name="diary", chat_id=1, action="write")
        result = plugin.handle_interaction("새로 쓴 일기", 1, interaction)

        mock_store.add.assert_called_once()
        assert "저장되었습니다" in result["text"]

    # ---- handle_interaction: edit ------------------------------------------

    def test_process_edit_via_interaction(self):
        """handle_interaction - edit 액션 → 일기 수정."""
        from src.plugins.loader import PluginInteraction

        plugin, mock_store = _make_plugin()
        original = Diary(
            id=5, chat_id=1, date="2026-03-16", content="원본",
            created_at="2026-03-16T10:00:00", updated_at="2026-03-16T10:00:00",
        )
        mock_store.get.return_value = original
        mock_store.update.return_value = True

        interaction = PluginInteraction(
            plugin_name="diary", chat_id=1, action="edit", state={"diary_id": 5}
        )
        result = plugin.handle_interaction("수정된 내용", 1, interaction)

        mock_store.update.assert_called_once_with(5, "수정된 내용")
        assert "수정되었습니다" in result["text"]

    # ---- ownership check ---------------------------------------------------

    def test_ownership_check_on_delete(self):
        """다른 chat_id의 일기 삭제 시도 → '권한이 없습니다'."""
        plugin, mock_store = _make_plugin()
        diary = Diary(
            id=1, chat_id=999, date="2026-03-17", content="남의 일기",
            created_at="2026-03-17T10:00:00", updated_at="2026-03-17T10:00:00",
        )
        mock_store.get.return_value = diary

        result = plugin.handle_callback("diary:del:1", chat_id=1)

        assert "권한이 없습니다" in result["text"]
        mock_store.delete.assert_not_called()

    def test_ownership_check_on_view(self):
        """다른 chat_id의 일기 조회 시도 → '권한이 없습니다'."""
        plugin, mock_store = _make_plugin()
        diary = Diary(
            id=2, chat_id=999, date="2026-03-17", content="남의 일기",
            created_at="2026-03-17T10:00:00", updated_at="2026-03-17T10:00:00",
        )
        mock_store.get.return_value = diary

        result = plugin.handle_callback("diary:view:2", chat_id=1)

        assert "권한이 없습니다" in result["text"]

    # ---- scheduled action --------------------------------------------------

    @pytest.mark.asyncio
    async def test_scheduled_action_no_entry(self):
        """daily_diary 스케줄 - 오늘 일기 없음 → 작성 유도 메시지 반환."""
        plugin, mock_store = _make_plugin()
        mock_store.get_by_date.return_value = None

        result = await plugin.execute_scheduled_action("daily_diary", 1)

        assert result != ""
        assert "일기" in result

    @pytest.mark.asyncio
    async def test_scheduled_action_already_written(self):
        """daily_diary 스케줄 - 오늘 일기 이미 있음 → 완료 메시지 반환."""
        plugin, mock_store = _make_plugin()
        today = date.today().isoformat()
        existing = Diary(
            id=1, chat_id=1, date=today, content="작성됨",
            created_at="2026-03-17T10:00:00", updated_at="2026-03-17T10:00:00",
        )
        mock_store.get_by_date.return_value = existing

        result = await plugin.execute_scheduled_action("daily_diary", 1)

        assert result != ""
        assert "이미 작성" in result

    # ---- handle_callback: write_yesterday ------------------------------------

    def test_menu_shows_yesterday_status(self):
        """메뉴에 어제 상태 표시 및 '⏪ 어제 쓰기' 버튼 확인."""
        plugin, mock_store = _make_plugin()
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        # today: no entry, yesterday: has entry
        mock_store.get_by_date.side_effect = lambda cid, d: (
            None if d == today else (
                Diary(id=1, chat_id=1, date=yesterday, content="어제 일기",
                      created_at="2026-03-16T10:00:00", updated_at="2026-03-16T10:00:00")
                if d == yesterday else None
            )
        )
        mock_store.count_by_chat.return_value = 1

        result = plugin._handle_menu(1)

        text = result["text"]
        # Today status line
        assert "📝 오늘 일기 미작성" in text
        # Yesterday status line
        assert "✅ 어제 일기 작성됨" in text

        # Check for "⏪ 어제 쓰기" button
        markup = result["reply_markup"]
        buttons_flat = [btn for row in markup.inline_keyboard for btn in row]
        button_texts = [b.text for b in buttons_flat]
        assert "⏪ 어제 쓰기" in button_texts

    def test_callback_write_yesterday_no_existing(self):
        """diary:write_yesterday - 어제 일기 없음 → ForceReply with '어제' labels."""
        plugin, mock_store = _make_plugin()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        mock_store.get_by_date.return_value = None

        result = plugin.handle_callback("diary:write_yesterday", 1)

        # Should have ForceReply for yesterday
        assert "force_reply" in result
        assert "force_reply_prompt" in result
        assert result.get("interaction_action") == "write"
        assert result.get("interaction_state", {}).get("target_date") == yesterday

        # Check placeholder and prompt contain yesterday labels
        force_reply = result["force_reply"]
        assert "어제" in force_reply.input_field_placeholder or "어제" in result.get("force_reply_prompt", "")

    def test_callback_write_yesterday_existing(self):
        """diary:write_yesterday - 어제 일기 이미 있음 → 기존 내용 + 수정/보기 버튼."""
        plugin, mock_store = _make_plugin()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        existing = Diary(
            id=42, chat_id=1, date=yesterday, content="어제 일기입니다.",
            created_at="2026-03-16T10:00:00", updated_at="2026-03-16T10:00:00",
        )
        mock_store.get_by_date.return_value = existing

        result = plugin.handle_callback("diary:write_yesterday", 1)

        text = result["text"]
        assert "이미 작성" in text
        assert "어제의 일기" in text

        # Check for edit/view buttons
        markup = result["reply_markup"]
        buttons_flat = [btn for row in markup.inline_keyboard for btn in row]
        cb_datas = [b.callback_data for b in buttons_flat]
        assert "diary:edit:42" in cb_datas
        assert "diary:view:42" in cb_datas

    def test_process_write_yesterday_via_interaction(self):
        """handle_interaction - target_date=yesterday → 어제 날짜로 저장."""
        from src.plugins.loader import PluginInteraction

        plugin, mock_store = _make_plugin()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        saved = Diary(
            id=10, chat_id=1, date=yesterday, content="어제의 추억",
            created_at="2026-03-16T10:00:00", updated_at="2026-03-16T10:00:00",
        )
        mock_store.get_by_date.return_value = None  # no existing
        mock_store.add.return_value = saved

        interaction = PluginInteraction(
            plugin_name="diary", chat_id=1, action="write",
            state={"target_date": yesterday}
        )
        result = plugin.handle_interaction("어제의 추억", 1, interaction)

        # Verify add was called with yesterday's date
        mock_store.add.assert_called_once_with(1, yesterday, "어제의 추억")
        assert "저장되었습니다" in result["text"]
