"""Question bank plugin - AI-authored questions and lightweight practice."""

from __future__ import annotations

import re
from typing import Optional, TYPE_CHECKING, cast

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.formatters import escape_html
from src.plugins.loader import (
    PLUGIN_SURFACE_CATALOG,
    PLUGIN_SURFACE_MAIN_MENU,
    Plugin,
    PluginInteraction,
    PluginMenuEntry,
    PluginResult,
    ScheduledAction,
)
from src.plugins.storage import QuestionBankStore
from src.repository.adapters.plugin_storage import RepositoryQuestionBankStore
from src.repository.repository import QuestionBankAttempt, QuestionBankQuestion

if TYPE_CHECKING:
    from src.repository.repository import QuestionBankScheduleConfig, Schedule


TYPE_LABELS = {
    "short": "단답식",
    "multiple_choice": "객관식",
    "subjective": "주관식",
}

SCOPE_ALL = "all"
SCOPE_WRONG_ALL = "wa"

_SHORT_PROMPT_COMPLEX_PATTERNS = [
    re.compile(pattern)
    for pattern in [
        r"\(\d+\)",
        r"\d+가지",
        r"\d+종류",
        r"각각",
        r"모두",
        r"순서대로",
        r"차이",
        r"비교",
        r"예를",
        r"이름과",
        r"포트와",
        r"두 가지",
        r"세 가지",
        r"네 가지",
        r"다섯 가지",
    ]
]


