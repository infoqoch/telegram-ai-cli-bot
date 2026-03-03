# Telegram Claude Bot

**Claude Code를 텔레그램에서. API 키 없이.**

터미널 없이 스마트폰으로 Claude Code와 대화하세요.

---

## 왜 이 프로젝트인가?

| | |
|---|---|
| **API 키 불필요** | Claude CLI만 설치되어 있으면 바로 동작 - 추가 비용 없음 |
| **어디서든** | 출퇴근길, 카페, 침대에서 텔레그램으로 코딩 대화 |
| **멀티 세션** | 프로젝트별 독립 대화, 모델 선택(Opus/Sonnet/Haiku) |
| **AI 매니저** | 자연어로 세션 관리 - "주식돌이 오푸스로 만들어줘" |
| **플러그인** | Claude 호출 없이 빠른 응답 - 메모, 날씨 등 확장 가능 |
| **보안** | 허용된 ID만 접근 + 선택적 인증 |

---

## 기술적 하이라이트

### 2-Track 응답 시스템

AI 응답은 느립니다(수십 초~수 분). 모든 요청을 Claude에 보내면 사용자 경험이 나빠집니다.

```
사용자 메시지
    │
    ├─▶ [Track 1] 플러그인 매칭 → 즉시 응답 (0.1초)
    │       "메모해줘: 장보기"  → 저장 완료
    │       "서울 날씨"        → Open-Meteo API
    │
    └─▶ [Track 2] Claude CLI  → 백그라운드 처리 (수십 초)
            "코드 리뷰해줘"    → Fire-and-Forget
```

플러그인이 처리 가능하면 Claude를 호출하지 않아 빠르고, 처리 불가하면 Claude로 넘깁니다.

### 세션별 커스터마이징

세션마다 독립적인 설정이 가능합니다:

| 기능 | 설명 |
|------|------|
| **이름 지정** | `/new opus 코딩도우미` - 세션에 이름 부여 |
| **모델 선택** | opus/sonnet/haiku 중 선택 |
| **모델 변경** | `/model sonnet` - 기존 세션 모델 변경 |
| **세션 전환** | `/s_abc123` - 다른 세션으로 전환 |

### Fire-and-Forget 아키텍처

```python
# 핸들러는 즉시 반환 → 텔레그램 응답 지연 없음
task = asyncio.create_task(self._process_claude_request(...))
self._register_task(task, user_id, session_id, trace_id)
# Claude 응답(수 분)을 기다리지 않고 다음 메시지 처리 가능
```

### ACTION 패턴 시스템

매니저 세션이 자연어를 파싱하여 실제 작업 수행:

```
사용자: "abc123 삭제해줘"
매니저: "삭제할게요! [ACTION:DELETE:abc123]"
봇: [ACTION:DELETE:abc123] 패턴을 파싱하여 세션 삭제 메서드 호출
```

### 보호 메커니즘

| 위협 | 보호 메커니즘 |
|------|---------------|
| 무단 접근 | `ALLOWED_CHAT_IDS` 화이트리스트 |
| 요청 폭주 | 유저별 Semaphore (동시 3개 제한) |
| 좀비 태스크 | Watchdog 루프 (30분 타임아웃, 자동 kill) |
| 파일 손상 | Atomic Write (임시파일 → replace) |

---

## 빠른 시작

### 1. 사전 준비

- **Python 3.11+**
- **Claude CLI** 설치 및 로그인
  ```bash
  claude --version  # 설치 확인
  ```

### 2. 텔레그램 봇 생성

1. [@BotFather](https://t.me/BotFather)에서 `/newbot`
2. **API 토큰** 복사

### 3. 설치 및 실행

```bash
git clone https://github.com/infoqoch/telegram-claude-bot.git
cd telegram-claude-bot
python -m venv venv && source venv/bin/activate
pip install -e .

cp .env.example .env
# .env 수정: TELEGRAM_TOKEN, ALLOWED_CHAT_IDS

./run.sh start     # 봇 시작
./run.sh status    # 상태 확인
./run.sh log       # 로그 보기
```

> **채팅 ID 확인**: 봇 시작 후 `/chatid` 입력

---

## 사용법

### 기본 대화

메시지를 보내면 Claude가 응답합니다.

### 세션 관리

| 명령어 | 설명 |
|--------|------|
| `/new opus 프로젝트명` | 새 Opus 세션 (이름 지정) |
| `/session` | 현재 세션 정보 |
| `/session_list` | 전체 세션 목록 |
| `/s_abc123` | 세션 전환 |
| `/model opus` | 모델 변경 |

### 매니저 모드

자연어로 세션 관리:

```
/m                     → 매니저 모드 진입
"주식분석 오푸스로 만들어" → 새 세션 생성
"abc123 삭제해"         → 세션 삭제
```

### 플러그인

Claude 호출 없이 즉시 응답:

```
메모해줘: 장보기 목록    → 저장 (즉시)
메모 보여줘             → 조회 (즉시)
서울 날씨               → Open-Meteo API (즉시)
```

> `plugins/custom/`에 직접 플러그인 추가 가능

---

## 문서

| 문서 | 내용 |
|------|------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 시스템 설계, 프로젝트 구조 |
| [CLAUDE.md](CLAUDE.md) | AI 개발 가이드, 플러그인 작성법 |

---

## 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `TELEGRAM_TOKEN` | (필수) | 봇 토큰 |
| `ALLOWED_CHAT_IDS` | (전체허용) | 허용 채팅 ID |
| `REQUIRE_AUTH` | `true` | 인증 필요 여부 |
| `AUTH_SECRET_KEY` | - | 인증 키 |
| `SESSION_TIMEOUT_HOURS` | `24` | 세션 만료 시간 |

---

## 라이선스

MIT
