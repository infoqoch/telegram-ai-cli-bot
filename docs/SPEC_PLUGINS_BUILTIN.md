# Built-in Plugin Specifications

> UI/UX specifications for built-in plugins: Todo, Calendar, Weather, Diary, Memo
> For bot core specifications, see [SPEC.md](SPEC.md)

---

## Todo Plugin

### Concept

Daily task management. Centered on today's todos: mark complete, delete, or move to tomorrow. Provides a weekly view and carry-over of yesterday's incomplete items.

### Triggers

- Natural language: `todo`, `할일`, `투두` (exact match)
- Excluded: question patterns ("what is todo", "how", "why", etc.) → passed to AI

### Daily Flow

```
09:00  [Automatic] Yesterday Report
       → Display yesterday's incomplete items + carry-over button

Daytime [Manual] Add / complete / delete todos
       → Type "todo" → today's list + action buttons

21:00  [Automatic] Daily Wrap-up
       → Today's progress + incomplete items → prompt to move to tomorrow
```

### Today's List Screen

```
📋 Todos for 2026-03-07

⬜ 1. Buy groceries
✅ 2. Read chapter 5
⬜ 3. Call dentist

📊 1/3 completed

[1. Buy groceries]  [3. Call dentist]  ← Incomplete items only (20-char truncate)
[📋 Multi-select]
[◀️ Prev] [📅 Week] [Next ▶️]
[✨ Work with AI]
[➕ Add] [🔄 Refresh]
```

- Empty list: `No todos yet.` + `[➕ Add]` button

### Item Detail → Actions

Tap an item button:
```
📌 Todo

Buy groceries

[✅ Done] [🗑️ Delete]
[📅 Tomorrow]
[⬅️ Back]
```

Feedback after each action + list refresh:
- Done: `✅ Marked as done!`
- Delete: `🗑️ Deleted!`
- Tomorrow: `📅 Moved to tomorrow!`

### Add (ForceReply)

`[➕ Add]` → ForceReply prompt → user input (multiple items separated by line breaks) → result:
```
✅ 3 added!

• Buy milk
• Clean house
• Fix bug

[📄 View list] [➕ Add more]
```

### Multi-select Mode

```
📋 Multi-select

Tap items to select/deselect.

⬜ Buy groceries
☑️ Call dentist

📌 1 selected

[⬜ Buy groceries]  [☑️ Call dentist]    ← 18-char truncate
[✅ Done(1)] [🗑️ Delete(1)] [📅 Tomorrow(1)]   ← Shown only when items selected
[🔄 Deselect all] [⬅️ Back]
```

### Date Navigation

- `[◀️ Prev]` / `[Next ▶️]`: Move one day, show that date's todos
- Non-today dates: header shows `MM/DD (Label)`
- `[📅 Today]` button to return to today

### Weekly View

Shows 3 days before and after the center date (7 days total). Center-date based, not fixed Mon–Sun.

```
📅 Weekly Todos (03/04 ~ 03/10)

👉 03/04(Mon): ✅ 3/3        ← All complete
03/05(Tue): ⬜ 1/4           ← Has incomplete
03/06(Wed): —                ← No todos
📍03/07(Thu): ⬜ 2/5         ← Today indicator

[4(Mon)] [5(Tue)] [6(Wed)] [📍7(Thu)]  ← Tap date → that day's list
[8(Fri)] [9(Sat)] [10(Sun)]
[◀️ Prev week] [📅 Today] [Next week ▶️]
```

### Yesterday's Incomplete Carry-over

Via Yesterday Report or `[📅 Carry selected]` button:

```
📋 Incomplete from 2026-03-06

Select items to carry over to today.

⬜ Unfinished task A
☑️ Unfinished task B

📌 1 selected

[⬜ task A] [☑️ task B]             ← 18-char truncate
[📅 Carry selected(1)] [📅 Carry all(2)]
[📄 Today]
```

- Carry selected or carry all
- Empty state: `✅ No incomplete items from yesterday!`

### Schedule Actions