class QuestionBankPlugin(Plugin):
    """MVP question bank plugin."""

    name = "question_bank"
    description = "AI-created question bank and practice"
    display_name = "Question Bank"
    MENU_ENTRY = PluginMenuEntry(
        label="📚 Question Bank",
        surfaces=(PLUGIN_SURFACE_CATALOG, PLUGIN_SURFACE_MAIN_MENU),
        priority=60,
        default_promoted=False,
    )
    usage = (
        "📚 <b>Question Bank</b>\n\n"
        "<code>문제은행</code>, <code>퀴즈</code>, or <code>/question_bank</code>\n\n"
        "<b>Features</b>\n"
        "• AI creates questions by writing directly to plugin tables\n"
        "• Practice from all banks, one bank, or wrong answers only\n"
        "• Ask AI about each graded result\n"
        "• AI can register plugin schedules for scheduled practice"
    )

    CALLBACK_PREFIX = "qb:"
    FORCE_REPLY_MARKER = "qb_answer"

    TRIGGER_KEYWORDS = ["question_bank", "quiz", "문제은행", "퀴즈", "문제"]
    EXCLUDE_PATTERNS = [
        r"(란|이란|가|이)\s*(뭐|무엇|뭔)",
        r"영어로|번역|translate",
        r"어떻게|왜|언제|어디",
        r"알려줘|설명|뜻",
    ]

    def get_schema(self) -> str:
        return """
CREATE TABLE IF NOT EXISTS qb_banks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_qb_banks_chat_id ON qb_banks(chat_id);
CREATE INDEX IF NOT EXISTS idx_qb_banks_chat_archived ON qb_banks(chat_id, archived);

CREATE TABLE IF NOT EXISTS qb_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('short', 'multiple_choice', 'subjective')),
    prompt TEXT NOT NULL,
    answer_text TEXT,
    correct_option_no INTEGER,
    model_answer TEXT,
    grading_rubric TEXT,
    explanation TEXT NOT NULL DEFAULT '',
    points REAL NOT NULL DEFAULT 1,
    pass_score REAL NOT NULL DEFAULT 1,
    match_policy TEXT NOT NULL DEFAULT 'strict_trim',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (bank_id) REFERENCES qb_banks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_qb_questions_chat_id ON qb_questions(chat_id);
CREATE INDEX IF NOT EXISTS idx_qb_questions_bank_id ON qb_questions(bank_id);
CREATE INDEX IF NOT EXISTS idx_qb_questions_chat_active ON qb_questions(chat_id, active);
CREATE INDEX IF NOT EXISTS idx_qb_questions_type ON qb_questions(type);

CREATE TABLE IF NOT EXISTS qb_options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    option_no INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (question_id, option_no),
    FOREIGN KEY (question_id) REFERENCES qb_questions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_qb_options_question_id ON qb_options(question_id);

CREATE TABLE IF NOT EXISTS qb_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    answer_text TEXT NOT NULL,
    selected_option_no INTEGER,
    is_correct INTEGER,
    score REAL,
    feedback TEXT NOT NULL DEFAULT '',
    ai_status TEXT NOT NULL DEFAULT 'not_needed',
    ai_model TEXT,
    ai_raw_response TEXT,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
    evaluated_at TEXT,
    FOREIGN KEY (question_id) REFERENCES qb_questions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_qb_attempts_chat_id ON qb_attempts(chat_id);
CREATE INDEX IF NOT EXISTS idx_qb_attempts_question_id ON qb_attempts(question_id);
CREATE INDEX IF NOT EXISTS idx_qb_attempts_chat_correct ON qb_attempts(chat_id, is_correct);
CREATE INDEX IF NOT EXISTS idx_qb_attempts_submitted_at ON qb_attempts(submitted_at DESC);

CREATE TABLE IF NOT EXISTS qb_schedule_configs (
    schedule_id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('all', 'bank', 'wrong_all', 'wrong_bank')),
    bank_id INTEGER,
    question_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (schedule_id) REFERENCES schedules(id) ON DELETE CASCADE,
    FOREIGN KEY (bank_id) REFERENCES qb_banks(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_qb_schedule_configs_chat_id ON qb_schedule_configs(chat_id);
CREATE INDEX IF NOT EXISTS idx_qb_schedule_configs_bank_id ON qb_schedule_configs(bank_id);

CREATE TRIGGER IF NOT EXISTS update_qb_banks_timestamp
AFTER UPDATE ON qb_banks
BEGIN
    UPDATE qb_banks SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS update_qb_questions_timestamp
AFTER UPDATE ON qb_questions
BEGIN
    UPDATE qb_questions SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS update_qb_schedule_configs_timestamp
AFTER UPDATE ON qb_schedule_configs
BEGIN
    UPDATE qb_schedule_configs SET updated_at = datetime('now') WHERE schedule_id = NEW.schedule_id;
END;
"""

    @property
    def store(self) -> QuestionBankStore:
        """Question bank storage adapter bound by the plugin runtime."""
        return cast(QuestionBankStore, self.storage)

    def build_storage(self, repository):
        """Bind question bank persistence through a bounded adapter."""
        return RepositoryQuestionBankStore(repository)

    async def handle(self, message: str, chat_id: int) -> PluginResult:
        result = self._handle_main(chat_id)
        return PluginResult(
            handled=True,
            response=result["text"],
            reply_markup=result.get("reply_markup"),
        )

    def handle_callback(self, callback_data: str, chat_id: int) -> dict:
        parts = callback_data.split(":")
        action = parts[1] if len(parts) > 1 else "main"

        try:
            if action == "main":
                return self._handle_main(chat_id)
            if action == "banks":
                return self._handle_banks(chat_id)
            if action == "bank":
                return self._handle_bank_detail(chat_id, int(parts[2]))
            if action == "practice":
                scope_token = parts[2] if len(parts) > 2 else SCOPE_ALL
                bank_id, wrong_only = self._decode_scope_token(scope_token)
                return self._handle_practice(chat_id, bank_id=bank_id, wrong_only=wrong_only, scope_token=scope_token)
            if action == "wrong":
                scope_token = parts[2] if len(parts) > 2 else SCOPE_ALL
                bank_id, _ = self._decode_scope_token(scope_token)
                return self._handle_wrong(chat_id, bank_id=bank_id)
            if action == "wrong_practice":
                return self._handle_practice(chat_id, wrong_only=True, scope_token=SCOPE_WRONG_ALL)
            if action == "stats":
                scope_token = parts[2] if len(parts) > 2 else SCOPE_ALL
                bank_id, _ = self._decode_scope_token(scope_token)
                return self._handle_stats(chat_id, bank_id=bank_id)
            if action == "q":
                scope_token = parts[3] if len(parts) > 3 else SCOPE_ALL
                return self._handle_question(chat_id, int(parts[2]), scope_token=scope_token)
            if action == "a":
                scope_token = parts[4] if len(parts) > 4 else SCOPE_ALL
                return self._handle_choice_answer(
                    chat_id,
                    int(parts[2]),
                    int(parts[3]),
                    scope_token=scope_token,
                )
            if action == "reply":
                scope_token = parts[3] if len(parts) > 3 else SCOPE_ALL
                return self._handle_answer_prompt(chat_id, int(parts[2]), scope_token=scope_token)
            if action == "ask":
                return self._handle_ai_question_prompt(chat_id, int(parts[2]))
        except (IndexError, ValueError):
            return {"text": "❌ Invalid request.", "edit": True}

        return {"text": "❌ Unknown command.", "edit": True}

    def handle_interaction(
        self,
        message: str,
        chat_id: int,
        interaction: Optional[PluginInteraction] = None,
    ) -> dict:
        action = interaction.action if interaction else "answer"
        state = interaction.state if interaction else {}

        if action == "ask_ai":
            attempt_id = int(state.get("attempt_id", 0))
            return self._process_ai_question(chat_id, attempt_id, message)

        question_id = int(state.get("question_id", 0))
        scope_token = str(state.get("scope_token") or SCOPE_ALL)
        return self._process_text_answer(chat_id, question_id, message, scope_token=scope_token)

    def get_scheduled_actions(self) -> list[ScheduledAction]:
        return [
            ScheduledAction(
                name="scheduled_practice",
                description="📚 Question bank practice",
                recommended_hour=9,
                recommended_minute=0,
            ),
        ]

    async def execute_scheduled_action(
        self,
        action_name: str,
        chat_id: int,
        schedule: Optional["Schedule"] = None,
    ) -> str | dict | None:
        if action_name != "scheduled_practice":
            raise NotImplementedError(f"Action '{action_name}' not implemented")

        config = self._resolve_schedule_config(chat_id, schedule)
        scope_token = self._scope_token_from_config(config)
        bank_id, wrong_only = self._decode_scope_token(scope_token)
        question = self.store.pick_question(chat_id, bank_id=bank_id, wrong_only=wrong_only)
        if not question:
            if wrong_only:
                return None
            return self._build_empty_state(chat_id, bank_id=bank_id, wrong_only=False, edit=False)

        result = self._render_question(chat_id, question, scope_token=scope_token)
        result["edit"] = False
        return result

    async def handle_ai_completion(
        self,
        action_name: str,
        chat_id: int,
        payload: dict[str, object],
        *,
        ai_response: str,
        ai_error: Optional[str],
        session_id: Optional[str] = None,
    ) -> str | dict | None:
        del ai_response, session_id
        if ai_error or action_name != "render_attempt_result":
            return None

        attempt_id = payload.get("attempt_id")
        scope_token = payload.get("scope_token")
        if not isinstance(attempt_id, int) or not isinstance(scope_token, str):
            return None

        attempt = self.store.get_attempt(attempt_id, chat_id)
        if not attempt or attempt.is_correct is None:
            return None

        question = self.store.get_question(attempt.question_id, chat_id)
        if not question:
            return None

        return {
            "text": self._build_ai_completion_result_text(question, attempt),
            "delivery_buttons": self._build_ai_completion_buttons(question.id, attempt.id, scope_token),
        }

    def _handle_main(self, chat_id: int) -> dict:
        self.store.ensure_default_bank(chat_id)
        stats = self.store.stats(chat_id)

        accuracy = "-"
        if stats["attempts"] > 0:
            accuracy = f"{round(stats['correct'] / stats['attempts'] * 100)}%"

        text = (
            "📚 <b>Question Bank</b>\n\n"
            f"문제집: <b>{stats['banks']}</b>\n"
            f"문제: <b>{stats['questions']}</b>\n"
            f"풀이: <b>{stats['attempts']}</b>\n"
            f"정답률: <b>{accuracy}</b>\n\n"
            "문제 생성/수정과 스케줄 등록은 AI와 대화해서 처리합니다."
        )
        buttons = [
            [
                InlineKeyboardButton("🎲 전체 랜덤", callback_data=f"qb:practice:{SCOPE_ALL}"),
                InlineKeyboardButton("📁 문제집", callback_data="qb:banks"),
            ],
            [
                InlineKeyboardButton("❌ 오답 보기", callback_data=f"qb:wrong:{SCOPE_ALL}"),
                InlineKeyboardButton("📊 통계", callback_data=f"qb:stats:{SCOPE_ALL}"),
            ],
            [InlineKeyboardButton("✨ AI로 문제 만들기", callback_data="aiwork:question_bank")],
            [InlineKeyboardButton("🔄 Refresh", callback_data="qb:main")],
        ]
        return {"text": text, "reply_markup": InlineKeyboardMarkup(buttons), "edit": True}

    def _handle_banks(self, chat_id: int) -> dict:
        banks = self.store.list_banks(chat_id)
        if not banks:
            return self._build_empty_state(chat_id, edit=True)

        lines = ["📁 <b>문제집 목록</b>\n"]
        buttons: list[list[InlineKeyboardButton]] = []
        for bank in banks:
            stats = self.store.stats(chat_id, bank_id=bank.id)
            accuracy = "-"
            if stats["attempts"] > 0:
                accuracy = f"{round(stats['correct'] / stats['attempts'] * 100)}%"
            lines.append(
                f"#{bank.id} <b>{escape_html(bank.title)}</b>  "
                f"문제 {stats['questions']} / 정답률 {accuracy}"
            )
            buttons.append([
                InlineKeyboardButton(
                    f"📁 {bank.title[:24]}",
                    callback_data=f"qb:bank:{bank.id}",
                ),
            ])

        buttons.append([InlineKeyboardButton("✨ AI로 문제 만들기", callback_data="aiwork:question_bank")])
        buttons.append([InlineKeyboardButton("⬅️ 메인", callback_data="qb:main")])
        return {"text": "\n".join(lines), "reply_markup": InlineKeyboardMarkup(buttons), "edit": True}

    def _handle_bank_detail(self, chat_id: int, bank_id: int) -> dict:
        bank = self.store.get_bank(bank_id, chat_id)
        if not bank:
            return {"text": "❌ Problem bank not found.", "edit": True}

        stats = self.store.stats(chat_id, bank_id=bank.id)
        accuracy = "-"
        if stats["attempts"] > 0:
            accuracy = f"{round(stats['correct'] / stats['attempts'] * 100)}%"

        lines = [
            "📁 <b>문제집</b>",
            "",
            f"<b>{escape_html(bank.title)}</b>",
        ]
        if bank.description:
            lines.extend(["", escape_html(bank.description)])
        lines.extend(
            [
                "",
                f"문제: <b>{stats['questions']}</b>",
                f"풀이: <b>{stats['attempts']}</b>",
                f"정답률: <b>{accuracy}</b>",
                "",
                "AI에게 이 문제집 이름을 말하면 해당 문제집으로 문제 생성/스케줄 등록을 요청할 수 있습니다.",
            ]
        )
        buttons = [
            [
                InlineKeyboardButton("▶️ 이 문제집 풀기", callback_data=f"qb:practice:{self._bank_scope_token(bank.id)}"),
                InlineKeyboardButton("❌ 오답 보기", callback_data=f"qb:wrong:{self._bank_scope_token(bank.id)}"),
            ],
            [InlineKeyboardButton("📊 통계", callback_data=f"qb:stats:{self._bank_scope_token(bank.id)}")],
            [InlineKeyboardButton("✨ AI로 작업", callback_data="aiwork:question_bank")],
            [InlineKeyboardButton("⬅️ 문제집", callback_data="qb:banks")],
        ]
        return {"text": "\n".join(lines), "reply_markup": InlineKeyboardMarkup(buttons), "edit": True}

    def _handle_practice(
        self,
        chat_id: int,
        *,
        bank_id: Optional[int] = None,
        wrong_only: bool = False,
        scope_token: str = SCOPE_ALL,
    ) -> dict:
        question = self.store.pick_question(chat_id, bank_id=bank_id, wrong_only=wrong_only)
        if not question:
            return self._build_empty_state(chat_id, bank_id=bank_id, wrong_only=wrong_only, edit=True)
        return self._render_question(chat_id, question, scope_token=scope_token)

    def _handle_question(self, chat_id: int, question_id: int, *, scope_token: str = SCOPE_ALL) -> dict:
        question = self.store.get_question(question_id, chat_id)
        if not question:
            return {"text": "❌ Question not found.", "edit": True}
        return self._render_question(chat_id, question, scope_token=scope_token)

    def _render_question(self, chat_id: int, question: QuestionBankQuestion, *, scope_token: str) -> dict:
        bank = self.store.get_bank(question.bank_id, chat_id)
        bank_title = bank.title if bank else f"Bank #{question.bank_id}"
        type_label = TYPE_LABELS.get(question.type, question.type)
        if question.type == "short" and self._short_requires_ai(question):
            type_label = "단답식 (AI 채점)"
        lines = [
            f"📝 <b>문제 #{question.id}</b>",
            f"📁 <b>{escape_html(bank_title)}</b>",
            f"<i>{escape_html(self._scope_label(chat_id, scope_token))}</i>",
            f"<i>{type_label}</i>",
            "",
            escape_html(question.prompt),
        ]
        buttons: list[list[InlineKeyboardButton]] = []

        if question.type == "multiple_choice":
            options = self.store.get_options(question.id)
            if not options:
                lines.append("\n⚠️ 선택지가 없습니다. AI로 문제 데이터를 점검하세요.")
                buttons.append([InlineKeyboardButton("✨ AI와 작업", callback_data="aiwork:question_bank")])
            for option in options:
                lines.append(f"{option.option_no}. {escape_html(option.text)}")
            if options:
                buttons.extend(self._build_choice_buttons(question.id, options, scope_token))
        else:
            if question.type == "short" and self._short_requires_ai(question):
                lines.extend(["", "답안의 의미를 기준으로 AI가 판정합니다."])
            buttons.append([InlineKeyboardButton("✍️ 답 입력", callback_data=f"qb:reply:{question.id}:{scope_token}")])

        buttons.append([
            InlineKeyboardButton("⏭️ 다른 문제", callback_data=f"qb:practice:{scope_token}"),
            InlineKeyboardButton("⬅️ 메인", callback_data="qb:main"),
        ])
        return {"text": "\n".join(lines), "reply_markup": InlineKeyboardMarkup(buttons), "edit": True}

    def _handle_choice_answer(
        self,
        chat_id: int,
        question_id: int,
        selected_option_no: int,
        *,
        scope_token: str = SCOPE_ALL,
    ) -> dict:
        question = self.store.get_question(question_id, chat_id)
        if not question or question.type != "multiple_choice":
            return {"text": "❌ Question not found.", "edit": True}

        options = self.store.get_options(question.id)
        option_by_no = {option.option_no: option for option in options}
        selected = option_by_no.get(selected_option_no)
        correct = option_by_no.get(question.correct_option_no or 0)
        if not selected:
            return {"text": "❌ Invalid option.", "edit": True}

        is_correct = selected_option_no == question.correct_option_no
        feedback = question.explanation or ("정답입니다." if is_correct else "오답입니다.")
        attempt = self.store.add_attempt(
            chat_id=chat_id,
            question_id=question.id,
            answer_text=selected.text,
            selected_option_no=selected_option_no,
            is_correct=is_correct,
            score=question.points if is_correct else 0,
            feedback=feedback,
        )
        return self._render_result(
            question=question,
            attempt=attempt,
            correct_answer=f"{correct.option_no}. {correct.text}" if correct else "-",
            scope_token=scope_token,
        )

    def _handle_answer_prompt(self, chat_id: int, question_id: int, *, scope_token: str = SCOPE_ALL) -> dict:
        question = self.store.get_question(question_id, chat_id)
        if not question:
            return {"text": "❌ Question not found.", "edit": True}
        if question.type == "multiple_choice":
            return self._render_question(chat_id, question, scope_token=scope_token)

        if question.type == "subjective":
            placeholder = "답안을 작성하세요..."
        elif self._short_requires_ai(question):
            placeholder = "핵심 답안을 자연스럽게 적어도 됩니다..."
        else:
            placeholder = "정답을 입력하세요..."
        return {
            "text": f"✍️ <b>답 입력</b>\n\n{escape_html(question.prompt)}",
            "force_reply_prompt": "✍️ 답을 입력하세요:",
            "force_reply": ForceReply(selective=True, input_field_placeholder=placeholder),
            "interaction_action": "answer",
            "interaction_state": {"question_id": question.id, "scope_token": scope_token},
            "edit": False,
        }

    def _process_text_answer(
        self,
        chat_id: int,
        question_id: int,
        answer: str,
        *,
        scope_token: str = SCOPE_ALL,
    ) -> dict:
        question = self.store.get_question(question_id, chat_id)
        if not question:
            return {"text": "❌ Question not found."}

        answer = answer.strip()
        if not answer:
            return {
                "text": "❌ 답안이 비어 있습니다.",
                "reply_markup": InlineKeyboardMarkup([[
                    InlineKeyboardButton("✍️ 다시 입력", callback_data=f"qb:reply:{question.id}:{scope_token}"),
                ]]),
            }

        if question.type == "subjective":
            return self._process_subjective_answer(chat_id, question, answer, scope_token=scope_token)
        if question.type == "short" and self._short_requires_ai(question):
            return self._process_ai_graded_short_answer(chat_id, question, answer, scope_token=scope_token)

        accepted_answers = self._accepted_short_answers(question)
        expected = self._short_answer_display(question)
        if question.match_policy == "strict_raw":
            is_correct = answer in accepted_answers
        else:
            loose_text = question.match_policy == "loose_text"
            normalized_answer = self._normalize_short_answer(answer, loose=loose_text)
            is_correct = normalized_answer in {
                self._normalize_short_answer(candidate, loose=loose_text)
                for candidate in accepted_answers
            }
        feedback = question.explanation or (
            "정답입니다." if is_correct else "단답식은 정확히 일치해야 정답입니다."
        )
        attempt = self.store.add_attempt(
            chat_id=chat_id,
            question_id=question.id,
            answer_text=answer,
            is_correct=is_correct,
            score=question.points if is_correct else 0,
            feedback=feedback,
        )
        return self._render_result(
            question=question,
            attempt=attempt,
            correct_answer=expected,
            scope_token=scope_token,
        )

    def _process_subjective_answer(
        self,
        chat_id: int,
        question: QuestionBankQuestion,
        answer: str,
        *,
        scope_token: str,
    ) -> dict:
        attempt = self.store.add_attempt(
            chat_id=chat_id,
            question_id=question.id,
            answer_text=answer,
            ai_status="pending",
        )
        continue_button = self._continue_practice_button(scope_token)
        return {
            "text": (
                "🧠 <b>주관식 채점 요청됨</b>\n\n"
                "AI가 답안을 평가하고 결과를 DB에 기록합니다."
            ),
            "reply_markup": InlineKeyboardMarkup([
                [continue_button],
                [InlineKeyboardButton("⬅️ 메인", callback_data="qb:main")],
            ]),
            "dispatch_ai": True,
            "ai_session_name": "Question Bank Grading",
            "ai_message": self._build_subjective_grading_prompt(chat_id, question, attempt),
            "delivery_buttons": [[self._button_payload("➡️ 계속 문제 풀기", continue_button.callback_data)]],
            "post_completion_hook": self._build_attempt_completion_hook(attempt.id, scope_token),
        }

    def _process_ai_graded_short_answer(
        self,
        chat_id: int,
        question: QuestionBankQuestion,
        answer: str,
        *,
        scope_token: str,
    ) -> dict:
        attempt = self.store.add_attempt(
            chat_id=chat_id,
            question_id=question.id,
            answer_text=answer,
            ai_status="pending",
        )
        continue_button = self._continue_practice_button(scope_token)
        return {
            "text": (
                "🧠 <b>단답식 AI 채점 요청됨</b>\n\n"
                "이 문제는 동의어, 순서, 복수 항목 여부를 함께 봐야 해서 AI가 판정합니다."
            ),
            "reply_markup": InlineKeyboardMarkup([
                [continue_button],
                [InlineKeyboardButton("⬅️ 메인", callback_data="qb:main")],
            ]),
            "dispatch_ai": True,
            "ai_session_name": "Question Bank Short Grading",
            "ai_message": self._build_short_grading_prompt(chat_id, question, attempt),
            "delivery_buttons": [[self._button_payload("➡️ 계속 문제 풀기", continue_button.callback_data)]],
            "post_completion_hook": self._build_attempt_completion_hook(attempt.id, scope_token),
        }

    def _render_result(
        self,
        *,
        question: QuestionBankQuestion,
        attempt: QuestionBankAttempt,
        correct_answer: str,
        scope_token: str,
    ) -> dict:
        icon = "✅" if attempt.is_correct else "❌"
        title = "정답" if attempt.is_correct else "오답"
        score = f"{attempt.score:g}" if attempt.score is not None else "-"

        lines = [
            f"{icon} <b>{title}</b>",
            "",
            f"<b>문제 #{question.id}</b>",
            escape_html(question.prompt),
            "",
            f"내 답: <code>{escape_html(attempt.answer_text)}</code>",
            f"정답: <code>{escape_html(correct_answer)}</code>",
            f"점수: <b>{score}</b> / {question.points:g}",
        ]
        if attempt.feedback:
            lines.extend(["", f"<b>해설</b>\n{escape_html(attempt.feedback)}"])

        buttons = [
            [InlineKeyboardButton("✨ AI와 대화", callback_data=f"qb:ask:{attempt.id}")],
            [
                InlineKeyboardButton("🔁 다시 풀기", callback_data=f"qb:q:{question.id}:{scope_token}"),
                InlineKeyboardButton("➡️ 다음 문제", callback_data=f"qb:practice:{scope_token}"),
            ],
            [InlineKeyboardButton("⬅️ 메인", callback_data="qb:main")],
        ]
        return {"text": "\n".join(lines), "reply_markup": InlineKeyboardMarkup(buttons), "edit": True}

    def _handle_ai_question_prompt(self, chat_id: int, attempt_id: int) -> dict:
        attempt = self.store.get_attempt(attempt_id, chat_id)
        if not attempt:
            return {"text": "❌ Attempt not found.", "edit": True}

        question = self.store.get_question(attempt.question_id, chat_id)
        if not question:
            return {"text": "❌ Question not found.", "edit": True}

        return {
            "text": (
                "✨ <b>AI와 대화</b>\n\n"
                f"문제 #{question.id}와 내 답안을 기준으로 질문하세요."
            ),
            "force_reply_prompt": "✨ AI에게 물어볼 내용을 입력하세요:",
            "force_reply": ForceReply(
                selective=True,
                input_field_placeholder="왜 틀렸는지, 관련 개념 등을 질문하세요...",
            ),
            "interaction_action": "ask_ai",
            "interaction_state": {"attempt_id": attempt.id},
            "edit": False,
        }

    def _process_ai_question(self, chat_id: int, attempt_id: int, user_question: str) -> dict:
        attempt = self.store.get_attempt(attempt_id, chat_id)
        if not attempt:
            return {"text": "❌ Attempt not found."}
        question = self.store.get_question(attempt.question_id, chat_id)
        if not question:
            return {"text": "❌ Question not found."}

        return {
            "text": "✨ AI에게 질문을 전달합니다.",
            "dispatch_ai": True,
            "ai_session_name": "Question Bank Help",
            "ai_message": self._build_attempt_discussion_prompt(question, attempt, user_question),
        }

    def _handle_wrong(self, chat_id: int, *, bank_id: Optional[int] = None) -> dict:
        attempts = self.store.recent_wrong_attempts(chat_id, limit=10, bank_id=bank_id)
        scope_token = self._scope_token(bank_id=bank_id, wrong_only=True)
        base_label = self._scope_label(chat_id, self._scope_token(bank_id=bank_id))
        if not attempts:
            buttons = [[InlineKeyboardButton("▶️ 문제 풀기", callback_data=f"qb:practice:{self._scope_token(bank_id=bank_id)}")]]
            buttons.append([InlineKeyboardButton("⬅️ 메인", callback_data="qb:main")])
            return {
                "text": f"✅ <b>{escape_html(base_label)} 오답이 없습니다.</b>",
                "reply_markup": InlineKeyboardMarkup(buttons),
                "edit": True,
            }

        lines = [f"❌ <b>{escape_html(base_label)} 최근 오답</b>\n"]
        buttons = []
        for attempt in attempts:
            question = self.store.get_question(attempt.question_id, chat_id)
            if not question:
                continue
            preview = question.prompt[:40] + ("..." if len(question.prompt) > 40 else "")
            lines.append(f"#{question.id} {escape_html(preview)}")
            buttons.append([
                InlineKeyboardButton(f"🔁 #{question.id}", callback_data=f"qb:q:{question.id}:{scope_token}"),
                InlineKeyboardButton("✨ AI", callback_data=f"qb:ask:{attempt.id}"),
            ])

        buttons.append([
            InlineKeyboardButton("🎯 오답 랜덤", callback_data=f"qb:practice:{scope_token}"),
            InlineKeyboardButton("⬅️ 메인", callback_data="qb:main"),
        ])
        return {"text": "\n".join(lines), "reply_markup": InlineKeyboardMarkup(buttons), "edit": True}

    def _handle_stats(self, chat_id: int, *, bank_id: Optional[int] = None) -> dict:
        stats = self.store.stats(chat_id, bank_id=bank_id)
        accuracy = "-"
        if stats["attempts"] > 0:
            accuracy = f"{round(stats['correct'] / stats['attempts'] * 100)}%"

        if bank_id is None:
            title = "전체"
            back_button = InlineKeyboardButton("⬅️ 메인", callback_data="qb:main")
        else:
            bank = self.store.get_bank(bank_id, chat_id)
            if not bank:
                return {"text": "❌ Problem bank not found.", "edit": True}
            title = bank.title
            back_button = InlineKeyboardButton("⬅️ 문제집", callback_data=f"qb:bank:{bank_id}")

        text = (
            "📊 <b>Question Bank Stats</b>\n\n"
            f"범위: <b>{escape_html(title)}</b>\n"
            f"문제집: <b>{stats['banks']}</b>\n"
            f"문제: <b>{stats['questions']}</b>\n"
            f"풀이: <b>{stats['attempts']}</b>\n"
            f"정답: <b>{stats['correct']}</b>\n"
            f"오답: <b>{stats['wrong']}</b>\n"
            f"정답률: <b>{accuracy}</b>"
        )
        buttons = [[back_button]]
        return {"text": text, "reply_markup": InlineKeyboardMarkup(buttons), "edit": True}

    def _build_subjective_grading_prompt(
        self,
        chat_id: int,
        question: QuestionBankQuestion,
        attempt: QuestionBankAttempt,
    ) -> str:
        return f"""[Question Bank - Subjective Grading]
Evaluate the user's subjective answer and update the bot database through the `query_db` MCP tool.

Rules:
- Use the rubric and model answer below.
- Score from 0 to {question.points:g}.
- Mark correct if score >= {question.pass_score:g}.
- Update exactly this row: qb_attempts.id = {attempt.id}, chat_id = {chat_id}.
- Escape SQL string quotes by doubling single quotes.
- After updating DB, explain the result briefly in Korean.

SQL update shape:
UPDATE qb_attempts
SET is_correct = 1_or_0,
    score = numeric_score,
    feedback = 'short Korean feedback',
    ai_status = 'done',
    ai_model = 'provider-used-by-you',
    ai_raw_response = 'compact JSON or text summary',
    evaluated_at = datetime('now')
WHERE id = {attempt.id} AND chat_id = {{chat_id}};

Question:
{question.prompt}

Model answer:
{question.model_answer or "(none)"}

Rubric:
{question.grading_rubric or "Use semantic equivalence to the model answer."}

Pass score:
{question.pass_score:g}

User answer:
{attempt.answer_text}
"""

    def _build_short_grading_prompt(
        self,
        chat_id: int,
        question: QuestionBankQuestion,
        attempt: QuestionBankAttempt,
    ) -> str:
        accepted = "\n".join(f"- {answer}" for answer in self._accepted_short_answers(question))
        return f"""[Question Bank - Short Answer Grading]
This question is stored as short-answer, but it is not safe for strict exact matching.
Evaluate the user's answer against the canonical answer and accepted aliases, then update the bot database through `query_db`.

Rules:
- Accept Korean/English equivalent labels only when they are factually identical.
- If the question asks for multiple items or ordered steps, the user's answer must cover all required parts.
- Be strict on missing 핵심 facts, but ignore trivial punctuation/casing differences.
- Score from 0 to {question.points:g}.
- Mark correct if score >= {question.pass_score:g}.
- Update exactly this row: qb_attempts.id = {attempt.id}, chat_id = {chat_id}.
- Escape SQL string quotes by doubling single quotes.
- After updating DB, explain the result briefly in Korean.

SQL update shape:
UPDATE qb_attempts
SET is_correct = 1_or_0,
    score = numeric_score,
    feedback = 'short Korean feedback',
    ai_status = 'done',
    ai_model = 'provider-used-by-you',
    ai_raw_response = 'compact JSON or text summary',
    evaluated_at = datetime('now')
WHERE id = {attempt.id} AND chat_id = {{chat_id}};

Question:
{question.prompt}

Canonical answer / accepted aliases:
{accepted}

Stored explanation:
{question.explanation or "(none)"}

User answer:
{attempt.answer_text}
"""

    def _build_attempt_discussion_prompt(
        self,
        question: QuestionBankQuestion,
        attempt: QuestionBankAttempt,
        user_question: str,
    ) -> str:
        status = "correct" if attempt.is_correct else "wrong"
        return f"""[Question Bank - Explain Attempt]
Answer the user's follow-up question in Korean using the problem context.

Question type: {TYPE_LABELS.get(question.type, question.type)}
Question:
{question.prompt}

Expected answer:
{self._expected_answer_text(question)}

User answer:
{attempt.answer_text}

Result: {status}
Score: {attempt.score if attempt.score is not None else "-"} / {question.points:g}
Feedback:
{attempt.feedback or "(none)"}

User follow-up:
{user_question.strip()}
"""

    def _resolve_schedule_config(
        self,
        chat_id: int,
        schedule: Optional["Schedule"],
    ) -> Optional["QuestionBankScheduleConfig"]:
        if not schedule:
            return None
        return self.store.get_schedule_config(schedule.id, chat_id)

    def _scope_token_from_config(self, config: Optional["QuestionBankScheduleConfig"]) -> str:
        if not config:
            return SCOPE_ALL
        if config.scope_type == "wrong_all":
            return SCOPE_WRONG_ALL
        if config.scope_type == "bank" and config.bank_id:
            return self._bank_scope_token(config.bank_id)
        if config.scope_type == "wrong_bank" and config.bank_id:
            return self._scope_token(bank_id=config.bank_id, wrong_only=True)
        return SCOPE_ALL

    def _build_empty_state(
        self,
        chat_id: int,
        *,
        bank_id: Optional[int] = None,
        wrong_only: bool = False,
        edit: bool,
    ) -> dict:
        del chat_id
        if wrong_only:
            label = "오답 문제가 없습니다."
        elif bank_id is None:
            label = "아직 문제가 없습니다."
        else:
            label = "이 문제집에는 아직 문제가 없습니다."

        buttons = [[InlineKeyboardButton("✨ AI로 문제 만들기", callback_data="aiwork:question_bank")]]
        if bank_id is None:
            buttons.append([InlineKeyboardButton("⬅️ 메인", callback_data="qb:main")])
        else:
            buttons.append([InlineKeyboardButton("⬅️ 문제집", callback_data=f"qb:bank:{bank_id}")])

        return {
            "text": (
                f"📭 <b>{label}</b>\n\n"
                "아래 버튼으로 AI에게 문제 생성이나 스케줄 구성을 요청하세요."
            ),
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": edit,
        }

    def _build_choice_buttons(self, question_id: int, options, scope_token: str) -> list[list[InlineKeyboardButton]]:
        rows: list[list[InlineKeyboardButton]] = []
        current_row: list[InlineKeyboardButton] = []
        for option in options:
            current_row.append(
                InlineKeyboardButton(
                    str(option.option_no),
                    callback_data=f"qb:a:{question_id}:{option.option_no}:{scope_token}",
                )
            )
            if len(current_row) == 5:
                rows.append(current_row)
                current_row = []
        if current_row:
            rows.append(current_row)
        return rows

    def _continue_practice_button(self, scope_token: str) -> InlineKeyboardButton:
        return InlineKeyboardButton("➡️ 계속 문제 풀기", callback_data=f"qb:practice:{scope_token}")

    def _button_payload(self, text: str, callback_data: str) -> dict[str, str]:
        return {"text": text, "callback_data": callback_data}

    def _build_attempt_completion_hook(self, attempt_id: int, scope_token: str) -> dict[str, object]:
        return {
            "action": "render_attempt_result",
            "payload": {"attempt_id": attempt_id, "scope_token": scope_token},
        }

    def _build_ai_completion_result_text(
        self,
        question: QuestionBankQuestion,
        attempt: QuestionBankAttempt,
    ) -> str:
        icon = "✅" if attempt.is_correct else "❌"
        title = "정답" if attempt.is_correct else "오답"
        score = f"{attempt.score:g}" if attempt.score is not None else "-"
        lines = [
            f"{icon} <b>{title}</b>",
            "",
            f"<b>문제 #{question.id}</b>",
            escape_html(question.prompt),
            "",
            f"내 답: <code>{escape_html(attempt.answer_text)}</code>",
            f"정답: <code>{escape_html(self._expected_answer_text(question))}</code>",
            f"점수: <b>{score}</b> / {question.points:g}",
        ]
        if attempt.feedback:
            lines.extend(["", f"<b>해설</b>\n{escape_html(attempt.feedback)}"])
        return "\n".join(lines)

    def _build_ai_completion_buttons(
        self,
        question_id: int,
        attempt_id: int,
        scope_token: str,
    ) -> list[list[dict[str, str]]]:
        return [
            [self._button_payload("✨ AI와 대화", f"qb:ask:{attempt_id}")],
            [
                self._button_payload("🔁 다시 풀기", f"qb:q:{question_id}:{scope_token}"),
                self._button_payload("➡️ 계속 문제 풀기", f"qb:practice:{scope_token}"),
            ],
            [self._button_payload("⬅️ 메인", "qb:main")],
        ]

    def _accepted_short_answers(self, question: QuestionBankQuestion) -> list[str]:
        answer_text = question.answer_text or ""
        if "||" not in answer_text:
            return [answer_text.strip()] if answer_text.strip() else []
        return [part.strip() for part in answer_text.split("||") if part.strip()]

    def _short_answer_display(self, question: QuestionBankQuestion) -> str:
        answers = self._accepted_short_answers(question)
        return " / ".join(answers) if answers else ""

    def _normalize_short_answer(self, answer: str, *, loose: bool = False) -> str:
        normalized = " ".join(answer.strip().lower().split())
        normalized = normalized.strip(".,;:!?\"'")
        if loose:
            normalized = re.sub(r"[\s,\(\)\[\]\{\}'\"`“”‘’·;]+", "", normalized)
        return normalized

    def _short_requires_ai(self, question: QuestionBankQuestion) -> bool:
        if question.type != "short":
            return False

        answers = self._accepted_short_answers(question)
        if not answers:
            return True

        prompt = question.prompt or ""
        if any(pattern.search(prompt) for pattern in _SHORT_PROMPT_COMPLEX_PATTERNS):
            return True

        for answer in answers:
            if any(token in answer for token in [",", ":", "/", "(", ")", "→", "\n"]):
                return True
            if len(answer) > 32:
                return True
            if len(answer.split()) > 3:
                return True

        if len(answers) > 4:
            return True

        return False

    def _expected_answer_text(self, question: QuestionBankQuestion) -> str:
        if question.type == "multiple_choice":
            options = self.store.get_options(question.id)
            for option in options:
                if option.option_no == question.correct_option_no:
                    return f"{option.option_no}. {option.text}"
            return str(question.correct_option_no or "")
        if question.type == "subjective":
            return question.model_answer or "(model answer missing)"
        return self._short_answer_display(question)

    def _scope_token(self, *, bank_id: Optional[int] = None, wrong_only: bool = False) -> str:
        if bank_id is None:
            return SCOPE_WRONG_ALL if wrong_only else SCOPE_ALL
        return f"wb{bank_id}" if wrong_only else self._bank_scope_token(bank_id)

    def _bank_scope_token(self, bank_id: int) -> str:
        return f"b{bank_id}"

    def _decode_scope_token(self, scope_token: str) -> tuple[Optional[int], bool]:
        if not scope_token or scope_token == SCOPE_ALL:
            return None, False
        if scope_token == SCOPE_WRONG_ALL:
            return None, True
        if scope_token.startswith("wb"):
            return int(scope_token[2:]), True
        if scope_token.startswith("b"):
            return int(scope_token[1:]), False
        raise ValueError(f"Unknown scope token: {scope_token}")

    def _scope_label(self, chat_id: int, scope_token: str) -> str:
        bank_id, wrong_only = self._decode_scope_token(scope_token)
        if bank_id is None:
            base = "전체 문제"
        else:
            bank = self.store.get_bank(bank_id, chat_id)
            base = bank.title if bank else f"문제집 #{bank_id}"
        return f"{base} / 오답" if wrong_only else base
