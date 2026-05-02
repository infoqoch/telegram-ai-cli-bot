"""Plugin-facing storage adapters backed by the repository."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from src.repository.repository import (
    Diary,
    Memo,
    QuestionBank,
    QuestionBankAttempt,
    QuestionBankOption,
    QuestionBankQuestion,
    QuestionBankScheduleConfig,
    Todo,
    WeatherLocation,
)

if TYPE_CHECKING:
    from src.repository.repository import Repository


def _now_utc() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def _require_conn(repo: "Repository") -> sqlite3.Connection:
    """Return the live SQLite connection from the repository."""
    conn = getattr(repo, "_conn", None)
    if conn is None:
        raise RuntimeError("Repository connection is unavailable for plugin storage")
    return conn


def _row_to_todo(row: sqlite3.Row) -> Todo:
    """Convert one SQLite row to the shared Todo dataclass."""
    return Todo(
        id=row["id"],
        chat_id=row["chat_id"],
        date=row["date"],
        slot=row["slot"],
        text=row["text"],
        done=bool(row["done"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class RepositoryPluginDatabase:
    """Small database adapter used by the plugin runtime."""

    def __init__(self, repo: "Repository"):
        self._repo = repo

    def executescript(self, schema: str) -> None:
        """Execute one plugin schema script."""
        if not schema:
            return

        conn = _require_conn(self._repo)
        conn.executescript(schema)
        conn.commit()


class RepositoryMemoStore:
    """Memo store adapter over the repository."""

    def __init__(self, repo: "Repository"):
        self._repo = repo

    def add(self, chat_id: int, content: str) -> Memo:
        now = _now_utc()
        conn = _require_conn(self._repo)
        cursor = conn.execute(
            "INSERT INTO memos (chat_id, content, created_at) VALUES (?, ?, ?)",
            (chat_id, content, now),
        )
        conn.commit()
        return Memo(
            id=cursor.lastrowid or 0,
            chat_id=chat_id,
            content=content,
            created_at=now,
        )

    def get(self, memo_id: int) -> Optional[Memo]:
        conn = _require_conn(self._repo)
        row = conn.execute("SELECT * FROM memos WHERE id = ?", (memo_id,)).fetchone()
        if not row:
            return None
        return Memo(
            id=row["id"],
            chat_id=row["chat_id"],
            content=row["content"],
            created_at=row["created_at"],
        )

    def delete(self, memo_id: int) -> bool:
        conn = _require_conn(self._repo)
        cursor = conn.execute("DELETE FROM memos WHERE id = ?", (memo_id,))
        conn.commit()
        return cursor.rowcount > 0

    def list_by_chat(self, chat_id: int) -> list[Memo]:
        conn = _require_conn(self._repo)
        rows = conn.execute(
            "SELECT * FROM memos WHERE chat_id = ? ORDER BY created_at DESC",
            (chat_id,),
        ).fetchall()
        return [
            Memo(
                id=row["id"],
                chat_id=row["chat_id"],
                content=row["content"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def clear_by_chat(self, chat_id: int) -> int:
        conn = _require_conn(self._repo)
        cursor = conn.execute("DELETE FROM memos WHERE chat_id = ?", (chat_id,))
        conn.commit()
        return cursor.rowcount


class RepositoryTodoStore:
    """Todo store adapter over the repository."""

    def __init__(self, repo: "Repository"):
        self._repo = repo

    def add(self, chat_id: int, date: str, text: str) -> Todo:
        now = _now_utc()
        conn = _require_conn(self._repo)
        cursor = conn.execute(
            """INSERT INTO todos (chat_id, date, slot, text, created_at, updated_at)
               VALUES (?, ?, 'default', ?, ?, ?)""",
            (chat_id, date, text, now, now),
        )
        conn.commit()
        return Todo(
            id=cursor.lastrowid or 0,
            chat_id=chat_id,
            date=date,
            slot="default",
            text=text,
            done=False,
            created_at=now,
            updated_at=now,
        )

    def get(self, todo_id: int) -> Optional[Todo]:
        conn = _require_conn(self._repo)
        row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        return _row_to_todo(row) if row else None

    def toggle(self, todo_id: int) -> Optional[bool]:
        todo = self.get(todo_id)
        if not todo:
            return None

        new_state = not todo.done
        conn = _require_conn(self._repo)
        conn.execute("UPDATE todos SET done = ? WHERE id = ?", (int(new_state), todo_id))
        conn.commit()
        return new_state

    def delete(self, todo_id: int) -> bool:
        conn = _require_conn(self._repo)
        cursor = conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        conn.commit()
        return cursor.rowcount > 0

    def list_by_date(self, chat_id: int, date: str) -> list[Todo]:
        conn = _require_conn(self._repo)
        rows = conn.execute(
            "SELECT * FROM todos WHERE chat_id = ? AND date = ? ORDER BY id",
            (chat_id, date),
        ).fetchall()
        return [_row_to_todo(row) for row in rows]

    def clear_by_date(self, chat_id: int, date: str) -> int:
        conn = _require_conn(self._repo)
        cursor = conn.execute(
            "DELETE FROM todos WHERE chat_id = ? AND date = ?",
            (chat_id, date),
        )
        conn.commit()
        return cursor.rowcount

    def mark_done(self, todo_id: int, done: bool = True) -> bool:
        conn = _require_conn(self._repo)
        cursor = conn.execute(
            "UPDATE todos SET done = ? WHERE id = ?",
            (int(done), todo_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def pending_for_date(self, chat_id: int, date: str) -> list[Todo]:
        conn = _require_conn(self._repo)
        rows = conn.execute(
            "SELECT * FROM todos WHERE chat_id = ? AND date = ? AND done = 0 ORDER BY id",
            (chat_id, date),
        ).fetchall()
        return [_row_to_todo(row) for row in rows]

    def move_to_date(self, todo_ids: list[int], new_date: str) -> int:
        if not todo_ids:
            return 0
        conn = _require_conn(self._repo)
        placeholders = ",".join("?" * len(todo_ids))
        cursor = conn.execute(
            f"UPDATE todos SET date = ? WHERE id IN ({placeholders})",
            [new_date] + todo_ids,
        )
        conn.commit()
        return cursor.rowcount

    def by_date_range(
        self,
        chat_id: int,
        start_date: str,
        end_date: str,
    ) -> dict[str, list[Todo]]:
        conn = _require_conn(self._repo)
        rows = conn.execute(
            """SELECT * FROM todos
               WHERE chat_id = ? AND date >= ? AND date <= ?
               ORDER BY date, id""",
            (chat_id, start_date, end_date),
        ).fetchall()
        result: dict[str, list[Todo]] = {}
        for row in rows:
            todo = _row_to_todo(row)
            result.setdefault(todo.date, []).append(todo)
        return result

    def stats_for_date(self, chat_id: int, date: str) -> dict[str, int]:
        conn = _require_conn(self._repo)
        row = conn.execute(
            """SELECT
                COUNT(*) as total,
                SUM(CASE WHEN done = 1 THEN 1 ELSE 0 END) as done,
                SUM(CASE WHEN done = 0 THEN 1 ELSE 0 END) as pending
               FROM todos WHERE chat_id = ? AND date = ?""",
            (chat_id, date),
        ).fetchone()
        return {
            "total": row["total"] or 0,
            "done": row["done"] or 0,
            "pending": row["pending"] or 0,
        }


def _row_to_diary(row: sqlite3.Row) -> Diary:
    """Convert one SQLite row to the shared Diary dataclass."""
    return Diary(
        id=row["id"],
        chat_id=row["chat_id"],
        date=row["date"],
        content=row["content"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class RepositoryDiaryStore:
    """Diary store adapter over the repository."""

    def __init__(self, repo: "Repository"):
        self._repo = repo

    def add(self, chat_id: int, date: str, content: str) -> Diary:
        now = _now_utc()
        conn = _require_conn(self._repo)
        cursor = conn.execute(
            "INSERT INTO diaries (chat_id, date, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, date, content, now, now),
        )
        conn.commit()
        return Diary(
            id=cursor.lastrowid or 0,
            chat_id=chat_id,
            date=date,
            content=content,
            created_at=now,
            updated_at=now,
        )

    def get(self, diary_id: int) -> Optional[Diary]:
        conn = _require_conn(self._repo)
        row = conn.execute("SELECT * FROM diaries WHERE id = ?", (diary_id,)).fetchone()
        return _row_to_diary(row) if row else None

    def get_by_date(self, chat_id: int, date: str) -> Optional[Diary]:
        conn = _require_conn(self._repo)
        row = conn.execute(
            "SELECT * FROM diaries WHERE chat_id = ? AND date = ?",
            (chat_id, date),
        ).fetchone()
        return _row_to_diary(row) if row else None

    def update(self, diary_id: int, content: str) -> bool:
        conn = _require_conn(self._repo)
        cursor = conn.execute(
            "UPDATE diaries SET content = ? WHERE id = ?",
            (content, diary_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def delete(self, diary_id: int) -> bool:
        conn = _require_conn(self._repo)
        cursor = conn.execute("DELETE FROM diaries WHERE id = ?", (diary_id,))
        conn.commit()
        return cursor.rowcount > 0

    def count_by_chat(self, chat_id: int) -> int:
        conn = _require_conn(self._repo)
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM diaries WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        return row["cnt"] if row else 0

    def list_by_month(self, chat_id: int, year: int, month: int) -> list[Diary]:
        start = f"{year:04d}-{month:02d}-01"
        if month == 12:
            end = f"{year + 1:04d}-01-01"
        else:
            end = f"{year:04d}-{month + 1:02d}-01"
        conn = _require_conn(self._repo)
        rows = conn.execute(
            "SELECT * FROM diaries WHERE chat_id = ? AND date >= ? AND date < ? ORDER BY date DESC",
            (chat_id, start, end),
        ).fetchall()
        return [_row_to_diary(row) for row in rows]


class RepositoryWeatherLocationStore:
    """Weather location store adapter over the repository."""

    def __init__(self, repo: "Repository"):
        self._repo = repo

    def set(
        self,
        chat_id: int,
        name: str,
        lat: float,
        lon: float,
        country: Optional[str] = None,
    ) -> WeatherLocation:
        conn = _require_conn(self._repo)
        conn.execute(
            """INSERT OR REPLACE INTO weather_locations (chat_id, name, country, lat, lon, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (chat_id, name, country, lat, lon, _now_utc()),
        )
        conn.commit()
        return WeatherLocation(
            chat_id=chat_id,
            name=name,
            country=country,
            lat=lat,
            lon=lon,
        )

    def get(self, chat_id: int) -> Optional[WeatherLocation]:
        conn = _require_conn(self._repo)
        row = conn.execute(
            "SELECT * FROM weather_locations WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if not row:
            return None
        return WeatherLocation(
            chat_id=row["chat_id"],
            name=row["name"],
            country=row["country"],
            lat=row["lat"],
            lon=row["lon"],
        )

    def delete(self, chat_id: int) -> bool:
        conn = _require_conn(self._repo)
        cursor = conn.execute(
            "DELETE FROM weather_locations WHERE chat_id = ?",
            (chat_id,),
        )
        conn.commit()
        return cursor.rowcount > 0


def _row_to_question_bank(row: sqlite3.Row) -> QuestionBank:
    """Convert one SQLite row to the shared QuestionBank dataclass."""
    return QuestionBank(
        id=row["id"],
        chat_id=row["chat_id"],
        title=row["title"],
        description=row["description"],
        archived=bool(row["archived"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_question(row: sqlite3.Row) -> QuestionBankQuestion:
    """Convert one SQLite row to the shared QuestionBankQuestion dataclass."""
    return QuestionBankQuestion(
        id=row["id"],
        bank_id=row["bank_id"],
        chat_id=row["chat_id"],
        type=row["type"],
        prompt=row["prompt"],
        answer_text=row["answer_text"],
        correct_option_no=row["correct_option_no"],
        model_answer=row["model_answer"],
        grading_rubric=row["grading_rubric"],
        explanation=row["explanation"] or "",
        points=float(row["points"]),
        pass_score=float(row["pass_score"]),
        match_policy=row["match_policy"],
        active=bool(row["active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_question_option(row: sqlite3.Row) -> QuestionBankOption:
    """Convert one SQLite row to the shared QuestionBankOption dataclass."""
    return QuestionBankOption(
        id=row["id"],
        question_id=row["question_id"],
        option_no=row["option_no"],
        text=row["text"],
    )


def _row_to_question_attempt(row: sqlite3.Row) -> QuestionBankAttempt:
    """Convert one SQLite row to the shared QuestionBankAttempt dataclass."""
    is_correct = row["is_correct"]
    return QuestionBankAttempt(
        id=row["id"],
        chat_id=row["chat_id"],
        question_id=row["question_id"],
        answer_text=row["answer_text"],
        selected_option_no=row["selected_option_no"],
        is_correct=bool(is_correct) if is_correct is not None else None,
        score=float(row["score"]) if row["score"] is not None else None,
        feedback=row["feedback"] or "",
        ai_status=row["ai_status"],
        ai_model=row["ai_model"],
        ai_raw_response=row["ai_raw_response"],
        submitted_at=row["submitted_at"],
        evaluated_at=row["evaluated_at"],
    )


def _row_to_question_bank_schedule_config(row: sqlite3.Row) -> QuestionBankScheduleConfig:
    """Convert one SQLite row to the shared QuestionBankScheduleConfig dataclass."""
    return QuestionBankScheduleConfig(
        schedule_id=row["schedule_id"],
        chat_id=row["chat_id"],
        scope_type=row["scope_type"],
        bank_id=row["bank_id"],
        question_count=row["question_count"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class RepositoryQuestionBankStore:
    """Question bank store adapter over the repository."""

    def __init__(self, repo: "Repository"):
        self._repo = repo

    def ensure_default_bank(self, chat_id: int) -> QuestionBank:
        conn = _require_conn(self._repo)
        row = conn.execute(
            """SELECT * FROM qb_banks
               WHERE chat_id = ? AND archived = 0
               ORDER BY id LIMIT 1""",
            (chat_id,),
        ).fetchone()
        if row:
            return _row_to_question_bank(row)

        now = _now_utc()
        cursor = conn.execute(
            """INSERT INTO qb_banks
               (chat_id, title, description, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (chat_id, "Default", "AI-created questions", now, now),
        )
        conn.commit()
        return QuestionBank(
            id=cursor.lastrowid or 0,
            chat_id=chat_id,
            title="Default",
            description="AI-created questions",
            archived=False,
            created_at=now,
            updated_at=now,
        )

    def list_banks(self, chat_id: int) -> list[QuestionBank]:
        self.ensure_default_bank(chat_id)
        conn = _require_conn(self._repo)
        rows = conn.execute(
            """SELECT * FROM qb_banks
               WHERE chat_id = ? AND archived = 0
               ORDER BY id""",
            (chat_id,),
        ).fetchall()
        return [_row_to_question_bank(row) for row in rows]

    def get_bank(self, bank_id: int, chat_id: int) -> Optional[QuestionBank]:
        conn = _require_conn(self._repo)
        row = conn.execute(
            """SELECT * FROM qb_banks
               WHERE id = ? AND chat_id = ? AND archived = 0""",
            (bank_id, chat_id),
        ).fetchone()
        return _row_to_question_bank(row) if row else None

    def stats(self, chat_id: int, bank_id: Optional[int] = None) -> dict[str, int]:
        conn = _require_conn(self._repo)
        if bank_id is None:
            bank_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM qb_banks WHERE chat_id = ? AND archived = 0",
                (chat_id,),
            ).fetchone()
            question_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM qb_questions WHERE chat_id = ? AND active = 1",
                (chat_id,),
            ).fetchone()
            attempt_row = conn.execute(
                """SELECT
                       COUNT(*) AS total,
                       SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS correct,
                       SUM(CASE WHEN is_correct = 0 THEN 1 ELSE 0 END) AS wrong
                   FROM qb_attempts
                   WHERE chat_id = ?""",
                (chat_id,),
            ).fetchone()
            bank_count = bank_row["cnt"] or 0
        else:
            bank_row = self.get_bank(bank_id, chat_id)
            if bank_row is None:
                return {"banks": 0, "questions": 0, "attempts": 0, "correct": 0, "wrong": 0}
            question_row = conn.execute(
                """SELECT COUNT(*) AS cnt
                   FROM qb_questions
                   WHERE chat_id = ? AND bank_id = ? AND active = 1""",
                (chat_id, bank_id),
            ).fetchone()
            attempt_row = conn.execute(
                """SELECT
                       COUNT(*) AS total,
                       SUM(CASE WHEN a.is_correct = 1 THEN 1 ELSE 0 END) AS correct,
                       SUM(CASE WHEN a.is_correct = 0 THEN 1 ELSE 0 END) AS wrong
                   FROM qb_attempts a
                   JOIN qb_questions q ON q.id = a.question_id
                   WHERE a.chat_id = ? AND q.bank_id = ?""",
                (chat_id, bank_id),
            ).fetchone()
            bank_count = 1
        return {
            "banks": bank_count,
            "questions": question_row["cnt"] or 0,
            "attempts": attempt_row["total"] or 0,
            "correct": attempt_row["correct"] or 0,
            "wrong": attempt_row["wrong"] or 0,
        }

    def get_question(self, question_id: int, chat_id: int) -> Optional[QuestionBankQuestion]:
        conn = _require_conn(self._repo)
        row = conn.execute(
            "SELECT * FROM qb_questions WHERE id = ? AND chat_id = ? AND active = 1",
            (question_id, chat_id),
        ).fetchone()
        return _row_to_question(row) if row else None

    def get_options(self, question_id: int) -> list[QuestionBankOption]:
        conn = _require_conn(self._repo)
        rows = conn.execute(
            "SELECT * FROM qb_options WHERE question_id = ? ORDER BY option_no",
            (question_id,),
        ).fetchall()
        return [_row_to_question_option(row) for row in rows]

    def pick_question(
        self,
        chat_id: int,
        *,
        bank_id: Optional[int] = None,
        wrong_only: bool = False,
    ) -> Optional[QuestionBankQuestion]:
        self.ensure_default_bank(chat_id)
        conn = _require_conn(self._repo)
        if wrong_only:
            if bank_id is None:
                row = conn.execute(
                    """SELECT q.*
                       FROM qb_questions q
                       WHERE q.chat_id = ?
                         AND q.active = 1
                         AND EXISTS (
                             SELECT 1
                             FROM qb_attempts a
                             WHERE a.question_id = q.id
                               AND a.chat_id = q.chat_id
                               AND a.is_correct = 0
                         )
                       ORDER BY RANDOM()
                       LIMIT 1""",
                    (chat_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT q.*
                       FROM qb_questions q
                       WHERE q.chat_id = ?
                         AND q.bank_id = ?
                         AND q.active = 1
                         AND EXISTS (
                             SELECT 1
                             FROM qb_attempts a
                             WHERE a.question_id = q.id
                               AND a.chat_id = q.chat_id
                               AND a.is_correct = 0
                         )
                       ORDER BY RANDOM()
                       LIMIT 1""",
                    (chat_id, bank_id),
                ).fetchone()
        else:
            if bank_id is None:
                row = conn.execute(
                    """SELECT * FROM qb_questions
                       WHERE chat_id = ? AND active = 1
                       ORDER BY RANDOM()
                       LIMIT 1""",
                    (chat_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT * FROM qb_questions
                       WHERE chat_id = ? AND bank_id = ? AND active = 1
                       ORDER BY RANDOM()
                       LIMIT 1""",
                    (chat_id, bank_id),
                ).fetchone()
        return _row_to_question(row) if row else None

    def add_attempt(
        self,
        *,
        chat_id: int,
        question_id: int,
        answer_text: str,
        selected_option_no: Optional[int] = None,
        is_correct: Optional[bool] = None,
        score: Optional[float] = None,
        feedback: str = "",
        ai_status: str = "not_needed",
        ai_model: Optional[str] = None,
        ai_raw_response: Optional[str] = None,
    ) -> QuestionBankAttempt:
        now = _now_utc()
        conn = _require_conn(self._repo)
        cursor = conn.execute(
            """INSERT INTO qb_attempts
               (chat_id, question_id, answer_text, selected_option_no, is_correct,
                score, feedback, ai_status, ai_model, ai_raw_response,
                submitted_at, evaluated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chat_id,
                question_id,
                answer_text,
                selected_option_no,
                int(is_correct) if is_correct is not None else None,
                score,
                feedback,
                ai_status,
                ai_model,
                ai_raw_response,
                now,
                now if is_correct is not None else None,
            ),
        )
        conn.commit()
        attempt = self.get_attempt(cursor.lastrowid or 0, chat_id)
        if attempt:
            return attempt
        return QuestionBankAttempt(
            id=cursor.lastrowid or 0,
            chat_id=chat_id,
            question_id=question_id,
            answer_text=answer_text,
            selected_option_no=selected_option_no,
            is_correct=is_correct,
            score=score,
            feedback=feedback,
            ai_status=ai_status,
            ai_model=ai_model,
            ai_raw_response=ai_raw_response,
            submitted_at=now,
            evaluated_at=now if is_correct is not None else None,
        )

    def get_attempt(self, attempt_id: int, chat_id: int) -> Optional[QuestionBankAttempt]:
        conn = _require_conn(self._repo)
        row = conn.execute(
            "SELECT * FROM qb_attempts WHERE id = ? AND chat_id = ?",
            (attempt_id, chat_id),
        ).fetchone()
        return _row_to_question_attempt(row) if row else None

    def recent_wrong_attempts(
        self,
        chat_id: int,
        limit: int = 10,
        bank_id: Optional[int] = None,
    ) -> list[QuestionBankAttempt]:
        conn = _require_conn(self._repo)
        if bank_id is None:
            rows = conn.execute(
                """SELECT * FROM qb_attempts
                   WHERE chat_id = ? AND is_correct = 0
                   ORDER BY submitted_at DESC, id DESC
                   LIMIT ?""",
                (chat_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT a.*
                   FROM qb_attempts a
                   JOIN qb_questions q ON q.id = a.question_id
                   WHERE a.chat_id = ? AND a.is_correct = 0 AND q.bank_id = ?
                   ORDER BY a.submitted_at DESC, a.id DESC
                   LIMIT ?""",
                (chat_id, bank_id, limit),
            ).fetchall()
        return [_row_to_question_attempt(row) for row in rows]

    def save_schedule_config(
        self,
        *,
        schedule_id: str,
        chat_id: int,
        scope_type: str,
        bank_id: Optional[int] = None,
        question_count: int = 1,
    ) -> QuestionBankScheduleConfig:
        now = _now_utc()
        conn = _require_conn(self._repo)
        conn.execute(
            """INSERT INTO qb_schedule_configs
               (schedule_id, chat_id, scope_type, bank_id, question_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(schedule_id) DO UPDATE SET
                   chat_id = excluded.chat_id,
                   scope_type = excluded.scope_type,
                   bank_id = excluded.bank_id,
                   question_count = excluded.question_count,
                   updated_at = excluded.updated_at""",
            (schedule_id, chat_id, scope_type, bank_id, question_count, now, now),
        )
        conn.commit()
        config = self.get_schedule_config(schedule_id, chat_id)
        if config:
            return config
        return QuestionBankScheduleConfig(
            schedule_id=schedule_id,
            chat_id=chat_id,
            scope_type=scope_type,
            bank_id=bank_id,
            question_count=question_count,
            created_at=now,
            updated_at=now,
        )

    def get_schedule_config(self, schedule_id: str, chat_id: int) -> Optional[QuestionBankScheduleConfig]:
        conn = _require_conn(self._repo)
        row = conn.execute(
            """SELECT * FROM qb_schedule_configs
               WHERE schedule_id = ? AND chat_id = ?""",
            (schedule_id, chat_id),
        ).fetchone()
        return _row_to_question_bank_schedule_config(row) if row else None