| Action | Recommended Time | Behavior |
|--------|-----------------|----------|
| `yesterday_report` | 09:00 | Show yesterday's incomplete items + carry-over button. Skipped if no todos yesterday |
| `daily_wrap` | 21:00 | Today's progress + incomplete list. Celebration message if all complete. Skipped if no todos |

---

## Calendar Plugin

### Concept

Google Calendar integration. View events by day, navigate dates, add/edit/delete events through a step-by-step UI. Supports scheduled briefings and reminders. Requires `GOOGLE_SERVICE_ACCOUNT_FILE` and `GOOGLE_CALENDAR_ID` environment variables.

### Triggers

- Natural language: `calendar`, `cal`, `캘린더`, `일정`, `달력` (exact match, or keyword followed by a subcommand word)
- Excluded: question patterns ("what is calendar", etc.) → passed to AI

### Hub Screen (Day View)

The main screen showing a single day's events. Opens on today by default.

```
📅 Today — Mon, Mar 7

────────────────────
🌅 All day  Team holiday
⏰ 09:00  Weekly standup
⏰ 14:00  Dentist

[🌅 All day · Team holiday]
[09:00 · Weekly standup]
[14:00 · Dentist]
[◀️ Mar 6] [Today] [Mar 8 ▶️]
[📅 Calendar grid]
[➕ Add event]
[✨ Work with AI]
```

- No events: `No events ☀️`
- Tapping an event button → event detail screen

### Date Navigation

- `[◀️]` / `[▶️]` buttons: navigate one day at a time
- `[📅 Calendar grid]`: opens a month-grid for direct date picking
- `[Today]`: returns to today's hub

### Calendar Grid (Date Picker)

```
📅 2026/03

[Mon] [Tue] [Wed] [Thu] [Fri] [Sat] [Sun]
[ 2 ] [ 3 ] [ 4 ] [ 5 ] [ 6 ] [ 7 ] [ 8 ]
[ 9 ] [10 ] [11 ] [12 ] [13 ] [14 ] [15 ]
...
[•22•]  ← Today marker

[◀️ Feb] [Apr ▶️]
```

Tapping a day → hub for that date.

### Event Detail Screen

```
📌 Weekly standup

⏰ 09:00 - 09:30
📅 Monday, March 7, 2026
📍 Conference room B
📝 Bring Q1 report

[✏️ Edit] [🗑 Delete]
[◀ Back]
```

### Add Event Flow

Step-by-step date/time picker → ForceReply for title.

**Step 1 — Date select:**
```
📅 Select date

[Today Mar 7] [Tomorrow Mar 8] [Day after Mar 9]
[📅 More dates...]
```

**Step 2 — Hour select:**
```
⏰ Select start hour

📅 Monday, March 7, 2026

[00h] [01h] [02h] [03h]
[04h] [05h] [06h] [07h]
...
[🌅 All day]
[❌ Cancel]
```

**Step 3 — Minute select:**
```
⏰ 09h — select minute

📅 Monday, March 7, 2026

[:00] [:05] [:10] [:15]
[:20] [:25] [:30] [:35]
...
[❌ Cancel]
```

**Step 4 — Title (ForceReply):**
```
📝 Add Event

📅 Monday, March 7, 2026
⏰ 09:00

Enter the title.
```
ForceReply placeholder: `e.g., Team meeting, Dentist...`

**Result:**
```
✅ Event created!

📅 Monday, March 7, 2026
⏰ 09:00
📌 Weekly standup

[📅 Calendar]
```

### All-day Events

In Step 2 (hour select), tap `[🌅 All day]` → skip minute step → ForceReply for title directly.

Result shows `🌅 All day` instead of a time.

### Edit Event

Tap `[✏️ Edit]` from event detail → edit submenu:

```
✏️ What to edit?

📌 Weekly standup

[📅 Date/Time] [📌 Title]
[◀ Back]
```

- **Edit Title**: ForceReply for new title → `✅ Title updated!`
- **Edit Date/Time**: Same date → hour → minute picker flow as Add, then applies the new time

