# Question Bank

Plugin for AI-authored practice questions. There is no manual "add question" UI. When the user asks to create, import, revise, or delete questions, use MCP `query_db` to write directly to the tables below.

## Tables

```sql
qb_banks (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
)

qb_questions (
    id INTEGER PRIMARY KEY,
    bank_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    type TEXT NOT NULL,              -- 'short', 'multiple_choice', 'subjective'
    prompt TEXT NOT NULL,
    answer_text TEXT,                -- short-answer exact answer
    correct_option_no INTEGER,       -- multiple-choice correct number
    model_answer TEXT,               -- subjective model answer
    grading_rubric TEXT,             -- subjective grading rubric
    explanation TEXT NOT NULL DEFAULT '',
    points REAL NOT NULL DEFAULT 1,
    pass_score REAL NOT NULL DEFAULT 1,
    match_policy TEXT NOT NULL DEFAULT 'strict_trim',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT,
    updated_at TEXT
)

qb_options (
    id INTEGER PRIMARY KEY,
    question_id INTEGER NOT NULL,
    option_no INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (question_id, option_no)
)

qb_attempts (
    id INTEGER PRIMARY KEY,
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
    submitted_at TEXT,
    evaluated_at TEXT
)

qb_schedule_configs (
    schedule_id TEXT PRIMARY KEY,   -- references schedules.id
    chat_id INTEGER NOT NULL,
    scope_type TEXT NOT NULL,       -- 'all', 'bank', 'wrong_all', 'wrong_bank'
    bank_id INTEGER,
    question_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT,
    updated_at TEXT
)
```

## Creation Rules

- Always scope data with `chat_id = {chat_id}`.
- First ensure a bank exists. Prefer an existing non-archived bank unless the user requests a new named bank.
- Use `type = 'short'` only when the answer is one clear concept with a very small fixed alias set.
- Use `type = 'multiple_choice'` for numbered options, and `type = 'subjective'` for anything that needs semantic judgment.
- For short-answer questions, store accepted answers in `answer_text`. If there are fixed aliases, separate them with `||`, for example `서울 || Seoul`.
- Keep short-answer aliases small and explicit. Good short answers are things like one product name, one policy name, one code, one region name, one acronym expansion.
- If the answer needs ordered steps, multiple independent facts, ranges, examples, explanation, comparison, or “all that apply”, do NOT use `short`; use `subjective`.
- For short-answer questions, keep `match_policy = 'strict_trim'` unless the user explicitly requests raw exact matching.
- `match_policy = 'loose_text'` is allowed only when spaces, commas, and surrounding parentheses are not semantically meaningful. It still preserves technical symbols like `+`, `-`, `.`, `/`, `#`, `:`, `_`.
- For multiple-choice questions, set `correct_option_no` in `qb_questions` and insert numbered rows into `qb_options`.
- For subjective questions, set `model_answer`, `grading_rubric`, and a numeric `pass_score` such as `0.7`.
- Keep `explanation` concise and learner-facing.
- Do not insert attempts unless the user is submitting an answer or you are grading an existing pending subjective attempt.

## Useful SQL

Ensure default bank:

```sql
INSERT INTO qb_banks (chat_id, title, description)
SELECT {chat_id}, 'Default', 'AI-created questions'
WHERE NOT EXISTS (
    SELECT 1 FROM qb_banks WHERE chat_id = {chat_id} AND archived = 0
)
```

Get a bank id:

```sql
SELECT id FROM qb_banks
WHERE chat_id = {chat_id} AND archived = 0
ORDER BY id LIMIT 1
```

List banks first when the user mentions a specific bank:

```sql
SELECT id, title, description
FROM qb_banks
WHERE chat_id = {chat_id} AND archived = 0
ORDER BY id
```

Create a safe short-answer question:

```sql
INSERT INTO qb_questions
    (bank_id, chat_id, type, prompt, answer_text, explanation, points, pass_score)
VALUES
    (<bank_id>, {chat_id}, 'short', '대한민국의 수도는?', '서울 || Seoul', '대한민국의 수도는 서울입니다.', 1, 1)
```

Safe to use `loose_text`:

```sql
INSERT INTO qb_questions
    (bank_id, chat_id, type, prompt, answer_text, explanation, match_policy, points, pass_score)
VALUES
    (<bank_id>, {chat_id}, 'short', 'OAC의 풀네임은?', 'OAC || Origin Access Control',
     'OAC는 Origin Access Control의 약자입니다.', 'loose_text', 1, 1)
```

