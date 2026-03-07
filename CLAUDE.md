# AI Bot - 프로젝트 규칙

## 문서 아키텍처

본 프로젝트의 문서는 3개 레이어로 구성된다.

| 레이어 | 파일 | 성격 | 참조 시점 |
|--------|------|------|----------|
| **Layer 1: 개발 규칙** | `CLAUDE.md` | 코드로 표현 불가능한 메타 규칙 | 모든 작업의 시작과 끝 |
| **Layer 2: 개발 인터페이스** | `CLAUDE.md` | 확장 포인트의 계약(contract) | 기능 확장/수정할 때 |
| **Layer 3: UI/UX 기획서** | `docs/SPEC.md` | 사용자 경험 의도, 시나리오, UX 원칙 | 신규 기능 기획, UX 판단 |

**원칙:**
- Layer 1, 2는 코드만으로 파악 불가능하거나 역추출 비용이 높은 정보만 기술
- Layer 3는 코드에 존재하지 않는 기획 의도, 사용자 시나리오, UX 정책을 기술
- 코드가 이미 설명하는 단일 기능의 구현 상세는 문서화하지 않음

---

# Layer 1: 개발 규칙

## 개발 원칙 (CRITICAL)

### 베타 개발 모드

현재 **베타 개발 중**이므로 아래 원칙을 철저히 준수:

| 원칙 | 설명 |
|------|------|
| **레거시 고려 금지** | 하위 호환성 코드, fallback 로직 작성하지 않음 |
| **코드 품질** | 깔끔하고, 단순하고, 명확한 코드만 허용 |
| **마이그레이션 가능** | 기존 데이터 → 새 형식으로 변환하여 처리 |
| **마이그레이션 불가** | 깔끔하게 포기 (복잡한 호환 코드 작성 금지) |

### 베타 개발 규칙
1. 논의하고 결정한 기획안 전체는 완수하는 것을 목표로 한다. 기획안의 업무는 기능/역할/업무편의에 따라 분리한다. 분리할 필요가 없으면 하나의 업무만으로 처리한다.
2. 분리한 것은 다음에 순서에 따라 처리한다.
    - 개발한다.
    - 유닛테스트/통합테스트 전체 수행한다.
    - 정상이면 커밋 및 푸시한다.
    - 모든 업무가 완료될 때까지 2를 반복한다.
3. 모든 업무가 완료되면, 통합테스트를 수행하여 필요한 개선을 수행한다.
4. 기획안을 기반으로 코드리뷰를 한다.
5. 보고서를 제출하고 봇을 재실행한다.

### 테스트 범위
- 텔래그램의 풀링을 직접 할 수는 없으므로 목킹한다.
- 그 이외의 모든 리소스는 자유롭게 사용 가능하다.
  - 리포지토리, 클로드 cli, 텔래그램에 메시지 보내기 등.
  - 풀링 이외에는 모든 것이 허용된다.
- 개발의 범위가 큰 경우 랄프/병렬/리소스 최대로 처리한다.

