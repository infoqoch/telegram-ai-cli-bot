# 작업 현황 - AI 작업 처리 상태 모니터링

## 기능 개요
현재 진행 중인 AI 요청, 대기 중인 메시지, 세션 잠금 상태를 실시간으로 확인하는 읽기 전용 모니터링 기능입니다.

## 관련 DB 테이블

### message_log (AI 요청 추적)
| 컬럼 | 설명 |
|------|------|
| id | 요청 고유 ID |
| chat_id | Telegram 채팅 ID |
| session_id | 세션 ID |
| model | 사용 모델 |
| request | 요청 메시지 내용 |
| request_at | 요청 시각 |
| processed | 처리 상태 (0=대기, 1=처리중, 2=완료) |
| processed_at | 처리 완료 시각 |
| response | AI 응답 내용 |
| error | 에러 메시지 |
| delivery_status | 전달 상태 (not_ready / pending / delivered / failed) |
| delivery_attempts | 전달 시도 횟수 |

### queued_messages (동시 요청 큐)
| 컬럼 | 설명 |
|------|------|
| id | 큐 항목 ID |
| session_id | 대상 세션 ID |
| message | 대기 메시지 내용 |
| model | 사용 모델 |
| created_at | 큐 등록 시각 |

### session_locks (세션 잠금)
| 컬럼 | 설명 |
|------|------|
| session_id | 잠금된 세션 ID |
| job_id | 처리 중인 작업 ID |
| worker_pid | 워커 프로세스 PID |
| acquired_at | 잠금 획득 시각 |

## 사용자 조작
- **상태 보기**: 현재 진행/대기 중인 작업 목록 확인
- **새로고침**: 최신 상태로 갱신
- 읽기 전용 기능으로, 작업 취소/수정은 불가

## AI 도움 가능 영역
- 작업 상태 설명 및 해석
- 멈춘 작업 진단 및 해결 방안 제시
- 작업 처리 패턴 분석 (평균 소요 시간, 에러 빈도 등)
- 시스템 최적화 제안