Unsafe for `short`, so use `subjective` instead:

- “TCP 3-way handshake의 이름과 각 플래그를 순서대로 쓰시오”
- “EC2의 5가지 요금 모델을 모두 쓰시오”
- “대칭키 알고리즘 1개와 비대칭키 알고리즘 1개를 쓰시오”
- “차이점을 비교하시오”

Create a multiple-choice question:

```sql
INSERT INTO qb_questions
    (bank_id, chat_id, type, prompt, correct_option_no, explanation, points, pass_score)
VALUES
    (<bank_id>, {chat_id}, 'multiple_choice', 'HTTP 성공 상태 코드는?', 2, 'HTTP 200은 요청 성공을 의미합니다.', 1, 1)
```

Then insert options using the new question id:

```sql
INSERT INTO qb_options (question_id, option_no, text)
VALUES (<question_id>, 1, '404')
```

Create a subjective question:

```sql
INSERT INTO qb_questions
    (bank_id, chat_id, type, prompt, model_answer, grading_rubric, explanation, points, pass_score)
VALUES
    (<bank_id>, {chat_id}, 'subjective',
     'REST API의 특징을 설명하세요.',
     '자원을 URI로 표현하고 HTTP 메서드로 행위를 나타내며 stateless 특성을 가진다.',
     'URI 자원 표현, HTTP 메서드, stateless 중 2개 이상을 정확히 설명하면 통과.',
     'REST의 핵심은 자원, HTTP 메서드, stateless입니다.',
     1, 0.7)
```

## Subjective Grading

When asked to grade a pending subjective attempt, update `qb_attempts` with:

- `is_correct`: `1` if `score >= pass_score`, else `0`
- `score`: numeric score
- `feedback`: concise Korean feedback
- `ai_status`: `'done'`
- `ai_model`: provider/model used by you
- `ai_raw_response`: compact summary or JSON-like text
- `evaluated_at`: `datetime('now')`

Use:

```sql
UPDATE qb_attempts
SET is_correct = 1,
    score = 0.8,
    feedback = '핵심 개념을 대부분 포함했지만 URI 설명이 부족합니다.',
    ai_status = 'done',
    ai_model = 'ai',
    ai_raw_response = '{"score":0.8,"pass":true}',
    evaluated_at = datetime('now')
WHERE id = <attempt_id> AND chat_id = {chat_id}
```

## Scheduled Practice

Question Bank schedules use the shared `schedules` table plus `qb_schedule_configs`.

1. Insert one row into `schedules` with:
   - `schedule_type = 'plugin'`
   - `plugin_name = 'question_bank'`
   - `action_name = 'scheduled_practice'`
   - `message = ''`
   - `name` describing the scope, e.g. `네트워크 문제집 랜덤 1문제`
   - reuse the current user's real `user_id`; if you do not know it yet, query an existing row in `schedules` or `users` first
2. Insert one row into `qb_schedule_configs` using the same `schedule_id`.
3. Call `reload_schedules()` after the DB writes.

Scope rules:

- `scope_type = 'all'`: all active questions for this chat
- `scope_type = 'bank'`: one bank only, requires `bank_id`
- `scope_type = 'wrong_all'`: only previously wrong questions across all banks
- `scope_type = 'wrong_bank'`: only previously wrong questions in one bank, requires `bank_id`
- Keep `question_count = 1` for MVP

Example schedule insert:

```sql
INSERT INTO schedules
    (id, user_id, chat_id, hour, minute, message, name, schedule_type, trigger_type,
     cron_expr, run_at_local, ai_provider, model, workspace_path, plugin_name, action_name,
     enabled, created_at, last_run, last_error, run_count)
VALUES
    ('<schedule_id>', '<user_id>', {chat_id}, 9, 0, '', '네트워크 문제집 랜덤 1문제',
     'plugin', 'cron', '0 9 * * *', NULL, 'claude', 'sonnet', NULL,
     'question_bank', 'scheduled_practice', 1, datetime('now'), NULL, NULL, 0)
```

Then:

```sql
INSERT INTO qb_schedule_configs
    (schedule_id, chat_id, scope_type, bank_id, question_count)
VALUES
    ('<schedule_id>', {chat_id}, 'bank', <bank_id>, 1)
```

## Constraints

- This is an MVP. Do not rely on quiz session tables, tags, or spaced repetition tables.
- `query_db` blocks multi-statement SQL, so execute one SQL statement at a time.
- `DROP` and `ALTER` are not allowed.
- Escape single quotes inside SQL strings by doubling them.
