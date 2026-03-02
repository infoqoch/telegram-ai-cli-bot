# 🤖 AI Bot

**Claude Code를 텔레그램에서. API 키 없이.**

터미널 없이 스마트폰으로 Claude Code와 대화하세요.

---

## ✨ 강점

| | |
|---|---|
| **🔑 API 키 불필요** | Claude CLI만 설치되어 있으면 바로 동작 |
| **📱 어디서든** | 출퇴근길, 카페, 침대에서 텔레그램으로 코딩 |
| **💬 멀티 세션** | 프로젝트별 독립 대화, 언제든 전환 가능 |
| **🔒 내 봇은 나만** | 허용된 ID만 접근 + 선택적 인증 |
| **⚡ 안정적** | 동시 요청 제한, 좀비 태스크 자동 정리 |

---

## 🚀 설치 가이드

### 1. 사전 준비

- **Python 3.11+**
- **Claude CLI** 설치 및 로그인 완료
  ```bash
  # Claude CLI 설치 확인
  claude --version
  ```

### 2. 텔레그램 봇 생성

1. 텔레그램에서 [@BotFather](https://t.me/BotFather) 검색
2. `/newbot` 명령어 입력
3. 봇 이름과 username 설정
4. **API 토큰** 복사 (예: `123456789:ABCdefGHI...`)

> 📖 자세한 내용: [Telegram Bot API 공식 문서](https://core.telegram.org/bots#how-do-i-create-a-bot)

### 3. 내 채팅 ID 확인

**방법 A: 봇에서 직접 확인** (권장)
1. 봇을 시작한 후 `/chatid` 입력
2. 표시된 Chat ID 복사

**방법 B: @userinfobot 사용**
1. 텔레그램에서 [@userinfobot](https://t.me/userinfobot) 검색
2. `/start` 입력
3. **Id** 값 복사 (예: `123456789`)

### 4. 프로젝트 설치

```bash
git clone https://github.com/infoqoch/ai-bot.git
cd ai-bot
python -m venv venv && source venv/bin/activate
pip install -e .
```

### 5. 환경 설정

```bash
cp .env.example .env
```

`.env` 파일 수정:
```env
TELEGRAM_TOKEN=123456789:ABCdefGHI...    # BotFather에서 받은 토큰
ALLOWED_CHAT_IDS=123456789               # 허용할 채팅 ID
REQUIRE_AUTH=false                        # 인증 없이 사용 (선택)
```

### 6. 실행

```bash
./run.sh start     # 봇 시작
./run.sh status    # 상태 확인
./run.sh log       # 로그 보기
./run.sh stop      # 봇 중지
```

---

## 💬 사용법

### 기본 대화
메시지를 보내면 Claude가 응답합니다.
```
나: 파이썬으로 피보나치 함수 만들어줘
봇: [Claude 응답]
```

### 세션 관리

| 명령어 | 설명 |
|--------|------|
| `/new` | 새 세션 시작 |
| `/session` | 현재 세션 정보 |
| `/session_list` | 전체 세션 목록 |
| `/s_abc123` | 해당 세션으로 전환 |
| `/h_abc123` | 해당 세션 히스토리 보기 |
| `/chatid` | 내 채팅 ID 확인 |

### 인증 (선택)

`REQUIRE_AUTH=true` 설정 시:
```
/auth <비밀키>    → 30분간 인증
/status           → 인증 상태 확인
```

### 플러그인

AI 호출 없이 빠르게 동작하는 자연어 명령어.

| 명령어 | 설명 |
|--------|------|
| `/plugins` | 플러그인 목록 |
| `/memo` | 메모 플러그인 사용법 |

**📝 메모 플러그인**
```
장보기 목록 메모해줘   → 저장
메모 보여줘           → 조회
메모 1 삭제           → 삭제
```

> 💡 `plugins/custom/`에 직접 플러그인 추가 가능 (Git 무시됨)

---

## ⚙️ 환경변수

### 필수

| 변수 | 설명 |
|------|------|
| `TELEGRAM_TOKEN` | BotFather에서 발급받은 봇 토큰 |

### 선택

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ALLOWED_CHAT_IDS` | (전체 허용) | 허용할 채팅 ID, 쉼표 구분 |
| `AI_COMMAND` | `claude` | Claude CLI 명령어 |
| `REQUIRE_AUTH` | `true` | 인증 필요 여부 |
| `AUTH_SECRET_KEY` | - | 인증 키 (`REQUIRE_AUTH=true` 시 필수) |
| `SESSION_TIMEOUT_HOURS` | `24` | 세션 만료 시간 |
| `SESSION_LIST_AI_SUMMARY` | `false` | 세션 목록에서 AI 요약 사용 |

---

## 🛠️ 개발

```bash
pip install -e ".[dev]"
./run.sh test     # 테스트 실행 (100개)
```

### 프로젝트 구조

```
src/
├── main.py           # 엔트리포인트
├── config.py         # Pydantic 설정
├── bot/
│   ├── handlers.py   # 텔레그램 핸들러
│   ├── middleware.py # 인증 관리
│   └── formatters.py # 메시지 포맷팅
└── claude/
    ├── client.py     # Claude CLI 클라이언트
    └── session.py    # 세션 저장소
```

---

## 📚 더 알아보기

- [아키텍처 및 설계 결정](docs/ARCHITECTURE.md) - 시스템 구조, 해결한 문제들

---

## 📄 라이선스

MIT
