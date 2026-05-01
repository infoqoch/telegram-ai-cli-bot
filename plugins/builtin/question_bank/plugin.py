"""Question bank plugin - AI-authored questions and lightweight practice."""

from __future__ import annotations

import re
from typing import Optional, cast

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.formatters import escape_html
from src.plugins.loader import (
    PLUGIN_SURFACE_CATALOG,
    PLUGIN_SURFACE_MAIN_MENU,
    Plugin,
    PluginInteraction,
    PluginMenuEntry,
    PluginResult,
)
from src.plugins.storage import QuestionBankStore
from src.repository.adapters.plugin_storage import RepositoryQuestionBankStore
from src.repository.repository import QuestionBankAttempt, QuestionBankQuestion


TYPE_LABELS = {
    "short": "단답식",
    "multiple_choice": "객관식",
    "subjective": "주관식",
}

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
        "• Practice short-answer, multiple-choice, and subjective questions\n"
        "• Ask AI about each graded result"
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
            if action == "practice":
                return self._handle_practice(chat_id)
            if action == "wrong":
                return self._handle_wrong(chat_id)
            if action == "wrong_practice":
                return self._handle_practice(chat_id, wrong_only=True)
            if action == "stats":
                return self._handle_stats(chat_id)
            if action == "q":
                return self._handle_question(chat_id, int(parts[2]))
            if action == "a":
                return self._handle_choice_answer(chat_id, int(parts[2]), int(parts[3]))
            if action == "reply":
                return self._handle_answer_prompt(chat_id, int(parts[2]))
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
        return self._process_text_answer(chat_id, question_id, message)

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
            "문제 생성/수정은 AI와 대화해서 처리합니다."
        )
        buttons = [
            [InlineKeyboardButton("▶️ 문제 풀기", callback_data="qb:practice")],
            [
                InlineKeyboardButton("❌ 오답 보기", callback_data="qb:wrong"),
                InlineKeyboardButton("📊 통계", callback_data="qb:stats"),
            ],
            [InlineKeyboardButton("✨ AI로 문제 만들기", callback_data="aiwork:question_bank")],
            [InlineKeyboardButton("🔄 Refresh", callback_data="qb:main")],
        ]
        return {"text": text, "reply_markup": InlineKeyboardMarkup(buttons), "edit": True}

    def _handle_practice(self, chat_id: int, *, wrong_only: bool = False) -> dict:
        question = self.store.pick_question(chat_id, wrong_only=wrong_only)
        if not question:
            label = "오답 문제가 없습니다." if wrong_only else "아직 문제가 없습니다."
            text = (
                f"📭 <b>{label}</b>\n\n"
                "아래 버튼으로 AI에게 문제 생성을 요청하세요."
            )
            buttons = [
                [InlineKeyboardButton("✨ AI로 문제 만들기", callback_data="aiwork:question_bank")],
                [InlineKeyboardButton("⬅️ 메인", callback_data="qb:main")],
            ]
            return {"text": text, "reply_markup": InlineKeyboardMarkup(buttons), "edit": True}
        return self._render_question(chat_id, question)

    def _handle_question(self, chat_id: int, question_id: int) -> dict:
        question = self.store.get_question(question_id, chat_id)
        if not question:
            return {"text": "❌ Question not found.", "edit": True}
        return self._render_question(chat_id, question)

    def _render_question(self, chat_id: int, question: QuestionBankQuestion) -> dict:
        type_label = TYPE_LABELS.get(question.type, question.type)
        if question.type == "short" and self._short_requires_ai(question):
            type_label = "단답식 (AI 채점)"
        lines = [
            f"📝 <b>문제 #{question.id}</b>",
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
                buttons.append([
                    InlineKeyboardButton(
                        str(option.option_no),
                        callback_data=f"qb:a:{question.id}:{option.option_no}",
                    )
                    for option in options[:5]
                ])
        else:
            if question.type == "short" and self._short_requires_ai(question):
                lines.extend(["", "답안의 의미를 기준으로 AI가 판정합니다."])
            buttons.append([InlineKeyboardButton("✍️ 답 입력", callback_data=f"qb:reply:{question.id}")])

        buttons.append([
            InlineKeyboardButton("⏭️ 다른 문제", callback_data="qb:practice"),
            InlineKeyboardButton("⬅️ 메인", callback_data="qb:main"),
        ])
        return {"text": "\n".join(lines), "reply_markup": InlineKeyboardMarkup(buttons), "edit": True}

    def _handle_choice_answer(self, chat_id: int, question_id: int, selected_option_no: int) -> dict:
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
            chat_id=chat_id,
            question=question,
            attempt=attempt,
            correct_answer=f"{correct.option_no}. {correct.text}" if correct else "-",
        )

    def _handle_answer_prompt(self, chat_id: int, question_id: int) -> dict:
        question = self.store.get_question(question_id, chat_id)
        if not question:
            return {"text": "❌ Question not found.", "edit": True}
        if question.type == "multiple_choice":
            return self._render_question(chat_id, question)

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
            "interaction_state": {"question_id": question.id},
            "edit": False,
        }

    def _process_text_answer(self, chat_id: int, question_id: int, answer: str) -> dict:
        question = self.store.get_question(question_id, chat_id)
        if not question:
            return {"text": "❌ Question not found."}

        answer = answer.strip()
        if not answer:
            return {
                "text": "❌ 답안이 비어 있습니다.",
                "reply_markup": InlineKeyboardMarkup([[
                    InlineKeyboardButton("✍️ 다시 입력", callback_data=f"qb:reply:{question.id}"),
                ]]),
            }

        if question.type == "subjective":
            return self._process_subjective_answer(chat_id, question, answer)
        if question.type == "short" and self._short_requires_ai(question):
            return self._process_ai_graded_short_answer(chat_id, question, answer)

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
            chat_id=chat_id,
            question=question,
            attempt=attempt,
            correct_answer=expected,
        )

    def _process_subjective_answer(
        self,
        chat_id: int,
        question: QuestionBankQuestion,
        answer: str,
    ) -> dict:
        attempt = self.store.add_attempt(
            chat_id=chat_id,
            question_id=question.id,
            answer_text=answer,
            ai_status="pending",
        )
        return {
            "text": (
                "🧠 <b>주관식 채점 요청됨</b>\n\n"
                "AI가 답안을 평가하고 결과를 DB에 기록합니다."
            ),
            "reply_markup": InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ 메인", callback_data="qb:main"),
            ]]),
            "dispatch_ai": True,
            "ai_session_name": "Question Bank Grading",
            "ai_message": self._build_subjective_grading_prompt(chat_id, question, attempt),
        }

    def _process_ai_graded_short_answer(
        self,
        chat_id: int,
        question: QuestionBankQuestion,
        answer: str,
    ) -> dict:
        attempt = self.store.add_attempt(
            chat_id=chat_id,
            question_id=question.id,
            answer_text=answer,
            ai_status="pending",
        )
        return {
            "text": (
                "🧠 <b>단답식 AI 채점 요청됨</b>\n\n"
                "이 문제는 동의어, 순서, 복수 항목 여부를 함께 봐야 해서 AI가 판정합니다."
            ),
            "reply_markup": InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ 메인", callback_data="qb:main"),
            ]]),
            "dispatch_ai": True,
            "ai_session_name": "Question Bank Short Grading",
            "ai_message": self._build_short_grading_prompt(chat_id, question, attempt),
        }

    def _render_result(
        self,
        *,
        chat_id: int,
        question: QuestionBankQuestion,
        attempt: QuestionBankAttempt,
        correct_answer: str,
    ) -> dict:
        del chat_id
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
                InlineKeyboardButton("🔁 다시 풀기", callback_data=f"qb:q:{question.id}"),
                InlineKeyboardButton("➡️ 다음 문제", callback_data="qb:practice"),
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

    def _handle_wrong(self, chat_id: int) -> dict:
        attempts = self.store.recent_wrong_attempts(chat_id, limit=10)
        if not attempts:
            return {
                "text": "✅ <b>오답이 없습니다.</b>",
                "reply_markup": InlineKeyboardMarkup([[
                    InlineKeyboardButton("▶️ 문제 풀기", callback_data="qb:practice"),
                    InlineKeyboardButton("⬅️ 메인", callback_data="qb:main"),
                ]]),
                "edit": True,
            }

        lines = ["❌ <b>최근 오답</b>\n"]
        buttons = []
        for attempt in attempts:
            question = self.store.get_question(attempt.question_id, chat_id)
            if not question:
                continue
            preview = question.prompt[:40] + ("..." if len(question.prompt) > 40 else "")
            lines.append(f"#{question.id} {escape_html(preview)}")
            buttons.append([
                InlineKeyboardButton(f"🔁 #{question.id}", callback_data=f"qb:q:{question.id}"),
                InlineKeyboardButton("✨ AI", callback_data=f"qb:ask:{attempt.id}"),
            ])

        buttons.append([
            InlineKeyboardButton("🎯 오답 랜덤", callback_data="qb:wrong_practice"),
            InlineKeyboardButton("⬅️ 메인", callback_data="qb:main"),
        ])
        return {"text": "\n".join(lines), "reply_markup": InlineKeyboardMarkup(buttons), "edit": True}

    def _handle_stats(self, chat_id: int) -> dict:
        stats = self.store.stats(chat_id)
        accuracy = "-"
        if stats["attempts"] > 0:
            accuracy = f"{round(stats['correct'] / stats['attempts'] * 100)}%"
        text = (
            "📊 <b>Question Bank Stats</b>\n\n"
            f"문제집: <b>{stats['banks']}</b>\n"
            f"문제: <b>{stats['questions']}</b>\n"
            f"풀이: <b>{stats['attempts']}</b>\n"
            f"정답: <b>{stats['correct']}</b>\n"
            f"오답: <b>{stats['wrong']}</b>\n"
            f"정답률: <b>{accuracy}</b>"
        )
        buttons = [[InlineKeyboardButton("⬅️ 메인", callback_data="qb:main")]]
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
