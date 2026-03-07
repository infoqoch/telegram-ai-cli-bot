# AI Bot - UI/UX 기획서

> Layer 3: 사용자 경험 의도, 시나리오, UX 원칙
> 코드에 존재하지 않는 기획 정보를 기술한다.

---

## 전체 UX 원칙

| 원칙 | 설명 |
|------|------|
| **즉시 피드백** | 플러그인/명령어는 Claude 호출 없이 즉시 응답. AI 호출 시에도 "처리 중" 상태를 사용자가 인지 |
| **한 탭 완료** | 가능한 한 인라인 버튼 한 번으로 작업 완료. 멀티스텝은 최소화 |
| **현재 상태 표시** | 모든 화면에서 현재 세션, 모델, 개수 등 컨텍스트를 표시 |
| **안전한 삭제** | 삭제 작업은 2단계 확인 (확인 버튼 → 실행). 예외: 워크스페이스 삭제 (단일 탭, 재등록이 용이하므로) |
| **빈 상태 유도** | 데이터가 없을 때 "추가" 행동을 유도하는 버튼 표시 |
| **비파괴적 에러** | 에러 발생 시 사용자 데이터 손실 없이 재시도 안내 |
| **영어 UI** | 모든 사용자 대면 텍스트는 영어. 시스템 프롬프트에 의해 Claude 응답만 한국어 |
| **플러그인 탈출구** | 플러그인이 자연어를 가로챌 수 있으므로, `/ai` 명령어로 항상 Claude에 직접 질문 가능 |

## 응답 포맷 규칙

