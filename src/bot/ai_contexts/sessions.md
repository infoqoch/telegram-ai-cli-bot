# 세션 관리 - AI 작업 컨텍스트

## 기능 개요
봇의 대화 세션을 관리합니다. 세션은 AI 대화 히스토리를 유지하며, 여러 세션을 동시에 보관하고 전환할 수 있습니다.

## 관련 DB 테이블

### sessions
| 컬럼 | 설명 |
|------|------|
| id | 세션 고유 ID (UUID) |
| chat_id | Telegram 채팅 ID |
| ai_provider | AI 제공자 (claude / codex) |
| provider_session_id | 외부 CLI 세션 ID |
| model | 사용 모델 프로파일 키 |
| name | 세션 이름 |
| is_current | 현재 활성 세션 여부 (0/1) |
| recycled | 비활성 아카이브 상태 (0/1) |
| deleted | 소프트 삭제 여부 (0/1) |
| created_at | 생성 시각 |
| last_activity_at | 마지막 활동 시각 |

## 세션 생애주기

| 상태 | 조건 | 설명 |
|------|------|------|
| 활성 | recycled=0, deleted=0 | 기본 상태, /sl에 표시됨 |
| 리사이클됨 | recycled=1 | 24시간 비활성 시 자동 보관 |
| 삭제됨 | deleted=1 | 7일 후 소프트 삭제 |

## AI 도움 가능 영역
- 현재 세션 목록 조회 및 분석
- 오래된 세션 정리 제안
- 세션 사용 패턴 분석
- 특정 세션 찾기 및 정보 조회

## MCP 도구

데이터 조회가 필요하면 `query_db` 도구를 사용하라. `{chat_id}` 플레이스홀더가 자동 치환된다.

- 활성 세션 목록: `query_db("SELECT id, name, ai_provider, model, is_current, last_activity_at FROM sessions WHERE chat_id = {chat_id} AND recycled = 0 AND deleted = 0 ORDER BY last_activity_at DESC LIMIT 30")`
- 리사이클된 세션: `query_db("SELECT id, name, ai_provider, last_activity_at FROM sessions WHERE chat_id = {chat_id} AND recycled = 1 AND deleted = 0 ORDER BY last_activity_at DESC")`
- 현재 세션: `query_db("SELECT id, name, ai_provider, model FROM sessions WHERE chat_id = {chat_id} AND is_current = 1 AND deleted = 0")`
- 세션 메시지 수: `query_db("SELECT s.name, COUNT(m.id) as msg_count FROM sessions s LEFT JOIN message_log m ON s.id = m.session_id WHERE s.chat_id = {chat_id} AND s.deleted = 0 GROUP BY s.id ORDER BY msg_count DESC")`
