# AI Bot - UI/UX 기획서

> Layer 3: 사용자 경험 의도, 시나리오, UX 원칙
> 코드에 존재하지 않는 기획 정보를 기술한다.

---

## 전체 UX 원칙

| 원칙 | 설명 |
|------|------|
| **즉시 피드백** | 플러그인/명령어는 AI 호출 없이 즉시 응답. AI 호출 시에도 "처리 중" 상태를 사용자가 인지 |
| **한 탭 완료** | 가능한 한 인라인 버튼 한 번으로 작업 완료. 멀티스텝은 최소화 |
| **현재 상태 표시** | 모든 화면에서 현재 세션, 모델, 개수 등 컨텍스트를 표시 |
| **안전한 삭제** | 삭제 작업은 2단계 확인 (확인 버튼 → 실행). 예외: 워크스페이스 삭제 (단일 탭, 재등록이 용이하므로) |
| **빈 상태 유도** | 데이터가 없을 때 "추가" 행동을 유도하는 버튼 표시 |
| **비파괴적 에러** | 에러 발생 시 사용자 데이터 손실 없이 재시도 안내 |
| **영어 UI** | 모든 사용자 대면 텍스트는 영어. 시스템 프롬프트에 의해 Claude 응답만 한국어 |
| **플러그인 탈출구** | 플러그인이 자연어를 가로챌 수 있으므로, `/ai` 명령어로 항상 현재 AI에 직접 질문 가능 |

## 응답 포맷 규칙