### 테스트 작성 규칙
- **개별 기능 테스트**: 각 콜백/핸들러의 단위 동작은 반드시 테스트한다.
- **멀티 스탭 해피케이스**: 인라인 키보드 → 콜백 → ForceReply 등 여러 단계를 거치는 플로우는 **해피케이스 1개 이상** end-to-end 테스트를 작성한다.
  - 예: 워크스페이스 스케줄 등록 (`ws:schedule` → 시간선택 → 분선택 → 모델선택 → 메시지입력 → 등록완료)
  - 예: 세션 삭제 (`sess:del` → 확인 → 삭제 실행)
  - 예: 스케줄러 시간 변경 (`sched:chtime` → 시간선택 → 분선택 → 완료`)
- **테스트 파일 위치**: `tests/test_callback_flows.py` (멀티 스탭 콜백 플로우 전용)

### 금지 패턴

```python
# ❌ 금지: 레거시 fallback
if new_system_available():
    use_new()
else:
    use_legacy()  # 이런 코드 작성 금지

# ❌ 금지: send_chat_action 사용 금지 (타임아웃 원인)
await context.bot.send_chat_action(chat_id=chat_id, action="typing")  # 절대 사용 금지!

# ✅ 권장: 새 시스템만 사용
def process():
    return new_system.process()  # 단순명확
```

### 데이터 저장소

- **SQLite Repository** 단일 사용 (`.data/bot.db`)
- JSON 파일 기반 저장 금지

### DDL 관리 (CRITICAL)

- **`src/repository/schema.sql`** = DB 스키마의 **단일 소스 (Single Source of Truth)**
- 테이블 추가/변경 시 `schema.sql`만 수정
- `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`로 멱등성 보장
- 별도 마이그레이션 시스템 없음 (로컬 싱글유저 봇이므로 불필요)
- 봇 시작 시 `init_schema()`가 `schema.sql` 실행 → 테이블 자동 생성

```
봇 시작 → get_connection() → init_schema(schema.sql) → Repository 생성
```

| 상황 | 처리 방법 |
|------|----------|
| 새 테이블 추가 | `schema.sql`에 `CREATE TABLE IF NOT EXISTS` 추가 |
| 컬럼 추가 | `schema.sql` 수정 + 기존 DB는 재생성 |
| 테이블 구조 변경 | `schema.sql` 수정 + 기존 DB 재생성 |
| 최초 실행 | `schema.sql`이 모든 테이블 자동 생성 |

## 개발 루틴

### 실행 스크립트 (run.sh)
```bash
./run.sh start    # 봇 시작
./run.sh stop     # 봇 중지
./run.sh restart  # 봇 재시작
./run.sh status   # 상태 확인
./run.sh log      # 로그 보기
./run.sh test     # 테스트 실행
```

### 완료 루틴 (CRITICAL - 모든 단계 필수)
```bash
./run.sh test                             # 1. 테스트
git add -A && git commit -m "type: msg"   # 2. 커밋
git push origin main                      # 3. 푸시
./run.sh restart                          # 4. 재시작
source venv/bin/activate && \
  python -m src.notify "변경1" -- "file1" # 5. 리포트 (필수!)
```

**리포트 형식:**
```bash
source venv/bin/activate && python -m src.notify "주요변경1" "변경2" -- "file1.py" "file2.py"
```
- `--` 앞: 변경사항 설명 (여러 개 가능)
- `--` 뒤: 수정된 파일 목록

## 커밋 컨벤션

| Type | 용도 |
|------|------|
| `feat` | 새 기능 |
| `fix` | 버그 수정 |
| `refactor` | 리팩토링 |
| `docs` | 문서 |
| `test` | 테스트 |
| `chore` | 기타 |

```
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```

## 코드 규칙

### 구조
```
src/
├── main.py                    # 봇 진입점, 핸들러 등록
├── config.py                  # 환경변수 기반 설정 (Pydantic Settings)
├── constants.py               # 전역 상수 (모델, 시간, 제한값)
├── notify.py                  # 개발 리포트 CLI
├── lock.py                    # 파일 락 (싱글톤)
├── supervisor.py              # 프로세스 감시
├── scheduler_manager.py       # 통합 job_queue 매니저
├── logging_config.py          # 로깅 설정
│
├── bot/
│   ├── handlers/              # 명령어/콜백/메시지 핸들러
│   ├── middleware.py           # 인증/권한 데코레이터
│   ├── formatters.py          # 메시지 포맷팅 (마크다운→HTML, truncation)
│   ├── session_queue.py       # 세션 큐 매니저
│   ├── constants.py           # UI 상수 (이모지, 제한값)
│   └── prompts/               # 시스템 프롬프트
│
├── claude/
│   └── client.py              # Claude CLI 래퍼
│
├── plugins/
│   └── loader.py              # Plugin 기본 클래스 + PluginLoader
│
├── repository/
│   ├── database.py            # DB 커넥션 싱글톤
│   ├── repository.py          # 통합 Repository (모든 데이터 접근)
│   ├── schema.sql             # DDL (Single Source of Truth)
│   └── adapters/              # 도메인별 어댑터
│       ├── schedule_adapter.py
│       └── workspace_adapter.py
│
└── services/
    ├── session_service.py     # 세션 생명주기
    ├── message_service.py     # 메시지 처리
    └── schedule_service.py    # 스케줄 CRUD + 실행
```

**호출 흐름:** Handler → Service → Repository → SQLite

### 네이밍
- 파일: `snake_case.py`
- 클래스: `PascalCase`
- 함수/변수: `snake_case`
- 상수: `UPPER_SNAKE_CASE`

### 비동기
- I/O → `async/await`
- subprocess → `asyncio.create_subprocess_exec`

### 테스트 코드
- 모듈: 테스트 의도 설명 (docstring)
- 메서드: 간단한 설명 (docstring)

## 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `TELEGRAM_TOKEN` | (필수) | 봇 토큰 |
| `ALLOWED_CHAT_IDS` | (빈값) | 허용 채팅 ID (쉼표 구분) |
| `ADMIN_CHAT_ID` | `0` | 관리자 알림/리포트 수신 chat ID |
| `AI_COMMAND` | `claude` | AI CLI 명령어 |
| `SESSION_TIMEOUT_HOURS` | `24` | 세션 만료 시간 |
| `RESPONSE_NOTIFY_SECONDS` | `60` | 응답 대기 알림까지 시간(초) |
| `SESSION_LIST_AI_SUMMARY` | `false` | 세션 목록에서 AI 요약 사용 여부 |
| `REQUIRE_AUTH` | `true` | 인증 필요 여부 |
| `AUTH_SECRET_KEY` | (조건부 필수) | 인증 키 (`REQUIRE_AUTH=true` 시 필수) |
| `AUTH_TIMEOUT_MINUTES` | `30` | 인증 유효 시간 |
| `WORKING_DIR` | (없음) | 봇 작업 디렉토리 (미설정 시 프로젝트 루트) |
| `ALLOWED_PROJECT_PATHS` | `~/AiSandbox/*,~/Projects/*` | 워크스페이스 허용 디렉토리 (glob 패턴, 쉼표 구분) |

## 프로세스 관리 (CRITICAL)

### 싱글톤 락 시스템

봇은 중복 실행 방지를 위해 파일 락 시스템 사용:

| 락 파일 | 용도 |
|---------|------|
| `/tmp/telegram-bot.lock` | main.py 싱글톤 |
| `/tmp/telegram-bot-supervisor.lock` | supervisor 싱글톤 |

### 프로세스 관리 규칙 (CRITICAL)

**반드시 `./run.sh` 명령어만 사용할 것!**

| 상황 | 올바른 방법 | 금지 |
|------|-------------|------|
| 봇 재시작 | `./run.sh restart` | `kill -9 PID` |
| 봇 중지 | `./run.sh stop` | `pkill -f src.main` |
| 중복 프로세스 정리 | `./run.sh restart` | 수동 kill |

### 왜 수동 kill이 위험한가?

1. **`kill -9`는 시그널 핸들러 무시** → 락 파일 미정리
2. **zsh에서 `kill -9 PID`가 실패할 수 있음** → 에러 무시되어 인지 못함
3. **Supervisor가 자식 프로세스 재생성** → 중복 발생

## 보호 메커니즘

| 계층 | 위협 | 보호 |
|------|------|------|
| 접근 | 무단 사용 | `ALLOWED_CHAT_IDS` |
| 인증 | 권한 탈취 | `AuthManager` (30분 TTL) |
| 동시성 | Race Condition | `_user_locks` |
| 리소스 | 요청 폭주 | `_user_semaphores` (3개) |
| 리소스 | 좀비 태스크 | Watchdog (30분) |
| 데이터 | 파일 손상 | Atomic Write |
| DoS | 긴 메시지 | `MAX_MESSAGE_LENGTH` (4096) |

## 로깅 시스템

### MDC 스타일 (contextvars)

요청별 컨텍스트 유지 (`trace_id`, `user_id`, `session_id`):

```
22:15:30.123 | INFO | 123456789 | a1b2c3d4 | 8f9e0d1c | handlers:handle_message:1364 | 메시지 수신
              ↑ level  ↑ user_id   ↑ session  ↑ trace_id   ↑ location
```

## 금지

- `.env` 커밋 금지
- `.data/` 커밋 금지
- 토큰 하드코딩 금지
- **수동 `kill -9` 사용 금지** → `./run.sh restart` 사용

---

# Layer 2: 개발 인터페이스

## 플러그인 아키텍처

### 디렉토리 구조
```
plugins/
├── builtin/               # Git 관리 (내장 플러그인)
│   ├── todo/
│   │   ├── __init__.py
│   │   ├── plugin.py      # 콜백, ForceReply, 스케줄 구현체
│   │   └── scheduler.py   # 투두 전용 스케줄 액션
│   ├── memo/
│   │   ├── __init__.py
│   │   └── plugin.py
│   └── weather/
│       ├── __init__.py
│       └── plugin.py
└── custom/                # Git 무시 (개인용)
    └── my_plugin/
        ├── __init__.py
        └── plugin.py
```

### 플러그인 클래스 구조

```python
from src.plugins.loader import Plugin, PluginResult, ScheduledAction

class MyPlugin(Plugin):
    name = "myplugin"                    # 필수: /myplugin 명령어로 사용
    description = "플러그인 설명"         # 필수: /plugins에 표시
    usage = (                            # 필수: /myplugin 실행 시 표시
        "<b>사용법</b>\n\n"
        "<code>명령어1</code> - 설명\n"
        "<code>명령어2</code> - 설명"
    )

    PATTERNS = [r"패턴1", r"패턴2"]          # 트리거 패턴 (정규식)
    EXCLUDE_PATTERNS = [r"(란|이란)\s*뭐"]   # 제외 패턴 → AI에게 넘김

    async def can_handle(self, message: str, chat_id: int) -> bool: ...
    async def handle(self, message: str, chat_id: int) -> PluginResult: ...

    # --- 선택 API ---
    # handle_callback(callback_data, chat_id) → dict    # 인라인 버튼 콜백
    # handle_force_reply(message, chat_id) → dict       # ForceReply 응답
    # get_scheduled_actions() → list[ScheduledAction]   # 스케줄 액션 목록
    # execute_scheduled_action(action_name, chat_id) → str  # 스케줄 실행
```

참고 구현체: `plugins/builtin/todo/` (콜백+ForceReply+스케줄), `plugins/builtin/memo/` (간단한 CRUD)

### 플러그인 규칙 (CRITICAL)

1. **제외 패턴 필수**: 자연어 명령어는 AI 질문과 충돌 가능
   - "메모란 뭐야" → 메모 플러그인이 아닌 AI가 답변해야 함
2. **안전한 로딩**: 플러그인 로드 실패 시 봇은 계속 동작 (try-catch 격리)
3. **데이터 저장**: `self.repository` (Repository 인스턴스, PluginLoader가 주입)
4. **검증 후 배포**: `python -m py_compile plugins/custom/my.py`

### 플러그인 데이터 저장 확장

플러그인이 새 데이터를 저장하려면:
1. `src/repository/schema.sql`에 `CREATE TABLE IF NOT EXISTS` 추가
2. `src/repository/repository.py`에 CRUD 메서드 추가
3. 플러그인에서 `self.repository.xxx()` 호출

### 콜백 처리 패턴

플러그인이 인라인 버튼을 사용하려면:

1. `CALLBACK_PREFIX = "myplugin:"` 정의 (기존 prefix와 충돌 금지)
2. `handle_callback(callback_data, chat_id) → dict` 구현
3. `callback_handlers.py`의 `handle_callback()` 메서드에 prefix 라우팅 분기 추가

**등록된 콜백 prefix (충돌 금지):**

| Prefix | 대상 | 등록 위치 |
|--------|------|----------|
| `td:` | 투두 플러그인 | `callback_handlers.py` |
| `memo:` | 메모 플러그인 | `callback_handlers.py` |
| `weather:` | 날씨 플러그인 | `callback_handlers.py` |
| `sess:` | 세션 관리 | `callback_handlers.py` |
| `sched:` | 스케줄러 | `callback_handlers.py` |
| `ws:` | 워크스페이스 | `callback_handlers.py` |
| `sq:` | 세션 큐 (충돌 처리) | `callback_handlers.py` |
| `tasks:` | 태스크 현황 | `callback_handlers.py` |

**ForceReply 마커 (충돌 금지):**

| 마커 | 용도 | 라우팅 위치 |
|------|------|------------|
| `td:add` | 투두 추가 | `message_handlers.py` |
| `memo_add` | 메모 추가 | `message_handlers.py` |
| `sess_name:{model}` | 세션 이름 입력 | `message_handlers.py` |
| `sess_rename:{session_id}` | 세션 이름 변경 | `message_handlers.py` |
| `schedule_input` | 스케줄 메시지 입력 | `message_handlers.py` |
| `_ws_pending` | 워크스페이스 플로우 | `message_handlers.py` (dict 기반) |

## 메시지 처리 흐름

```
사용자 메시지 도착
    │
    ▼
[1] 명령어 (/command)
    │ CommandHandler가 먼저 처리. 즉시 응답 (Claude 호출 없음)
    │
    ▼ 명령어 아님
[2] ForceReply 응답 감지
    │ reply_to_message.text에서 마커 추출 → 해당 핸들러로 라우팅
    │
    ▼ ForceReply 아님
[3] 플러그인 (자연어 패턴)
    │ plugins.process_message() 순회
    │ can_handle() → handle() → 즉시 응답
    │
    ▼ 플러그인 매칭 없음
[4] Claude AI (백그라운드 처리)
```

## 텔레그램 명령어 규칙

### 언더바(_) 규칙 (CRITICAL)

텔레그램은 **언더바로 연결된 문자열**을 하나의 명령어로 인식:

| 입력 | 클릭 가능한 부분 | 이유 |
|------|-----------------|------|
| `/new_opus` | `/new_opus` 전체 | 언더바로 연결 → 하나의 명령어 |
| `/new opus` | `/new`만 | 공백 → 별개의 단어 |
| `/s_12345678` | `/s_12345678` 전체 | 동적 세션 ID 포함 가능 |

### 명령어 설계 원칙

1. **고정 명령어**: 언더바로 연결 (`/new_opus`, `/model_haiku`)
2. **동적 파라미터**: 언더바 + ID (`/s_{id}`, `/h_{id}`, `/d_{id}`)
3. **단축 명령어**: 자주 쓰는 명령어 (`/sl` = `/session_list`, `/nw` = `/new_workspace`, `/ws` = `/workspace`)

## 워크스페이스 세션

로컬 디렉토리에 바인딩된 세션. `--cwd`로 디렉토리 지정, `--append-system-prompt`로 텔레그램 포맷 규칙 주입.

| 레이어 | 소스 | 역할 |
|--------|------|------|
| 워크스페이스 규칙 | `cwd`의 CLAUDE.md | 코드 스타일, 빌드 명령, 커밋 규칙 |
| 텔레그램 규칙 | `--append-system-prompt` | HTML 포맷, 간결한 응답 |

## 스케줄 타입

| 타입 | 설명 |
|------|------|
| `claude` | 일반 스케줄 (새 세션에서 실행) |
| `workspace` | 워크스페이스 스케줄 (경로의 CLAUDE.md 적용) |
| `plugin` | 플러그인 액션 스케줄 (모델/메시지 불필요) |