- 모든 응답은 **Telegram HTML** (`<b>`, `<i>`, `<code>`, `<pre>`)
- 마크다운 문법 금지 (`**`, `*`, `#`, `` ` ``, `>`)
- Claude 응답에 마크다운이 포함될 경우 `markdown_to_telegram_html()` 변환기가 자동으로 HTML로 변환
- 테이블 미지원 → 불릿/번호 리스트 사용
- 모바일 최적화: 간결한 텍스트, 4096자 제한 (안전 마진 4000자)
- 4000자 초과 시 자동 분할 전송

## 모델 표현 체계

| 모델 | 이모지 | 리스트 약자 | 색상 뱃지 |
|------|--------|------------|----------|
| opus | 🧠 | `[O]` | 🟣 |
| sonnet | ⚡ | `[S]` | 🔵 |
| haiku | 🚀 | `[H]` | 🟢 |

---

## 전체 명령어 목록

| 명령어 | 설명 | 단축 |
|--------|------|------|
| `/start` | 봇 시작 화면 (인증 상태, 현재 세션) | - |
| `/help` | 전체 명령어 도움말 | - |
| `/auth <key>` | 인증 (REQUIRE_AUTH=true 시) | - |
| `/status` | 인증 상태 확인 | - |
| `/new [model] [name]` | 새 세션 생성 | - |
| `/new_opus`, `/new_sonnet`, `/new_haiku` | 모델별 세션 단축 생성 | - |
| `/new_haiku_speedy` | Haiku + "Speedy" 이름 프리셋 | - |
| `/new_opus_smarty` | Opus + "Smarty" 이름 프리셋 | - |
| `/model [model]` | 현재 세션 모델 변경/확인 | - |
| `/model_opus`, `/model_sonnet`, `/model_haiku` | 모델 변경 단축 | - |
| `/session` | 현재 세션 정보 + 히스토리 | - |
| `/session_list` | 세션 목록 | `/sl` |
| `/s_{id}` | 세션 전환 | - |
| `/h_{id}` | 세션 히스토리 | `/history_{id}` |
| `/d_{id}` | 세션 삭제 | `/delete_{id}` |
| `/rename_name` | 현재 세션 이름 변경 | - |
| `/r_{id}_name` | 특정 세션 이름 변경 | - |
| `/back` | 이전 세션으로 복귀 | - |
| `/new_workspace path [model] [name]` | 워크스페이스 세션 생성 | `/nw` |
| `/workspace` | 워크스페이스 관리 | `/ws` |
| `/scheduler` | 스케줄 관리 | - |
| `/tasks` | 활성 태스크/큐 현황 | - |
| `/plugins` | 플러그인 목록 | - |
| `/ai <question>` | 플러그인 우회, Claude에 직접 질문 | - |
| `/chatid` | 내 Chat ID 확인 | - |
| `/{plugin}` | 플러그인 사용법 (예: `/todo`, `/memo`) | - |

---

## 접근 제어

### 인증 플로우

```
사용자 메시지 도착
    │
    ├─ ALLOWED_CHAT_IDS 미포함 → "Access denied." (종료)
    │
    ├─ REQUIRE_AUTH=false → 통과
    │
    └─ REQUIRE_AUTH=true
         ├─ 인증 세션 유효 → 통과
         └─ 미인증/만료 → "Authentication required." + /auth 안내 (종료)
```

### `/auth` 명령어

- 인자 없음: `Usage: /auth <secret_key>`
- 성공: `Authenticated! Valid for {N} minutes.`
- 실패: `Authentication failed. Wrong key.`

### 인증 상태 표시

| 위치 | 인증됨 | 미인증 | 인증 불필요 |
|------|--------|--------|------------|
| `/start` | `Auth: Authenticated (Xm remaining)` | `Auth: Authentication required` | `No authentication required` |
| `/help` | `/auth`, `/status` 섹션 표시 | 동일 | 섹션 숨김 |
| `/status` | `Authenticated (Xm remaining)` | `Authentication required.` | - |

---

## 기본 화면

### `/start`

```
Claude Code Bot

{인증 상태}
Session: [{세션정보}] ({N} messages)

/help for commands
```

### `/help`

전체 명령어를 카테고리별로 표시. 인증 필요 시 Authentication 섹션 포함, 플러그인 있으면 Plugins 섹션 포함.

### 알 수 없는 명령어

등록되지 않은 `/xxx` 입력 시: `Unknown command: {command}` + `/help for command list`

### `/chatid`

```
My Info

- Chat ID: {chat_id}
- Username: @{username}
- Name: {first_name}

Add this ID to ALLOWED_CHAT_IDS.
```

### `/plugins`

```
Plugin List

- /todo - 할일 관리
- /memo - 메모 저장
...

Use /plugin_name for usage details
```

플러그인 없음: `No plugins loaded.`

---

## 세션 관리

### 사용자 시나리오

**신규 사용자 첫 메시지:** 세션이 없으면 기본 모델(sonnet)로 자동 생성 → Claude 호출. 사용자가 세션을 의식하지 않아도 바로 대화 가능. 기본 모델이 sonnet인 이유: 속도와 품질의 균형.

**모델 선택 세션 생성:** `/new` → 모델 버튼 선택 → 이름 입력(ForceReply) → 생성 완료. 단축 명령어 `/new_opus` 등으로 한 단계 스킵 가능.

**프리셋 세션:** `/new_haiku_speedy` (빠른 응답용), `/new_opus_smarty` (고품질 분석용). 자주 쓰는 조합을 한 번에.

**세션 전환:** `/sl`로 목록 → 세션 이름 버튼 클릭 → 즉시 전환. 또는 `/s_{id}` 직접 입력.

**세션 삭제:** 목록에서 `Del` 버튼 → 확인 화면 → `Delete` 버튼. 현재 세션은 삭제 불가 (다른 세션으로 전환 먼저 필요).

**이전 세션 복귀:** `/back` → 직전에 사용하던 세션으로 즉시 전환.

### 세션 목록 화면

```
Session List (HH:MM:SS)        ← 타임스탬프는 콜백 갱신 시에만 표시

> [S] SessionName (abc12345)        ← 현재 세션에 > 표시
[O] OtherSession (def67890) [locked]  ← 처리 중이면 [locked]

[SessionName] [History] [Del]       ← 각 세션별 액션 버튼
[OtherSess..] [History] [Del]

[+Opus] [+Sonnet] [+Haiku]         ← 하단 고정
[Refresh] [Tasks]
```

- 최대 10개 표시
- 세션 이름은 버튼에서 10자 truncate

### 세션 정보 화면 (`/session`)

현재 세션의 상세 정보 + 최근 히스토리 10건 + 모델 변경/삭제 버튼.
히스토리 항목에 처리자 표시: `[cmd]` (명령어), `[plugin]` (플러그인), `[x]` (거절), 없음 (Claude).

### 모델 변경 (`/model`)

- `/model` (인자 없음): 현재 모델 표시 + 변경 안내
- `/model opus`: 모델 변경. 단축: `/model_opus`, `/model_sonnet`, `/model_haiku`
- 동일 모델: `Already using {model}.`
- 세션 없음: 세션 생성 안내
- 지원되지 않는 모델: `Unsupported model: xxx. Available: opus, sonnet, haiku`
- 인라인 버튼으로도 변경 가능 (`/session` 화면, 세션 전환 후 화면)

### 세션 이름 변경 (`/rename`)

- `/rename` (인자 없음): 현재 이름 표시 + 사용법 안내
- `/rename_newname`: 현재 세션 이름 변경
- `/r_{id}_newname`: 특정 세션 ID의 이름 변경
- 이름 최대 50자 제한

### 세션 히스토리 (`/h_{id}`)

```
Session History
- ID: {id}
- Messages: {count}

1. {message_preview}    ← 60자 truncate
2. {message_preview}
...

/s_{id} Switch to this session
```

빈 히스토리: `No history.`

### 세션 충돌 처리 (Session Queue)

현재 세션이 처리 중일 때 새 메시지가 오면:

```
Current session is processing
(메시지 미리보기)

선택지:
1. [Wait in this session (recommended)] → 큐에 추가, 완료 후 자동 처리
2. [다른 세션 버튼]                      → 해당 세션으로 전환 + 즉시 처리
3. [+Opus/Sonnet/Haiku]                 → 새 세션 생성 + 즉시 처리
4. [Cancel]                             → 요청 취소
```

- 대기열 위치 표시: `Position: #N`
- 요청 임시 저장 만료: 5분 (`expires_at = time.time() + 300`)
- 만료 시: `Request expired. Please resend the message.`

---

## 워크스페이스

### 개념

로컬 디렉토리에 바인딩된 세션. 해당 디렉토리의 CLAUDE.md 규칙을 따르면서 텔레그램 포맷으로 응답. 개발 프로젝트별 AI 어시스턴트를 텔레그램에서 사용하는 것이 목적.

### 사용자 시나리오

**빠른 워크스페이스 세션:** `/nw ~/AiSandbox/my-app opus` → 즉시 생성. 경로/모델/이름을 한 줄로.

**워크스페이스 등록 + 관리:** `/workspace` (= `/ws`) → 목록 화면 → `+ Add New` → AI 추천 또는 수동 입력.

**AI 추천 등록 플로우:**
1. 목적 입력 (ForceReply): "투자 분석 프로젝트"
2. AI가 ALLOWED_PROJECT_PATHS 내에서 적합한 디렉토리 추천
3. 추천 목록에서 선택 → 이름 입력 → 등록 완료
4. 추천 실패 시 수동 입력으로 전환

**수동 등록 플로우:**
1. 경로 입력 (ForceReply) → 존재 여부 검증
2. 이름 입력 (ForceReply)
3. 설명 입력 (ForceReply)
4. 등록 완료

### 워크스페이스 목록 화면

```
{활성도 이모지} WorkspaceName
   ~/short/path

[WorkspaceName] [Del]        ← 각 워크스페이스별 (삭제는 확인 없이 즉시)
[+ Add New] [Refresh]        ← 하단 고정
```

- 활성도: 사용 5회 초과 `🔥`, 이하 `📂`

### 워크스페이스 상세 → 액션 선택

워크스페이스 선택 시: `[Session]` (세션 시작) / `[Schedule]` (스케줄 등록) 선택.

- 세션 시작: 모델 선택 → 생성. 동일 워크스페이스 세션이 이미 있으면 자동 전환 (중복 생성 방지).
- 스케줄 등록: 시간 → 분 → 모델 → 메시지 입력 → 등록 완료.

---

## 스케줄러

### 개념

매일 지정 시간에 자동 실행되는 작업. 3가지 타입: Claude (일반 대화), Workspace (프로젝트 컨텍스트), Plugin (플러그인 액션). 시간 범위: 06~22시 (새벽 알림 방지).

### 사용자 시나리오

**스케줄 확인:** `/scheduler` → 등록된 스케줄 목록 (시간순 정렬, 활성/비활성 표시).

**Claude 스케줄 추가:** `+ Claude` → 시간(06~22h) → 분(5분 간격) → 모델 → 메시지 입력 → 등록.

**Workspace 스케줄 추가:** `+ Workspace` → 워크스페이스 선택 → 시간 → 분 → 모델 → 메시지 → 등록.

**Plugin 스케줄 추가:** `+ Plugin` → 플러그인 선택 → 액션 선택 → 시간 → 분 → 등록. (모델/메시지 불필요)

**스케줄 관리:** 목록에서 스케줄 클릭 → 상세 화면 → ON/OFF 토글, 시간 변경, 삭제.

### 스케줄 목록 화면

```
{ON/OFF} {타입} ScheduleName - HH:MM

[{ON/OFF} HH:MM {타입이모지} name]    ← 각 스케줄 버튼
[+ Claude] [+ Workspace] [+ Plugin]  ← 추가 버튼
[Refresh]

System Jobs                            ← 시스템 잡 (hourly_ping 등)
  {schedule_info} - {job_name}
```

- 타입 이모지: `💬` Claude, `📂` Workspace, `🔌` Plugin
- 상태: `✅` 활성, `⏸` 비활성

### 스케줄 상세 화면

```
{타입이모지} ScheduleName

Status: ON/OFF
Time: HH:MM (daily)
Model: model          ← Claude/Workspace만
Path: /path           ← Workspace만
Message: message...   ← Claude/Workspace만 (80자 truncate)
Plugin: name          ← Plugin만
Action: action        ← Plugin만
Runs: N

[ON/OFF 토글]
[Change Time (HH:MM)]
[Delete]
[Back]
```

### 시간 선택 UI

- 시간: 06h~22h, 4열 그리드 (17개 버튼)
- 분: 00~55, 5분 간격, 4열 그리드 (12개 버튼)

### 스케줄 실행 결과

실행 완료 시 사용자에게 전송:
```
📅 ScheduleName

{실행 결과 텍스트}
```

---

## `/ai` - Claude 직접 질문

### 개념

플러그인이 자연어 패턴을 가로챌 수 있으므로 (예: "메모"라고 입력하면 메모 플러그인이 처리), 사용자가 의도적으로 Claude에게 질문하고 싶을 때 사용하는 탈출구.

### 시나리오

- `/ai 메모란 뭐야` → 메모 플러그인 우회, Claude가 "메모"에 대해 답변
- `/ai` (인자 없음) → 사용법 안내

---

## 투두 플러그인

### 개념

일별 할일 관리. 오늘의 할일을 기준으로, 완료/삭제/내일로 이동 가능. 주간 뷰와 어제 미완료 이월 기능 제공.

### 트리거

- 자연어: `todo`, `할일`, `투두` (메시지 시작)
- 제외: 질문 패턴 ("투두란 뭐야", "어떻게", "왜" 등) → AI에게 넘김

### 하루 플로우

```
09:00  [자동] Yesterday Report
       → 어제 미완료 항목 표시 + 이월 버튼

낮     [수동] 할일 추가/완료/삭제
       → "할일" 입력 → 오늘 리스트 + 액션 버튼

21:00  [자동] Daily Wrap-up
       → 오늘 진행률 + 미완료 항목 → 내일로 이동 유도
```

### 오늘 리스트 화면

```
Todos for 2026-03-07

⬜ 1. Buy groceries
✅ 2. Read chapter 5
⬜ 3. Call dentist

1/3 completed

[1. Buy groceries]  [3. Call dentist]  ← 미완료 항목만 버튼 (20자 truncate)
[Multi-select]
[Prev] [Week] [Next]
[Add] [Refresh]
```

- 빈 리스트: `No todos yet.` + `[Add]` 버튼

### 항목 상세 → 액션

항목 클릭 시:
```
Todo

Buy groceries

[Done] [Delete]
[Tomorrow]
[Back]
```

각 액션 후 피드백 메시지 + 리스트 갱신:
- Done: `Marked as done!`
- Delete: `Deleted!`
- Tomorrow: `Moved to tomorrow!`

### 추가 (ForceReply)

`[Add]` → ForceReply 프롬프트 → 사용자 입력 (줄바꿈으로 복수 항목 가능) → 결과:
```
3 added!

- Buy milk
- Clean house
- Fix bug

[View list] [Add more]
```

### 멀티 선택 모드

```
Multi-select

Tap items to select/deselect.

⬜ Buy groceries
☑️ Call dentist

1 selected

[⬜ Buy groceries]  [☑️ Call dentist]    ← 18자 truncate
[Done(1)] [Delete(1)] [Tomorrow(1)]   ← 선택 시에만 표시
[Deselect all] [Back]
```

### 날짜 네비게이션

- `[Prev]`/`[Next]`: 하루씩 이동, 해당 날짜의 할일 표시
- 오늘이 아닌 날짜: 헤더에 `MM/DD` 표시
- `[Today]` 버튼으로 오늘로 복귀

### 주간 뷰

기준일을 중심으로 전후 3일 (총 7일) 표시. 월~일 고정이 아닌 중심일 기반.

```
Weekly Todos (03/04 ~ 03/10)

03/04(Mon): ✅ 3/3        ← 전체 완료
03/05(Tue): ⬜ 1/4        ← 미완료 있음
03/06(Wed): —             ← 할일 없음
👉 03/07(Thu): ⬜ 2/5      ← 오늘 표시

[4(Mon)] [5(Tue)] [6(Wed)] [📍7(Thu)]  ← 날짜 클릭 → 해당일 리스트
[8(Fri)] [9(Sat)] [10(Sun)]
[Prev week] [Today] [Next week]
```

### 어제 미완료 이월

Yesterday Report 또는 `[Carry over]` 버튼으로 진입:

```
Incomplete from 2026-03-06

Select items to carry over to today.

⬜ Unfinished task A
☑️ Unfinished task B

1 selected

[⬜ task A] [☑️ task B]          ← 18자 truncate
[Carry selected(1)] [Carry all(2)]
[Today]
```

- 선택 이월 / 전체 이월 가능
- 빈 상태: `No incomplete items from yesterday!`

### 스케줄 액션

| 액션 | 시간 | 동작 |
|------|------|------|
| `yesterday_report` | 09:00 | 어제 미완료 표시 + 이월 버튼. 어제 할일 없으면 스킵 |
| `daily_wrap` | 21:00 | 오늘 진행률 + 미완료 목록. 전체 완료면 축하 메시지. 할일 없으면 스킵 |

---

## 메모 플러그인

### 개념

짧은 텍스트 메모 저장. 최대 30개 (모바일에서 스크롤 없이 관리 가능한 적정 개수). CRUD + 멀티 삭제.

### 트리거

- 자연어: `메모`, `memo` (정확 일치)
- 제외: 질문 패턴 → AI에게 넘김

### 메인 화면

```
Memo

Saved: 5
(최대: Saved: 30 (max 30))

[List] [Add]
```

### 리스트 화면

```
Memo List

#1 Meeting notes for project review...
2026-03-05

#2 Shopping list
2026-03-06

[🗑️ #1 Meeting notes] [🗑️ #2 Shopping li...]  ← 15자 truncate
[Multi-delete]                                   ← 2개 이상일 때만
[Add] [Refresh]
[Back]
```

- 빈 상태: `No saved memos.` + `[Add]` + `[Back]`

### 추가 (ForceReply)

`[Add]` → ForceReply → 입력 → 저장:
```
Memo saved!

#3 This is my new memo content

[List] [Add]
```

- 빈 입력: `Memo content is empty.`
- 30개 초과: `Maximum 30 memos reached. Delete some before adding new ones.`

### 단일 삭제 (2단계 확인)

`[🗑️ #N]` → 확인 화면:
```
Delete?

#5 This is the memo content...

[Delete] [Cancel]
```

삭제 완료: `Deleted: ~~content~~` (취소선, 20자 truncate) + 리스트 갱신.

### 멀티 삭제

`[Multi-delete]` → 선택 모드:
```
Select Memos to Delete

Tap to select.

[⬜ #1 Short preview...] [✅ #2 Selected memo]  ← 20자 truncate
[Delete 1]               ← 선택 시에만
[Cancel]
```

확인 화면:
```
Delete 2 Memos?

- #1 Meeting notes for project re...    ← 30자 truncate
- #2 Shopping list

Are you sure?
[Delete] [Cancel]
```

---

## 날씨 플러그인

### 개념

도시별 현재 날씨 + 3일 예보. 기본 위치 저장 가능. Open-Meteo API (무료, 키 불필요).

### 트리거

- 자연어: `날씨`, `기온`, `weather`
- 도시 지정: `서울 날씨` (한국어 도시명 + 날씨)
- 위치 설정: `위치 설정: 서울`, `날씨 위치: 서울`, `서울 날씨 설정`

### 날씨 조회 시나리오

**저장된 위치 있음:** `날씨` → 저장 위치의 날씨 즉시 표시.

**저장된 위치 없음:** `날씨` → 도시 선택 화면 (퀵 시티 7개, 2열 그리드).

**도시 지정:** `서울 날씨` → 해당 도시 날씨 즉시 표시.

### 날씨 표시 화면

```
☀️ 서울 Weather

Current
- Weather: Clear
- Temp: 15.2°C
- Humidity: 45%
- Wind: 8.3 km/h

3-Day Forecast
03/07 ☀️ 5° / 16°
03/08 ⛅ 7° / 14°
03/09 🌧️ 3° / 10°

[서울] [부산] [대구] [인천]    ← 퀵 시티 (4+3 그리드)
[광주] [대전] [제주]
[Refresh] [Other city]
```

- 도시 선택 화면(저장 위치 없음)에서는 2열 그리드, 날씨 결과 화면에서는 4+3 그리드

### 위치 설정

`위치 설정: 서울` →
```
Location set!

서울 (South Korea)
Lat: 37.5665, Lon: 126.9780

[Check weather]
```

- 도시 못 찾음: `'{name}' not found. Try a different location.`

### 날씨 아이콘 매핑

| 코드 | 아이콘 | 설명 |
|------|--------|------|
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

## Claude 대화

### 응답 포맷

```
[SessionInfo|#HistoryCount]
question_preview

{Claude 응답 본문}

/s_{id} switch
/h_{id} history
```

- 세션 정보와 히스토리 번호를 헤더에 표시 → 어느 세션에서의 응답인지 식별
- 하단에 세션 전환/히스토리 링크 → 대화 후 바로 탐색 가능

### 에러 응답

| 상황 | 메시지 |
|------|--------|
| 타임아웃 | `Response timed out. Please try again.` |
| 빈 응답 | `{question_preview} Response is empty. Please try again.` |
| CLI 에러 | `Error: {error_detail}` |
| 처리 중 예외 | `An error occurred. Please try again later.` |
| 세션 초기화 중 | `Session initializing... Please try again shortly!` |
| 세션 생성 실패 | `Failed to create Claude session. Please try again.` |

### 장시간 작업 정책

| 경과 시간 | 동작 |
|----------|------|
| 0~5분 | 처리 중 (별도 표시 없음) |
| 5분 | 알림: `Task taking N+ minutes. Will notify on completion!` |
| 완료 (5분 이상 걸린 경우) | 알림: `Task complete! (Mm Ss)` |
| 30분 | Watchdog이 좀비 태스크 강제 종료 (Claude 프로세스 kill 포함) |

### 동시 요청 정책

유저당 최대 3개 Claude 요청 동시 처리 (Semaphore).

| 슬롯 상태 | 동작 |
|----------|------|
| 슬롯 여유 + 세션 미사용 | 즉시 처리 |
| 슬롯 여유 + 세션 사용 중 | 세션 큐 UI 표시 (대기/전환/새 세션) |
| 슬롯 3/3 사용 중 | 대기 |

### 시스템 프롬프트

Claude CLI에 전달되는 전역 프롬프트:
- Telegram HTML 포맷 사용 (마크다운 금지)
- 간결한 응답 (모바일 최적화)
- 한국어 응답 (별도 요청 없는 한)

워크스페이스 세션: 워크스페이스 CLAUDE.md 규칙 + 텔레그램 포맷 규칙이 동시 적용.

---

## 태스크 현황 (`/tasks`)

처리 중인 메시지와 대기열의 실시간 현황 대시보드.

```
Processing (2/3)

1. session-name
   3m 45s elapsed
   Can you summarize the...       ← 40자 truncate (표시 시)

2. research
   1m 12s elapsed
   Write a function...

Queue (1)
- session-name: Waiting message pre...  ← 30자 truncate

Slots: 1/3 available

[Refresh]
```

- 태스크 없음: `No active tasks` + 슬롯 현황
- 태스크 등록 시 메시지 100자로 저장, 표시 시 40자로 재절삭

---

## 에러/엣지 케이스 정책

### 에러 메시지 톤

| 유형 | 톤 | 예시 |
|------|------|------|
| 권한 거부 | 간결, 단호 | `Access denied.` |
| 인증 필요 | 안내 포함 | `Authentication required. /auth <key>` |
| 입력 오류 | 구체적 안내 | `Unsupported model: xxx. Available: opus, sonnet, haiku` |
| 찾을 수 없음 | 사실 전달 | `Session 'xxx' not found.` |
| 시스템 에러 | 재시도 안내 | `An error occurred. Please try again later.` |
| 버튼 만료 | 재시도 안내 | `Button expired. Please try again.` |

### 콜백 에러 처리

| 에러 유형 | 처리 |
|----------|------|
| `Message is not modified` | 무시 (같은 버튼 중복 클릭) |
| `Query is too old` | 만료 안내 메시지 |
| `message to edit not found` | 메시지 삭제됨 안내 |
| 플러그인 미발견 | `{Plugin} plugin not found.` |
| 기타 예외 | `Error occurred.` + 에러 코드 표시 |

### 엣지 케이스

| 상황 | 처리 |
|------|------|
| 현재 세션 삭제 시도 | 거부 + "다른 세션으로 전환 먼저" 안내 |
| 세션 없이 메시지 전송 | 기본 모델(sonnet)로 자동 생성 |
| 세션 없이 /model | 세션 생성 안내 |
| 세션 이름 50자 초과 | 거부 메시지 |
| 메모 30개 초과 | 추가 거부 + 삭제 안내 |
| 투두 빈 입력 | 거부 메시지 |
| 도시 못 찾음 (날씨) | 재입력 안내 |
| 스케줄 워크스페이스 없음 | `/workspace`에서 등록 안내 |
| 스케줄 가능 플러그인 없음 | `get_scheduled_actions()` 구현 안내 |
| ForceReply 입력 만료 | `Input expired. Please try again.` |
| 큐 요청 만료 (5분) | `Request expired. Please resend the message.` |
| 동일 워크스페이스 세션 존재 | 기존 세션으로 자동 전환 (중복 생성 방지) |

---

## 언어 정책

| 영역 | 언어 | 비고 |
|------|------|------|
| 봇 UI (명령어 응답, 버튼, 에러) | 영어 | 통일 완료 |
| Claude 응답 | 한국어 | 시스템 프롬프트로 지정 |
| 플러그인 트리거 | 한국어 | `할일`, `메모`, `날씨` 등 자연어 |
| 내부 에러 로그 | 한국어 일부 잔존 | session_service, client.py fallback 텍스트 |

---

## 로드맵

<!-- TODO: 향후 개발 계획, 미구현 기획 -->
