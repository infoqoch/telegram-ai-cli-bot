"""Question bank plugin tests."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from plugins.builtin.question_bank.plugin import QuestionBankPlugin
from src.repository.adapters.plugin_storage import RepositoryQuestionBankStore


def _make_plugin() -> QuestionBankPlugin:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    plugin = QuestionBankPlugin()
    conn.executescript(plugin.get_schema())
    repo = MagicMock()
    repo._conn = conn
    plugin._storage = RepositoryQuestionBankStore(repo)
    return plugin


def _add_short_question(plugin: QuestionBankPlugin, chat_id: int = 1) -> int:
    bank = plugin.store.ensure_default_bank(chat_id)
    conn = plugin.store._repo._conn
    cursor = conn.execute(
        """INSERT INTO qb_questions
           (bank_id, chat_id, type, prompt, answer_text, explanation)
           VALUES (?, ?, 'short', '대한민국의 수도는?', '서울', '대한민국의 수도는 서울입니다.')""",
        (bank.id, chat_id),
    )
    conn.commit()
    return cursor.lastrowid


def _add_alias_short_question(plugin: QuestionBankPlugin, chat_id: int = 1) -> int:
    bank = plugin.store.ensure_default_bank(chat_id)
    conn = plugin.store._repo._conn
    cursor = conn.execute(
        """INSERT INTO qb_questions
           (bank_id, chat_id, type, prompt, answer_text, explanation)
           VALUES (?, ?, 'short', '대한민국의 수도는?', '서울 || Seoul', '대한민국의 수도는 서울입니다.')""",
        (bank.id, chat_id),
    )
    conn.commit()
    return cursor.lastrowid


def _add_ambiguous_short_question(plugin: QuestionBankPlugin, chat_id: int = 1) -> int:
    bank = plugin.store.ensure_default_bank(chat_id)
    conn = plugin.store._repo._conn
    cursor = conn.execute(
        """INSERT INTO qb_questions
           (bank_id, chat_id, type, prompt, answer_text, explanation)
           VALUES (?, ?, 'short',
                   'TCP 연결을 수립할 때 사용하는 3단계 절차의 이름과, 각 단계의 패킷 플래그를 순서대로 쓰시오.',
                   'Three-Way Handshake: SYN → SYN-ACK → ACK',
                   '이 문제는 이름과 순서를 모두 요구합니다.')""",
        (bank.id, chat_id),
    )
    conn.commit()
    return cursor.lastrowid


def _add_loose_short_question(plugin: QuestionBankPlugin, chat_id: int = 1) -> int:
    bank = plugin.store.ensure_default_bank(chat_id)
    conn = plugin.store._repo._conn
    cursor = conn.execute(
        """INSERT INTO qb_questions
           (bank_id, chat_id, type, prompt, answer_text, explanation, match_policy)
           VALUES (?, ?, 'short', 'OAC의 풀네임은?', 'OAC || Origin Access Control',
                   'OAC는 Origin Access Control의 약자입니다.', 'loose_text')""",
        (bank.id, chat_id),
    )
    conn.commit()
    return cursor.lastrowid


def _add_symbol_short_question(plugin: QuestionBankPlugin, chat_id: int = 1) -> int:
    bank = plugin.store.ensure_default_bank(chat_id)
    conn = plugin.store._repo._conn
    cursor = conn.execute(
        """INSERT INTO qb_questions
           (bank_id, chat_id, type, prompt, answer_text, explanation, match_policy)
           VALUES (?, ?, 'short', 'C++의 언어 이름을 쓰시오.', 'C++',
                   '플러스 기호가 의미를 가진다.', 'loose_text')""",
        (bank.id, chat_id),
    )
    conn.commit()
    return cursor.lastrowid


def _add_choice_question(plugin: QuestionBankPlugin, chat_id: int = 1) -> int:
    bank = plugin.store.ensure_default_bank(chat_id)
    conn = plugin.store._repo._conn
    cursor = conn.execute(
        """INSERT INTO qb_questions
           (bank_id, chat_id, type, prompt, correct_option_no, explanation)
           VALUES (?, ?, 'multiple_choice', 'HTTP 성공 상태 코드는?', 2, 'HTTP 200은 성공입니다.')""",
        (bank.id, chat_id),
    )
    question_id = cursor.lastrowid
    conn.executemany(
        "INSERT INTO qb_options (question_id, option_no, text) VALUES (?, ?, ?)",
        [
            (question_id, 1, "404"),
            (question_id, 2, "200"),
            (question_id, 3, "500"),
        ],
    )
    conn.commit()
    return question_id


def _add_subjective_question(plugin: QuestionBankPlugin, chat_id: int = 1) -> int:
    bank = plugin.store.ensure_default_bank(chat_id)
    conn = plugin.store._repo._conn
    cursor = conn.execute(
        """INSERT INTO qb_questions
           (bank_id, chat_id, type, prompt, model_answer, grading_rubric, pass_score)
           VALUES (?, ?, 'subjective', 'REST API의 특징을 설명하세요.',
                   '자원 URI, HTTP 메서드, stateless를 설명한다.',
                   '세 핵심 중 두 개 이상을 정확히 설명하면 통과.', 0.7)""",
        (bank.id, chat_id),
    )
    conn.commit()
    return cursor.lastrowid


def test_main_screen_has_ai_creation_not_manual_add():
    plugin = _make_plugin()

    result = plugin._handle_main(1)

    assert "AI로 문제 만들기" in str(result["reply_markup"])
    assert "문제 추가" not in result["text"]


def test_multiple_choice_answer_records_attempt_and_ai_chat_button():
    plugin = _make_plugin()
    question_id = _add_choice_question(plugin)

    result = plugin._handle_choice_answer(1, question_id, 2)

    assert "✅" in result["text"]
    assert "AI와 대화" in str(result["reply_markup"])
    stats = plugin.store.stats(1)
    assert stats["attempts"] == 1
    assert stats["correct"] == 1


def test_short_answer_uses_strict_trim_matching():
    plugin = _make_plugin()
    question_id = _add_short_question(plugin)
    interaction = MagicMock(action="answer", state={"question_id": question_id})

    result = plugin.handle_interaction(" 서울 ", 1, interaction=interaction)

    assert "✅" in result["text"]
    assert plugin.store.stats(1)["correct"] == 1


def test_short_answer_supports_explicit_aliases():
    plugin = _make_plugin()
    question_id = _add_alias_short_question(plugin)
    interaction = MagicMock(action="answer", state={"question_id": question_id})

    result = plugin.handle_interaction("seoul", 1, interaction=interaction)

    assert "✅" in result["text"]
    assert plugin.store.stats(1)["correct"] == 1


def test_ambiguous_short_answer_dispatches_ai_grading():
    plugin = _make_plugin()
    question_id = _add_ambiguous_short_question(plugin)
    interaction = MagicMock(action="answer", state={"question_id": question_id})

    result = plugin.handle_interaction("SYN, SYN-ACK, ACK", 1, interaction=interaction)

    assert result["dispatch_ai"] is True
    assert "Short Answer Grading" in result["ai_message"]
    assert "Three-Way Handshake" in result["ai_message"]


def test_loose_text_ignores_spaces_and_parentheses_only():
    plugin = _make_plugin()
    question_id = _add_loose_short_question(plugin)
    interaction = MagicMock(action="answer", state={"question_id": question_id})

    result = plugin.handle_interaction("OriginAccessControl", 1, interaction=interaction)

    assert "✅" in result["text"]


def test_loose_text_preserves_meaningful_symbols():
    plugin = _make_plugin()
    question_id = _add_symbol_short_question(plugin)
    interaction = MagicMock(action="answer", state={"question_id": question_id})

    result = plugin.handle_interaction("c", 1, interaction=interaction)

    assert "❌" in result["text"]


def test_subjective_answer_dispatches_ai_grading_prompt():
    plugin = _make_plugin()
    question_id = _add_subjective_question(plugin)
    interaction = MagicMock(action="answer", state={"question_id": question_id})

    result = plugin.handle_interaction("HTTP 메서드와 stateless를 사용합니다.", 1, interaction=interaction)

    assert result["dispatch_ai"] is True
    assert "UPDATE qb_attempts" in result["ai_message"]
    assert "Subjective Grading" in result["ai_message"]
    assert plugin.store.stats(1)["attempts"] == 1


def test_ai_followup_dispatch_includes_attempt_context():
    plugin = _make_plugin()
    question_id = _add_short_question(plugin)
    interaction = MagicMock(action="answer", state={"question_id": question_id})
    answer_result = plugin.handle_interaction("부산", 1, interaction=interaction)
    assert "❌" in answer_result["text"]

    attempt = plugin.store.recent_wrong_attempts(1)[0]
    followup = MagicMock(action="ask_ai", state={"attempt_id": attempt.id})
    result = plugin.handle_interaction("왜 틀렸어?", 1, interaction=followup)

    assert result["dispatch_ai"] is True
    assert "왜 틀렸어?" in result["ai_message"]
    assert "대한민국의 수도" in result["ai_message"]


@pytest.mark.asyncio
async def test_can_handle_exact_keywords():
    plugin = _make_plugin()

    assert await plugin.can_handle("문제은행", 1)
    assert await plugin.can_handle("퀴즈", 1)
    assert not await plugin.can_handle("문제은행이 뭐야", 1)
