# AI Bot

AI CLI를 텔레그램으로 사용하는 봇. 세션 유지 지원.

## 기능

- 유저별 멀티 세션 지원
- 세션 전환 및 히스토리
- AI 기반 세션 요약
- 비동기 아키텍처
- 선택적 인증

## 시작하기

### 1. 설치

```bash
python -m venv venv
source venv/bin/activate
pip install -e .
```

### 2. 설정

```bash
cp .env.example .env
# .env 파일 수정
```

### 3. 실행

```bash
python -m src.main
```

## 명령어

| 명령어 | 설명 |
|--------|------|
| `/start` | 봇 상태 |
| `/help` | 도움말 |
| `/auth <키>` | 인증 (필요 시) |
| `/new` | 새 세션 |
| `/session` | 현재 세션 정보 |
| `/session_list` | 세션 목록 |
| `/s_<id>` | 세션 전환 |

## 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `TELEGRAM_TOKEN` | (필수) | 봇 토큰 |
| `ALLOWED_CHAT_IDS` | (빈값) | 허용 채팅 ID |
| `MAINTAINER_CHAT_ID` | (빈값) | 개발 알림 수신 |
| `AI_COMMAND` | `claude` | AI CLI 명령어 |
| `SESSION_TIMEOUT_HOURS` | `24` | 세션 만료 |
| `REQUIRE_AUTH` | `true` | 인증 필요 여부 |

## 개발

```bash
pip install -e ".[dev]"
pytest
```

## 구조

```
src/
├── main.py           # 엔트리포인트
├── config.py         # 설정
├── notify.py         # 개발 알림
├── bot/              # 텔레그램
│   ├── handlers.py
│   ├── middleware.py
│   └── formatters.py
└── claude/           # AI CLI
    ├── client.py
    └── session.py
```

## 라이선스

MIT