- 모든 응답은 **Telegram HTML** (`<b>`, `<i>`, `<code>`, `<pre>`)
- 마크다운 문법 금지 (`**`, `*`, `#`, `` ` ``, `>`)
- AI 응답에 마크다운이 포함될 경우 `markdown_to_telegram_html()` 변환기가 자동으로 HTML로 변환
- 테이블 미지원 → 불릿/번호 리스트 사용
- 모바일 최적화: 간결한 텍스트, 4096자 제한 (안전 마진 4000자)
- 4000자 초과 시 줄바꿈 기준으로 자동 분할 전송 (줄바꿈이 없으면 4000자 단위로 분할)

## 모델 표현 체계

| 모델 | 이모지 | 리스트 약자 |
|------|--------|------------|
| opus | 🧠 | `[O]` |
| sonnet | ⚡ | `[S]` |
| haiku | 🚀 | `[H]` |

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
| `/reload [name]` | 플러그인 리로드 (전체 또는 특정 플러그인) | - |
| `/ai <question>` | 플러그인 우회, 현재 AI에 직접 질문 | - |
| `/select_ai` | 현재 AI 제공자 선택 (`Claude` / `Codex`) | - |
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
CLI AI Bot

{인증 상태}
Current AI: {provider}
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

### AI 제공자 선택

사용자는 한 시점에 하나의 AI 제공자를 활성화한다.

| 제공자 | 성격 | 세션/모델 예시 |
|------|------|---------------|
| `Claude` | 기존 대화형 코딩 보조 | `opus`, `sonnet`, `haiku` |
| `Codex` | ChatGPT 로그인 기반 CLI 코딩 에이전트 | `GPT-5.4 High`, `GPT-5.4 XHigh`, `GPT-5.3 Codex Medium` |

### `/select_ai`

```
Current AI: Claude

[Claude] [Codex]
[Cancel]
```

- 선택 즉시 현재 AI가 바뀐다.
- 세션 목록(`/sl`), 현재 세션(`/session`), 새 세션(`/new`), 모델 변경(`/model`)은 모두 현재 선택된 AI 기준으로 동작한다.
- AI를 바꿔도 다른 AI의 세션은 삭제되지 않는다. 단지 화면에서 숨겨진다.
- 각 AI는 별도의 current session 상태를 가진다. Claude에서 보던 현재 세션과 Codex에서 보던 현재 세션을 각각 기억한다.
- 시작 화면, 도움말, 세션 화면에는 항상 `Current AI: {provider}`를 표시한다.

### 사용자 시나리오

**신규 사용자 첫 메시지:** 세션이 없으면 현재 AI의 기본 프로필로 자동 생성 → detached worker가 provider CLI를 호출. 사용자가 세션을 의식하지 않아도 바로 대화 가능.

**모델 선택 세션 생성:** `/new` → 모델 버튼 선택 → 이름 입력(ForceReply) → 생성 완료. 단축 명령어 `/new_opus` 등으로 한 단계 스킵 가능.

**프리셋 세션:** `/new_haiku_speedy` (빠른 응답용), `/new_opus_smarty` (고품질 분석용). 자주 쓰는 조합을 한 번에.

**세션 전환:** `/sl`로 목록 → 세션 이름 버튼 클릭 → `/session` 전체 정보 표시 (모델/히스토리/버튼 포함). `/s_{id}` 직접 입력 시에는 간단한 전환 메시지만 표시.

**세션 삭제:** 목록에서 `Del` 버튼 → 확인 화면 → `Delete` 버튼. 현재 세션은 삭제 불가 (다른 세션으로 전환 먼저 필요).

**이전 세션 복귀:** `/back` → 직전에 사용하던 세션으로 즉시 전환.

### 세션 목록 화면

```
Session List - Claude (HH:MM:SS)        ← 타임스탬프는 콜백 갱신 시에만 표시

> [S] SessionName (abc12345)        ← 현재 세션에 > 표시
[O] OtherSession (def67890) [locked]  ← 처리 중이면 [locked]

[SessionName] [History] [Del]       ← 각 세션별 액션 버튼
[OtherSess..] [History] [Del]

[+ provider models]                 ← 현재 AI의 모델 버튼
[Refresh] [Tasks]
[Switch AI]
```

- 최대 10개 표시
- 세션 이름은 버튼에서 10자 truncate
- 현재 선택한 AI의 세션만 표시
- Claude/Codex를 바꾸면 같은 `/sl` 명령어가 다른 목록을 보여준다

### 세션 정보 화면 (`/session`)

현재 세션의 상세 정보 + 최근 히스토리 10건 + 모델 변경/리네임/히스토리/삭제/Session List 버튼.
히스토리 항목에 처리자 표시: `[cmd]` (명령어), `[plugin]` (플러그인), `[x]` (거절), 없음 (AI).

- 상단에 현재 AI를 표시: `Current AI: Claude`
- 모델 버튼은 현재 세션의 AI가 제공하는 모델/프로필만 표시
- Claude와 Codex 세션은 서로 다른 모델 버튼 집합을 가진다

### 모델 변경 (`/model`)

- `/model` (인자 없음): `/session`으로 리다이렉트 (세션 정보 + 모델 변경 버튼 표시)
- `/model {profile}`: 현재 AI의 모델/프로필로 변경
- 동일 모델: `Already using {model}.`
- 세션 없음: 세션 생성 안내
- 지원되지 않는 모델: 현재 AI 기준 지원 목록을 표시
- 인라인 버튼으로도 변경 가능 (`/session` 화면, 세션 전환 후 화면)

### 모델/프로필 UX

사용자는 내부 CLI 플래그를 직접 알 필요가 없다. UI는 사람이 읽는 이름만 보여준다.

| AI | UI 라벨 | 내부 의미 |
|----|--------|----------|
| Claude | `Opus` | `opus` |
| Claude | `Sonnet` | `sonnet` |
| Claude | `Haiku` | `haiku` |
| Codex | `GPT-5.4 High` | `model=gpt-5.4`, `reasoning=high` |
| Codex | `GPT-5.4 XHigh` | `model=gpt-5.4`, `reasoning=xhigh` |
| Codex | `GPT-5.3 Codex Medium` | `model=gpt-5.3-codex`, `reasoning=medium` |

- DB에는 profile key를 저장하고, 실제 provider별 CLI 플래그는 내부에서 해석한다.
- 버튼, 세션 목록, `/session`, `/tasks`에는 UI 라벨만 보여준다.
- Codex profile은 reasoning depth를 포함한 "모델 프로필" 개념으로 다룬다.

### 세션 이름 변경 (`/rename`)

- `/rename` (인자 없음): 현재 이름 표시 + 사용법 안내
- `/rename_newname`: 현재 세션 이름 변경
- `/r_{id}_newname`: 특정 세션 ID의 이름 변경
- `/session` 화면에서도 리네임 버튼으로 변경 가능 (ForceReply 방식)
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

현재 세션에 detached worker가 실행 중일 때 새 메시지가 오면:

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
- 요청 임시 저장 만료: 5분
- 만료 시: `Request expired. Please resend the message.`
- `Wait in this session` 선택 후 저장되는 persistent queue는 자동 만료되지 않음
- 세션 사용 여부는 봇 메모리가 아니라 persistent lock 기준으로 판단
- 따라서 봇이 재시작돼도 같은 세션은 계속 busy로 보이며 중복 실행되지 않음

### 봇 재시작 중 응답 보존

자가 개발 중 AI agent가 `./run.sh restart`를 실행할 수 있다. 이때 UX 목표는 "응답 유실 없이 계속 진행되는 것"이다.

| 상황 | 사용자 경험 |
|------|-------------|
| 요청 접수 직후 | 핸들러는 즉시 반환. 사용자는 봇이 멈춘 것처럼 느끼지 않음 |
| 처리 중 봇 재시작 | 별도 복구 질문 없이 기존 작업이 계속 진행되고, 완료 응답이 그대로 도착 |
| 재시작 중 같은 세션에 새 메시지 | 세션은 여전히 busy로 보이며 Session Queue UI가 그대로 동작 |
| `Wait in this session` 선택 | 요청은 영속 대기열에 저장되고 현재 작업 완료 뒤 자동 실행 |
| worker 자체 비정상 종료 | 유실 알림 후 재전송을 유도 |

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
- 현재 AI 기준 모델 선택 버튼을 사용한다. 같은 워크스페이스라도 Claude/Codex 세션은 각각 따로 가질 수 있다.
- 스케줄 등록: 시간 → 분 → 모델 → 메시지 입력 → 등록 완료.

---

## 스케줄러

### 개념

매일 지정 시간에 자동 실행되는 작업. 3가지 타입: AI (일반 대화), Workspace (프로젝트 컨텍스트), Plugin (플러그인 액션). 시간 범위: 06~22시 (새벽 알림 방지).

### 사용자 시나리오

**스케줄 확인:** `/scheduler` → 등록된 스케줄 목록 (시간순 정렬, 활성/비활성 표시).

**AI 스케줄 추가:** `+ Current AI` → 시간(06~22h) → 분(5분 간격) → 모델 → 메시지 입력 → 등록.

- 일반/워크스페이스 스케줄은 생성 시점의 현재 AI 제공자를 따른다.
- Plugin 스케줄은 AI 제공자와 무관하다.

**Workspace 스케줄 추가:** `+ Workspace` → 워크스페이스 선택 → 시간 → 분 → 모델 → 메시지 → 등록.

**Plugin 스케줄 추가:** `+ Plugin` → 플러그인 선택 → 액션 선택 → 시간 → 분 → 등록. (모델/메시지 불필요)

**스케줄 관리:** 목록에서 스케줄 클릭 → 상세 화면 → ON/OFF 토글, 시간 변경, 삭제.

### 스케줄 목록 화면

```
{ON/OFF} {타입} ScheduleName - HH:MM

[{ON/OFF} HH:MM {타입이모지} name]    ← 각 스케줄 버튼
[+ Current AI] [+ Workspace] [+ Plugin]  ← 추가 버튼
[Refresh]

System Jobs                            ← 시스템 잡 (hourly_ping 등)
  {schedule_info} - {job_name}
```

- 타입 이모지: `💬` AI, `📂` Workspace, `🔌` Plugin
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

- 시간: 06h~22h (KST 기준), 4열 그리드 (17개 버튼)
- 분: 00~55, 5분 간격, 4열 그리드 (12개 버튼)

### 스케줄 실행 결과

실행 완료 시 사용자에게 전송 (4000자 초과 시 분할 전송):
```
📅 ScheduleName

{실행 결과 텍스트}
```

---

## `/ai` - 현재 AI 직접 질문

코드 구조와 런타임 책임 분리는 [ARCHITECTURE.md](./ARCHITECTURE.md) 참조.

### 개념

플러그인이 자연어 패턴을 가로챌 수 있으므로 (예: "메모"라고 입력하면 메모 플러그인이 처리), 사용자가 의도적으로 현재 AI에게 질문하고 싶을 때 사용하는 탈출구.

### 시나리오

- `/ai 메모란 뭐야` → 메모 플러그인 우회, 현재 AI가 "메모"에 대해 답변
- `/ai` (인자 없음) → 사용법 안내

---

## 빌트인 플러그인

빌트인 플러그인(Todo, Memo, Weather) 기획은 [SPEC_PLUGINS_BUILTIN.md](SPEC_PLUGINS_BUILTIN.md) 참조.

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
| detached watchdog 타임아웃 (30분) | `Task exceeded 30 minutes and was stopped. Please try again.` |
| provider 내부 타임아웃 | `Response timed out. Please try again.` |
| 빈 응답 | `{question_preview} Response is empty. Please try again.` |
| CLI 에러 | `Error: {error_detail}` |
| 처리 중 예외 | `An error occurred. Please try again later.` |
| 세션 초기화 중 | `Session initializing... Please try again shortly!` |
| 세션 생성 실패 | `Failed to create Claude session. Please try again.` |

### 장시간 작업 정책

| 경과 시간 | 동작 |
|----------|------|
| 0~5분 | 처리 중 (별도 표시 없음) |
| 5분 | 알림: `Task taking N+ minutes. Still running. I will notify you when it finishes.` |
| 5~30분 | detached worker는 계속 실행. provider client에는 별도 5분 hard timeout 없음 |
| 30분 | detached watchdog이 작업 중단, DB에는 `watchdog_timeout` 저장, 사용자에게 timeout 메시지 전송 |
| 완료 (5분 이상 걸린 경우) | 성공인 경우에만 알림: `Task complete! (Mm Ss)` |
| 봇 재시작 | detached worker가 계속 실행되고 완료 후 결과를 전송 |

### 동시 요청 정책

세션 단위 직렬화를 우선한다. 같은 세션에는 동시에 하나의 detached worker만 붙는다.

| 세션 상태 | 동작 |
|----------|------|
| 세션 idle | detached worker 즉시 시작 |
| 같은 세션 busy | Session Queue UI 표시 |
| `Wait` 선택 | persistent queue에 저장 후 현재 작업 완료 뒤 자동 처리 |
| 다른 세션 선택 | 세션 전환 후 즉시 처리 |
| 새 세션 선택 | 새 세션 생성 후 즉시 처리 |

### SQLite WAL 메모

- WAL이어도 reader 여러 개 + writer 한 개 모델이다.
- 즉 서로 다른 세션/row를 만지는 write라도 동시에 commit 단계로 들어가면 직렬화된다.
- 이 프로젝트는 그래서 DB row lock에 기대기보다, 앱 레벨 `session_locks`로 같은 세션의 worker를 직렬화하고, 각 write는 autocommit으로 짧게 끝내는 방식을 기본 원칙으로 둔다.

### AI 대화 실행 시나리오

1. 사용자가 메시지를 보냄
2. 세션이 idle이면 `message_log` row 생성 + `session_locks` 예약 + detached worker spawn
3. worker가 provider CLI를 호출하고, 5분이 지나면 "still running" 알림만 전송
4. 같은 세션에 새 메시지가 오면 Session Queue UI 표시
5. 사용자가 `Wait in this session`을 누르면 요청이 persistent queue에 저장됨
6. 현재 worker가 성공/실패/timeout으로 종료되면 lock을 유지한 채 다음 queued message를 이어 처리
7. 마지막 queued message까지 끝나면 lock 해제

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
Processing (2)

1. session-name
   3m 45s elapsed
   Can you summarize the...       ← 40자 truncate (표시 시)

2. research
   1m 12s elapsed
   Write a function...

Queue (1)
- session-name: Waiting message pre...  ← 30자 truncate

[Refresh] [Session List]
```

- 태스크 없음: `No active tasks`
- 멀티라인 메시지는 한 줄 미리보기로 정규화해서 표시
- 처리 중/대기열 상태는 DB 기준으로 계산되므로, 봇 재시작 직후에도 끊기지 않음

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
| 세션 충돌 UI 선택 만료 (5분) | `Request expired. Please resend the message.` |
| 동일 워크스페이스 세션 존재 | 기존 세션으로 자동 전환 (중복 생성 방지) |

---

## 언어 정책

| 영역 | 언어 | 비고 |
|------|------|------|
| 봇 UI (명령어 응답, 버튼, 에러) | 영어 | 통일 완료 |
| Claude 응답 | 한국어 | 시스템 프롬프트로 지정 |
| 플러그인 트리거 | 한국어 | `할일`, `메모`, `날씨` 등 자연어 |
| 내부 에러/디버그 로그 | 한국어 광범위 잔존 | main.py, handlers, services, client.py 등 |

---

## 향후 계획

- `/model` 명령어 제거 검토 (`/session` 통합으로 중복)
