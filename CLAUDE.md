# AI Bot - 프로젝트 규칙

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
├── main.py, config.py, notify.py
├── bot/     # 텔레그램 (handlers, middleware, formatters)
└── claude/  # AI CLI (client, session)
```

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
| `MAINTAINER_CHAT_ID` | (빈값) | 개발 리포트 수신 |
| `AI_COMMAND` | `claude` | AI CLI 명령어 |
| `SESSION_TIMEOUT_HOURS` | `24` | 세션 만료 시간 |
| `REQUIRE_AUTH` | `true` | 인증 필요 여부 |
| `AUTH_SECRET_KEY` | (조건부 필수) | 인증 키 (`REQUIRE_AUTH=true` 시 필수) |
| `AUTH_TIMEOUT_MINUTES` | `30` | 인증 유효 시간 |
## 플러그인 아키텍처

### 디렉토리 구조
```
telegram-claude-bot/
├── src/plugins/
│   └── loader.py              # Plugin 기본 클래스 + PluginLoader
└── plugins/
    ├── builtin/               # Git 관리 ✅ (내장 플러그인)
    │   ├── memo/
    │   │   ├── __init__.py
    │   │   └── plugin.py
    │   └── weather/
    │       ├── __init__.py
    │       └── plugin.py
    └── custom/                # Git 무시 ❌ (개인용)
        └── my_plugin/
            ├── __init__.py
            └── plugin.py
```

### 플러그인 클래스 구조

```python
from src.plugins.loader import Plugin, PluginResult

class MyPlugin(Plugin):
    name = "myplugin"                    # 필수: /myplugin 명령어로 사용
    description = "플러그인 설명"         # 필수: /plugins에 표시
    usage = (                            # 필수: /myplugin 실행 시 표시
        "📌 <b>사용법</b>\n\n"
        "• <code>명령어1</code> - 설명\n"
        "• <code>명령어2</code> - 설명"
    )

    # 트리거 패턴 (정규식)
    PATTERNS = [r"패턴1", r"패턴2"]

    # 제외 패턴 - 매칭되면 AI에게 넘김
    EXCLUDE_PATTERNS = [
        r"(란|이란|가|이)\s*(뭐|무엇)",  # "X란 뭐야" → AI
        r"영어로|번역",                   # 번역 요청 → AI
    ]

    async def can_handle(self, message: str, chat_id: int) -> bool:
        # 1. 제외 패턴 먼저 체크
        for pattern in self.EXCLUDE_PATTERNS:
            if re.search(pattern, message):
                return False  # AI에게 넘김
        # 2. 트리거 패턴 체크
        for pattern in self.PATTERNS:
            if re.search(pattern, message):
                return True
        return False

    async def handle(self, message: str, chat_id: int) -> PluginResult:
        # 처리 로직
        return PluginResult(handled=True, response="응답")
```

### 처리 흐름 (AI 호출 안함 = 빠름)

```
사용자 메시지
    ↓
handlers.py: plugins.process_message()
    ↓
각 플러그인.can_handle() 순회
    ├─ 제외 패턴 매칭 → return False (AI에게 넘김)
    └─ 트리거 패턴 매칭 → return True
    ↓
플러그인.handle() 실행
    ↓
PluginResult.response 즉시 반환 (Claude 호출 없음)
```

### 명령어 체계

| 명령어 | 설명 |
|--------|------|
| `/plugins` | 전체 플러그인 목록 |
| `/플러그인명` | 해당 플러그인 사용법 (예: `/memo`) |

### 플러그인 규칙 (CRITICAL)

1. **제외 패턴 필수**: 자연어 명령어는 AI 질문과 충돌 가능
   - "메모란 뭐야" → 메모 플러그인이 아닌 AI가 답변해야 함
   - `EXCLUDE_PATTERNS`로 질문/번역 등 제외

2. **안전한 로딩**: 플러그인 로드 실패 시 봇은 계속 동작
   - 각 플러그인은 try-catch로 격리
   - 실패한 플러그인만 스킵

3. **데이터 저장**: `self.get_data_dir(self._base_dir)`
   - 경로: `.data/{plugin_name}/`
   - 예: `.data/memo/12345.json`

4. **검증 후 배포**: custom 플러그인 작성 시
   - `python -m py_compile plugins/custom/my.py`
   - 검증 실패해도 기존 봇 정상 동작

### 참고 파일 (Claude 개발 시 확인)

| 파일 | 용도 |
|------|------|
| `src/plugins/loader.py` | Plugin 기본 클래스, PluginLoader |
| `plugins/builtin/memo/` | 참고용 플러그인 구현체 |
| `src/bot/handlers.py` | 플러그인 호출 위치 (process_message) |

## 메시지 처리 아키텍처

### 처리 우선순위

```
사용자 메시지 도착
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ 1️⃣ 명령어 (/command)                                    │
│    • /start, /help, /new, /session, /m 등              │
│    • CommandHandler가 먼저 처리                         │
│    • 즉시 응답 (Claude 호출 없음)                        │
└─────────────────────────────────────────────────────────┘
    │ 명령어 아님
    ▼
