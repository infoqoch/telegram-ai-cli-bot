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
python -m src.notify "변경1" -- "file1"   # 5. 리포트 (필수!)
```

**리포트 형식:**
```bash
python -m src.notify "주요변경1" "변경2" -- "file1.py" "file2.py"
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
    │   └── memo.py
    └── custom/                # Git 무시 ❌ (개인용)
        └── my_plugin.py
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
| `plugins/builtin/memo.py` | 참고용 플러그인 구현체 |
| `src/bot/handlers.py` | 플러그인 호출 위치 (process_message) |

## 금지

- `.env` 커밋 금지
- `.data/` 커밋 금지
- 토큰 하드코딩 금지