### Delete Event (with Confirmation)

Tap `[🗑 Delete]` from event detail → confirmation screen:

```
⚠️ Delete this event?

📌 Weekly standup
📅 Mar 7
⏰ 09:00 - 09:30

[✅ Delete] [❌ Cancel]
```

After deletion: `🗑 Event deleted.` + `[📅 Calendar]` button.

### Schedule Actions

| Action | Recommended Time | Behavior |
|--------|-----------------|----------|
| `morning_briefing` | ⭐ 08:00 daily | Today's events list. If no events: "No events ☀️" |
| `evening_summary` | ⭐ 22:00 daily | Tomorrow's events list (for next-day planning) |
| `reminder_10m` | Interval (every 5 min) | Alerts for events starting within 10 minutes. Silent if none |
| `reminder_1h` | Interval (every 5 min) | Alerts for events starting within 1 hour. Silent if none |

Reminder actions are **interval-based** (run every N minutes). Each event is reminded only once per trigger type (dedup via in-memory set). Returns `None` when there is nothing to report (intentional silence — no message sent).

Morning and evening briefings show an `[📅 Open Calendar]` button for quick access.

### MCP Tools

When configured, the Calendar plugin exposes two MCP tools that Claude can call during AI conversations:

| Tool | Description | Parameters |
|------|-------------|------------|
| `calendar_list_events` | Query events by date range | `start_date` (YYYY-MM-DD), `end_date` (YYYY-MM-DD) |
| `calendar_create_event` | Create a new event | `summary` (title), `start` (YYYY-MM-DDTHH:MM), `all_day` (bool, optional) |

Tools are only registered when the Google Calendar client is available (credentials configured). Claude can invoke these tools to read or write calendar data within an AI conversation.

### Configuration

| Env Variable | Description |
|--------------|-------------|
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Path to Google service account JSON file |
| `GOOGLE_CALENDAR_ID` | Calendar ID (e.g., `user@gmail.com` or `primary`) |

If not configured, the plugin responds with a setup instruction instead of showing the calendar.

---

## Weather Plugin

### Concept

Current weather + 3-day forecast by city. Saved location for quick access. Uses the Open-Meteo API (free, no API key required). City data loaded from a bundled CSV file.

### Triggers

- Natural language: `weather`, `날씨`, `기온` (exact match)
- Excluded: question patterns → passed to AI

### Weather Query Scenarios

**Saved location exists:** `weather` → show weather for saved location immediately.

**No saved location:** `weather` → region/province select screen.

**City from select:** Tap a city button → show that city's weather.

### Region Select Screen (No Saved Location)

```
🌤️ Select Region

Choose a region to check weather:

[서울] [부산] [대구]
[경기] [인천] [강원]
[충북] [충남] [대전]
...
```

Metro/special cities (서울, 부산, 대구, 인천, 광주, 대전, 울산, 세종) go directly to weather. Other regions show a city list.

### Province → City List Screen

```
🌤️ 경기 - Select City

Choose a city:

[수원] [성남] [의정부]
[안양] [부천] [광명]
...
[◀️ Back]
```

### Weather Display Screen

```
☀️ 서울 Weather

Current
• Weather: Clear
• Temp: 15.2°C
• Humidity: 45%
• Wind: 8.3 km/h

3-Day Forecast
03/07 ☀️ 5° / 16°
03/08 ⛅ 7° / 14°
03/09 🌧️ 3° / 10°

[🔄 Refresh] [📍 Other city]
[✨ Work with AI]
```

### Weather Icon Mapping

| Code | Icon | Description |
|------|------|-------------|
| 0 | ☀️ | Clear |
| 1 | 🌤️ | Mostly clear |
| 2 | ⛅ | Partly cloudy |
| 3 | ☁️ | Overcast |
| 45, 48 | 🌫️ | Fog / Rime fog |
| 51, 53, 55 | 🌧️ | Drizzle |
| 61, 63 | 🌧️ | Rain |
| 65 | 🌧️ | Heavy rain |
| 71, 73 | 🌨️ | Snow |
| 75 | ❄️ | Heavy snow |
| 80, 81 | 🌦️ | Showers |
| 82 | ⛈️ | Heavy showers |
| 95, 96, 99 | ⛈️ | Thunderstorm |