┌─────────────────────────────────────────────────────────┐
│ 2️⃣ 플러그인 (자연어 패턴)                                │
│    • "메모해줘", "오늘 할일", "날씨" 등                   │
│    • plugins.process_message() 순회                     │
│    • can_handle() → handle() → 즉시 응답               │
│    • EXCLUDE_PATTERNS으로 AI 질문 제외                  │
└─────────────────────────────────────────────────────────┘
    │ 플러그인 매칭 없음
    ▼
┌─────────────────────────────────────────────────────────┐
│ 3️⃣ Claude AI                                            │
│    • 일반 대화, 질문, 코딩 요청 등                       │
│    • Fire-and-Forget 패턴 (백그라운드 처리)             │
│    • Semaphore로 동시 3개 제한                          │
└─────────────────────────────────────────────────────────┘
```

### 처리자별 특징

| 처리자 | 응답 속도 | Claude 호출 | 히스토리 기록 |
|--------|----------|-------------|--------------|
| 명령어 | 즉시 | ❌ | ❌ |
| 플러그인 | 즉시 | ❌ | ✅ `plugin:{name}` |
| Claude | 1초~수분 | ✅ | ✅ `claude` |
| 거절됨 | 즉시 | ❌ | ❌ |

### 동시 요청 처리

```
Semaphore = 3 (유저당)

요청1 → 처리 중 (slot 1)
요청2 → 처리 중 (slot 2)
요청3 → 처리 중 (slot 3)
요청4 → ⚠️ 거절 (메시지 표시 후 버림)
요청5 → ⚠️ 거절
(요청1 완료)
요청6 → 처리 중 (slot 1)
```

### 장시간 작업 알림

| 경과 시간 | 동작 |
|----------|------|
| 0~5분 | 처리 중 (typing 표시) |
| 5분 | "⏳ 작업이 걸리고 있어요" 알림 |
| 완료 | "✅ 작업 완료! (Xm Ys 소요)" + 응답 |
| 30분 | Watchdog이 좀비 태스크 정리 |

### 히스토리 구조

```python
HistoryEntry = {
    "message": str,      # 사용자 메시지
    "timestamp": str,    # ISO format
    "processed": bool,   # 처리 완료 여부
    "processor": str,    # "command" | "plugin:{name}" | "claude" | "rejected"
}
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

1. **고정 명령어**: 언더바로 연결
   - ✅ `/new_opus`, `/new_sonnet`, `/new_haiku`
   - ❌ `/new opus` (opus가 클릭 불가)

2. **동적 파라미터**: 언더바 + ID
   - ✅ `/s_12345678` (세션 전환)
   - ✅ `/h_12345678` (히스토리)
   - ✅ `/d_12345678` (삭제)

3. **단축 명령어**: 자주 쓰는 명령어
   - `/sl` = `/session_list`
   - `/m` = 매니저 모드

### 현재 명령어 목록

| 명령어 | 설명 | 단축 |
|--------|------|------|
| `/session_list` | 세션 목록 | `/sl` |
| `/new_opus` | Opus 세션 생성 | - |
| `/new_sonnet` | Sonnet 세션 생성 | - |
| `/new_haiku` | Haiku 세션 생성 | - |
| `/s_{id}` | 세션 전환 | - |
| `/h_{id}` | 히스토리 보기 | - |
| `/d_{id}` | 세션 삭제 | - |
| `/new_project` | 프로젝트 세션 생성 | `/np` |

## 프로젝트 세션

로컬 프로젝트 디렉토리에 바인딩된 세션. 해당 프로젝트의 CLAUDE.md 규칙을 따르면서 텔레그램 포맷으로 응답.

### 사용법

```
/new_project 경로 [모델] [이름]
/np ~/AiSandbox/my-app opus 마이앱
```

### 동작 방식

| 레이어 | 소스 | 역할 |
|--------|------|------|
| 프로젝트 규칙 | `cwd`의 CLAUDE.md | 코드 스타일, 빌드 명령, 커밋 규칙 |
| 텔레그램 규칙 | `--append-system-prompt` | HTML 포맷, 간결한 응답 |

### 매니저 모드에서

```
"my-app 프로젝트 세션 만들어"
→ [ACTION:CREATE_PROJECT:sonnet:~/Projects/my-app:my-app]
```

### 허용 디렉토리

`.env`에서 설정 (기본값):
```
ALLOWED_PROJECT_PATHS=/Users/bae/AiSandbox/*,/Users/bae/Projects/*
```

## 금지

- `.env` 커밋 금지
- `.data/` 커밋 금지
- 토큰 하드코딩 금지
