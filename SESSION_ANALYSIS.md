# 세션 관리 시스템 분석

> **문서 상태:** v3.0 (2026-03-02)
>
> v1.0 문제 모두 해결, v2.0 개선 과제 대부분 완료 또는 불필요 판정.

---

## 1. 핵심 아키텍처

```
[사용자 메시지]
    ↓
[_user_locks] 유저별 Lock으로 세션 결정
    ↓
[_user_semaphores] 동시 3개 요청 제한
    ↓
[Fire-and-Forget] 백그라운드에서 Claude 호출
    ↓
[Watchdog] 30분 초과 태스크 자동 정리
```

### 데이터 구조
```python
{
    "user_id": {
        "current": "claude_session_id",
        "sessions": {
            "claude_session_id": {
                "created_at": "...",
                "last_used": "...",
                "history": ["질문1", "질문2"]
            }
        }
    }
}
```

---

## 2. 해결된 문제들

### v1.0 → v2.0 (Race Condition 관련)

| 문제 | 해결 방법 |
|------|----------|
| 세션 생성 Race Condition | `_user_locks` 유저별 Lock |
| `current` 덮어쓰기 | Lock으로 동시 생성 방지 |
| `set_claude_session_id()` 타이밍 | 메서드 제거, session_id = PK |
| Map 세션 유실 | 명시적 session_id 전달 |

### v2.0 → v3.0 (개선 과제)

| 과제 | 상태 | 해결 방법 |
|------|------|----------|
| 5.1 파일 I/O Race | ✅ 해결 | atomic write (temp + replace) |
| 5.6 Rate Limiting | ✅ 해결 | Semaphore(3) + Watchdog |
| 5.7 datetime 파싱 | ✅ 해결 | try-except 적용 |
| 5.9 중복 코드 | ✅ 해결 | 데코레이터 추가 (미적용) |

---

## 3. 불필요 판정 (오버엔지니어링)

### 5.2 CLI 인젝션 → 불필요

**이유**: `asyncio.create_subprocess_exec`는 `shell=False`가 기본값이며, 각 인자가 개별적으로 전달됨. Shell injection 불가능.

```python
# 안전함 - shell=False, 각 인자 분리
process = await asyncio.create_subprocess_exec(*cmd, ...)
```

### 5.3 메모리 누수 (_user_locks) → 불필요

**이유**:
- `ALLOWED_CHAT_IDS`로 허용 사용자 제한됨
- Lock/Semaphore 객체는 수십 바이트로 매우 가벼움
- 수백 명 사용해도 수 KB 수준

### 5.5 암호화 저장 → 불필요

**이유**:
- 개인 사용 봇 (ALLOWED_CHAT_IDS 제한)
- 서버에 직접 접근 가능한 사람 = 운영자 본인
- 필요 시 파일 권한 600 설정으로 충분

---

## 4. 남은 과제 (낮은 우선순위)

### 4.1 SESSION_NOT_FOUND 자동 복구

**현재 동작**: 에러 메시지 표시
**개선안**: 자동으로 새 세션 생성

```python
if error == "SESSION_NOT_FOUND":
    self.sessions.clear_current(user_id)
    await update.message.reply_text("세션이 만료되었습니다. 다시 메시지를 보내주세요.")
```

**판단**: 드문 케이스. 현재 상태로 충분함.

### 4.2 Graceful Shutdown

**현재 동작**: 봇 종료 시 진행 중 태스크 즉시 취소
**개선안**: 종료 전 태스크 완료 대기

**판단**: 재시작 빈도 낮음. TODO.md에 기록됨.

---

## 5. 현재 보호 메커니즘 요약

| 계층 | 보호 대상 | 메커니즘 |
|------|----------|----------|
| 접근 제어 | 허용 사용자 | ALLOWED_CHAT_IDS |
| 인증 | 인증된 사용자 | AuthManager (30분 TTL) |
| 동시성 | 세션 생성 | `_user_locks` (유저별 Lock) |
| 리소스 | 동시 요청 | `_user_semaphores` (최대 3개) |
| 좀비 방지 | 장시간 태스크 | Watchdog (30분 타임아웃) |
| 데이터 | 파일 저장 | atomic write |
| DoS | 메시지 길이 | MAX_MESSAGE_LENGTH (4096) |

---

## 6. 테스트 커버리지

- **총 100개 테스트** 통과
- Race Condition, atomic write, datetime 파싱, Fire-and-Forget 모두 테스트됨

---

## 7. 변경 이력

| 버전 | 날짜 | 내용 |
|------|------|------|
| v1.0 | 2026-03-02 | 초기 분석 - Race Condition 발견 |
| v2.0 | 2026-03-02 | Race Condition 해결, 개선 과제 정리 |
| v3.0 | 2026-03-02 | 개선 과제 완료, 오버엔지니어링 항목 제거 |
