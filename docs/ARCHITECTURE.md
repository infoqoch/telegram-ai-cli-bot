# 아키텍처

> 시스템의 내부 구조와 설계를 설명합니다.

---

## 1. 시스템 개요

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────┐
│  Telegram   │────▶│   Bot (Python)  │────▶│ Claude CLI  │
│   Client    │◀────│                 │◀────│  (Local)    │
└─────────────┘     └─────────────────┘     └─────────────┘
                            │
                    ┌───────┴───────┐
                    ▼               ▼
            ┌───────────────┐ ┌───────────────┐
            │ sessions.json │ │   Plugins     │
            └───────────────┘ └───────────────┘
```

### 핵심 설계 원칙

| 원칙 | 설명 |
|------|------|
| **CLI 래퍼** | Claude API 직접 호출 ❌, CLI subprocess 실행 ✅ |
| **Fire-and-Forget** | 핸들러는 즉시 반환, 응답은 백그라운드에서 전송 |
| **세션 = Claude session_id** | 자체 UUID 생성 ❌, Claude의 session_id를 그대로 사용 |
| **2-Track 응답** | 플러그인(즉시) vs Claude(백그라운드) |

---

## 2. 프로젝트 구조

```
src/
├── main.py              # 엔트리포인트
├── config.py            # Pydantic 설정
├── logging_config.py    # loguru + contextvars (MDC)
├── bot/
│   ├── handlers.py      # 텔레그램 핸들러 (Fire-and-Forget)
│   ├── middleware.py    # 인증/권한 데코레이터
│   ├── constants.py     # ACTION 패턴, 상수
│   ├── prompts/         # 시스템 프롬프트
│   └── formatters.py    # 메시지 포맷팅
├── claude/
│   ├── client.py        # Claude CLI 비동기 클라이언트
│   └── session.py       # 세션 저장소 (Atomic Write)
└── plugins/
    └── loader.py        # 플러그인 시스템

plugins/
├── builtin/             # 내장 플러그인 (memo, weather)
└── custom/              # 사용자 플러그인 (git ignored)
```

---

## 3. 핵심 컴포넌트

### 3.1 BotHandlers (`src/bot/handlers.py`)

텔레그램 메시지를 처리하는 핸들러 클래스.

```python
class BotHandlers:
    _user_locks: dict[str, Lock]           # 세션 생성 시 Race Condition 방지
    _user_semaphores: dict[str, Semaphore] # 동시 요청 제한 (최대 3개)
    _active_tasks: dict[int, TaskInfo]     # 실행 중인 태스크 추적
    _watchdog_task: Task                   # 좀비 태스크 정리 루프
```

**동시성 처리:**
- `_user_locks`: 세션 생성 구간을 유저별로 직렬화하여 중복 생성 방지
- `_user_semaphores`: 유저당 동시 요청 3개로 제한
- `_watchdog_task`: 30분 초과 태스크 자동 정리

### 3.2 ClaudeClient (`src/claude/client.py`)

Claude CLI를 비동기로 실행하는 클라이언트.

```python
class ClaudeClient:
    async def chat(message, session_id, model) -> ChatResponse
    async def create_session() -> str  # 새 세션 생성
    async def summarize(questions) -> str  # AI 요약
```

### 3.3 SessionStore (`src/claude/session.py`)

세션 데이터를 JSON 파일로 관리. Atomic Write로 파일 손상 방지.

```python
# 데이터 구조
{
    "user_id": {
        "current": "claude_session_id",
        "previous_session": "...",  # /back용
        "sessions": {
            "claude_session_id": {
                "created_at": "...",
                "last_used": "...",
                "history": ["질문1", "질문2"],
                "model": "opus",      # opus/sonnet/haiku
                "name": "주식분석",    # 사용자 지정 이름
                "is_manager": false   # 매니저 세션 여부
            }
        }
    }
}
```

### 3.4 PluginLoader (`src/plugins/loader.py`)

플러그인 시스템. Claude 호출 없이 빠른 응답.

```python
class PluginLoader:
    async def process_message(message, chat_id) -> PluginResult
    # can_handle() → True인 첫 번째 플러그인이 처리
```

---

## 4. 매니저 세션 & ACTION 패턴

### 4.1 매니저 세션

세션 관리를 자연어로 처리하는 특수 세션 (Opus 모델 사용).

```
사용자: /m                    → 매니저 모드 진입
사용자: "주식돌이 오푸스로 만들어"
매니저: "생성! [ACTION:CREATE:opus:주식돌이]"
봇: ACTION 패턴 파싱 → 세션 생성 메서드 호출 → 결과 표시
```

### 4.2 ACTION 패턴

매니저가 출력하면 봇이 실제로 실행하는 명령어:

| 패턴 | 예시 | 동작 |
|------|------|------|
| `[ACTION:DELETE:id]` | `[ACTION:DELETE:abc12345]` | 세션 삭제 |
| `[ACTION:RENAME:id:name]` | `[ACTION:RENAME:abc12345:주식분석]` | 이름 변경 |
| `[ACTION:CREATE:model:name]` | `[ACTION:CREATE:opus:코딩도우미]` | 세션 생성 |
| `[ACTION:SWITCH:id]` | `[ACTION:SWITCH:abc12345]` | 세션 전환 |

### 4.3 컨텍스트 주입

매니저 호출 시 세션 목록과 파일 경로 힌트를 주입:

```python
def _build_manager_context(self, user_id, message):
    return (
        f"{MANAGER_SYSTEM_PROMPT}\n\n"
        f"[Claude 세션 파일 경로]\n"
        f"~/.claude/projects/{project_path}/{{session_id}}.jsonl\n\n"
        f"[현재 세션 목록]\n{sessions_summary}\n\n"
        f"[사용자 요청]\n{message}"
    )
```

---

## 5. 로깅 시스템

### MDC 스타일 (contextvars)

Java의 MDC처럼 요청별 컨텍스트 유지:

```python
# logging_config.py
_trace_id: ContextVar[str] = ContextVar("trace_id", default="-")
_user_id: ContextVar[str] = ContextVar("user_id", default="-")
_session_id: ContextVar[str] = ContextVar("session_id", default="-")
```

### 로그 포맷

```
22:15:30.123 | INFO     | 123456789    | a1b2c3d4 | 8f9e0d1c | handlers:handle_message:1364 | 메시지 수신
               ↑ level    ↑ user_id     ↑ session  ↑ trace_id   ↑ location
```

---

## 6. 보호 메커니즘

| 계층 | 위협 | 보호 |
|------|------|------|
| 접근 | 무단 사용 | `ALLOWED_CHAT_IDS` |
| 인증 | 권한 탈취 | `AuthManager` (30분 TTL) |
| 동시성 | Race Condition | `_user_locks` |
| 리소스 | 요청 폭주 | `_user_semaphores` (3개) |
| 리소스 | 좀비 태스크 | Watchdog (30분) |
| 데이터 | 파일 손상 | Atomic Write |
| DoS | 긴 메시지 | `MAX_MESSAGE_LENGTH` (4096) |