---

## Diary Plugin

### Concept

One diary entry per day. Browse past entries by month. Write, edit, or delete entries through a button-driven UI. Supports a daily reminder schedule action.

### Triggers

- Natural language: `diary`, `일기` (exact match, or keyword followed by a subcommand word)
- Excluded: question patterns ("what is a diary", etc.) → passed to AI

### Main Screen (Monthly List)

```
📓 Diary List (2026/03)

[3/1 (Mon) Had a great morning walk...]
[3/5 (Fri) Finished the project report...]
[3/7 (Sun) Relaxed at home and read...]

[◀️ 2] [3 ▶️]
📊 This month: 3 · Total: 12
[✨ Work with AI]
[📝 Write] [⏪ Yesterday]
```

- No entries at all: `📭 No diary entries yet.` + `[📝 Write]` + `[⏪ Yesterday]`
- Month navigation: `[◀️ prev]` / `[next ▶️]` (future months not shown)
- `[📅 This month]` button appears when viewing a past month

### Write Flow (ForceReply)

**`[📝 Write]` (today's entry):**

1. If today's entry already exists → show existing entry preview with `[✏️ Edit]` / `[👁 View]` / `[◀️ Menu]`
2. If no entry → ForceReply prompt:
   ```
   📓 Write Diary

   2026/03/07 (Mon)

   Record today in your diary.
   ```
   Placeholder: `How was today?`

3. After submission:
   ```
   ✅ Diary saved!

   2026/03/07 (Mon)

   [👁 View] [📄 List]
   ```

**`[⏪ Yesterday]` (yesterday's entry):**

Same flow but with `"Record yesterday in your diary."` prompt and `"How was yesterday?"` placeholder.

### View Entry Screen

```
📓 2026/03/07 (Mon)

Today was productive. Finished the feature and wrote tests.
The deploy went smoothly too.

[✏️ Edit] [🗑 Delete]
[◀️ List]
```

Content is shown in a code block (monospace).

### Edit Flow

Tap `[✏️ Edit]` from view screen:

```
✏️ Edit Diary

2026/03/07 (Mon)

Current content:
today was productive...

```
ForceReply prompt: `✏️ Enter new content:`
Placeholder: `Enter new content...`

After submission:
```
✅ Diary updated!

2026/03/07 (Mon)

[👁 View] [📄 List]
```

### Delete (with Confirmation)

Tap `[🗑 Delete]` from view screen:

```
🗑 Delete this entry?

2026/03/07 (Mon)
Today was productive. Finished the fea...

[✅ Delete] [❌ Cancel]
```

After deletion: returns to monthly list with `🗑 [date] diary deleted.` prepended.

### Schedule Action

| Action | Recommended Time | Behavior |
|--------|-----------------|----------|
| `daily_diary` | (user sets) | If today's entry already written: shows preview with Edit/View buttons. If not written: prompts to write with Write/Yesterday/List buttons |

The reminder message is prefixed with `🔔 Daily diary reminder`.

---

## Memo Plugin

### Concept

Short text memo storage. Maximum 30 memos (manageable on mobile without excessive scrolling). CRUD + multi-delete.

### Triggers

- Natural language: `메모`, `memo` (exact match)
- Excluded: question patterns → passed to AI

### Main Screen

```
📝 Memo

Saved: 5

[📄 List] [➕ Add]
[✨ Work with AI]
```

When at max capacity: `Saved: 30 (max 30)`

### List Screen

```
📝 Memo List

#1 Meeting notes for project review...
2026-03-05

#2 Shopping list
2026-03-06

[🗑️ #1 Meeting notes] [🗑️ #2 Shopping li...]  ← 15-char truncate
[☑️ Multi-delete]                                ← Only shown when 2+ memos exist
[➕ Add] [🔄 Refresh]
[⬅️ Back]
```

- Empty state: `📭 No saved memos.` + `[➕ Add]` + `[⬅️ Back]`

### Add (ForceReply)

`[➕ Add]` → ForceReply → input → saved:
```
✅ Memo saved!

#3 This is my new memo content

[📄 List] [➕ Add]
```

- Empty input: `❌ Memo content is empty.` + `[📝 Try again]`
- Over 30 memos: `❌ Maximum 30 memos reached. Delete some before adding new ones.`

### Single Delete (2-step Confirmation)

Tap `[🗑️ #N ...]` → confirmation screen:
```
🗑️ Delete?

#5 This is the memo content

[✅ Delete] [❌ Cancel]
```

After deletion: `🗑️ Deleted: ~~content~~` (strikethrough, 20-char truncate) + list refreshed.

### Multi-delete

`[☑️ Multi-delete]` → selection mode:
```
☑️ Select Memos to Delete

Tap to select.

[⬜ #1 Short preview...] [✅ #2 Selected memo]  ← 20-char truncate
[🗑️ Delete 1]            ← Shown only when selected
[❌ Cancel]
```

Confirmation screen:
```
🗑️ Delete 2 Memos?

• #1 Meeting notes for project re...    ← 30-char truncate
• #2 Shopping list

Are you sure?
[✅ Delete] [❌ Cancel]
```

After deletion: `🗑️ N memos deleted` + list refreshed.

---

## Question Bank Plugin

### Concept

AI-authored question bank with lightweight practice. MVP storage has four practice tables: banks, questions, options, and attempts. There is no manual question-add UI; users create or edit questions by using AI Work, and the AI writes directly to the plugin tables through MCP `query_db`. Scheduled practice uses the shared `schedules` table plus one plugin-owned scope table.

### Triggers

- Natural language: `문제은행`, `퀴즈`, `문제`, `question_bank`, `quiz` (exact match)
- Keyword + content: routed to AI with Question Bank context so the AI can insert/update rows
- Excluded: question patterns like “문제은행이 뭐야” → passed to regular AI

### Main Screen

```
📚 Question Bank

문제집: 1
문제: 24
풀이: 12
정답률: 75%

문제 생성/수정과 스케줄 등록은 AI와 대화해서 처리합니다.

[🎲 전체 랜덤] [📁 문제집]
[❌ 오답 보기] [📊 통계]
[✨ AI로 문제 만들기]
[🔄 Refresh]
```

`[✨ AI로 문제 만들기]` opens `aiwork:question_bank`. The static AI context explains `qb_banks`, `qb_questions`, `qb_options`, `qb_attempts`, and `qb_schedule_configs` plus safe INSERT/UPDATE examples.

### Bank Scope UI

`[📁 문제집]` opens a bank list:

```
📁 문제집 목록

#1 Default  문제 12 / 정답률 80%
#2 네트워크  문제 18 / 정답률 67%

[📁 Default]
[📁 네트워크]
[✨ AI로 문제 만들기]
[⬅️ 메인]
```

Bank detail:

```
📁 문제집

네트워크

문제: 18
풀이: 6
정답률: 67%

[▶️ 이 문제집 풀기] [❌ 오답 보기]
[📊 통계]
[✨ AI로 작업]
[⬅️ 문제집]
```

### Practice Flow

Practice picks one active random question. MVP has no quiz-session table.
- Global scope: all active questions for `chat_id`
- Bank scope: active questions from one `qb_banks.id`
- Wrong-only scope: questions with at least one wrong attempt in that scope

Multiple-choice:

```
📝 문제 #12
📁 네트워크
전체 문제
객관식

HTTP 성공 상태 코드는?
1. 404
2. 200
3. 500

[1] [2] [3]
[⏭️ 다른 문제] [⬅️ 메인]
```

Short-answer:

```
📝 문제 #7
📁 Default
전체 문제
단답식

대한민국의 수도는?

[✍️ 답 입력]
[⏭️ 다른 문제] [⬅️ 메인]
```

Subjective:

```
📝 문제 #21
📁 AWS
AWS
주관식

REST API의 특징을 설명하세요.

[✍️ 답 입력]
[⏭️ 다른 문제] [⬅️ 메인]
```

### Grading Rules

- Multiple-choice: selected button number must equal `qb_questions.correct_option_no`.
- Short-answer: use exact matching only for one clear canonical answer or a tiny alias set such as `서울 || Seoul`.
- `loose_text` may be used for selected short-answer questions when only spaces, commas, or surrounding parentheses should be ignored. It does not ignore meaningful technical symbols like `+`, `-`, `.`, `/`, `#`, `:`, `_`.
- Ambiguous short-answer prompts are auto-upgraded to AI grading at answer time. This includes ordered steps, multiple required items, comparisons, long phrase answers, or anything that is not safe for exact matching.
- Subjective: answer is saved as a pending `qb_attempts` row, then a generated AI grading prompt is dispatched through the normal AI job system. The AI must update that attempt row with score, correctness, feedback, and `ai_status = 'done'`.
- AI-graded responses may attach extra continuation buttons to the final detached AI reply. Question Bank uses this to keep the user moving after subjective or AI-graded short-answer evaluation.

### Result Screen

Correct:

```
✅ 정답

문제 #12
HTTP 성공 상태 코드는?

내 답: 200
정답: 2. 200
점수: 1 / 1

해설
HTTP 200은 성공입니다.

[✨ AI와 대화]
[🔁 다시 풀기] [➡️ 다음 문제]
[⬅️ 메인]
```

Wrong:

```
❌ 오답

문제 #7
대한민국의 수도는?

내 답: 부산
정답: 서울
점수: 0 / 1

해설
단답식은 정확히 일치해야 정답입니다.

[✨ AI와 대화]
[🔁 다시 풀기] [➡️ 다음 문제]
[⬅️ 메인]
```

The result screen always includes `[✨ AI와 대화]`. Tapping it opens a ForceReply prompt and sends the user's follow-up to a new AI session with the question, expected answer, user answer, score, and feedback as context.

When the user answers from a bank-scoped or wrong-only flow, both `[🔁 다시 풀기]` and `[➡️ 다음 문제]` preserve that same scope.

For subjective and AI-graded short answers, the immediate “채점 요청됨” message includes `[➡️ 계속 문제 풀기]`, and the final detached AI grading response also includes the same continuation button so the user is not trapped in the grading session log.

### Wrong Answers

```
❌ 네트워크 최근 오답

#7 대한민국의 수도는?
#18 HTTP 캐시 헤더의 역할은?

[🔁 #7] [✨ AI]
[🔁 #18] [✨ AI]
[🎯 오답 랜덤] [⬅️ 메인]
```

Wrong answers may be viewed globally or from a bank detail screen. Bank-scoped wrong lists keep the bank scope when reopening a question or drawing the next wrong question.

### Empty State

If no questions exist:

```
📭 아직 문제가 없습니다.

아래 버튼으로 AI에게 문제 생성을 요청하세요.

[✨ AI로 문제 만들기]
[⬅️ 메인]
```

### Scheduled Practice

Question Bank registers one plugin schedule action:

- `scheduled_practice`: send one interactive question at schedule time

The schedule row itself stays in the shared `schedules` table:

- `schedule_type = 'plugin'`
- `plugin_name = 'question_bank'`
- `action_name = 'scheduled_practice'`

Question Bank-specific scope is stored in `qb_schedule_configs`:

- `scope_type = 'all'`
- `scope_type = 'bank'` with one `bank_id`
- `scope_type = 'wrong_all'`
- `scope_type = 'wrong_bank'` with one `bank_id`
- `question_count` stays `1` in MVP

If a wrong-only scheduled practice has no remaining wrong questions, the plugin returns no notification for that run. Other empty scopes return an “AI로 문제 만들기” empty state.
