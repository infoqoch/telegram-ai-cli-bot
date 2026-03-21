# 스케줄러 - 예약 메시지/작업 관리

## 기능 개요
사용자가 정해진 시간에 자동으로 실행되는 AI 대화, 워크스페이스 작업, 플러그인 액션을 등록·관리하는 기능입니다.

## 스케줄 유형 (schedule_type)
- **chat**: 일반 AI 대화 스케줄. 지정된 시간에 메시지를 AI에게 보내고 응답을 받음
- **workspace**: 워크스페이스 기반 스케줄. 특정 프로젝트 디렉토리의 CLAUDE.md 규칙을 적용하여 AI 작업 실행
- **plugin**: 플러그인 액션 스케줄. 할일 체크, 일기 알림 등 플러그인이 제공하는 액션을 실행 (AI 모델/메시지 불필요)

## 트리거 유형 (trigger_type)
- **cron**: 매일 반복 (hour, minute 기준)
- **run_at**: 1회성 실행 (run_at_local에 지정된 시각에 실행 후 자동 비활성화)
- **cron_expr**: cron 표현식 기반 반복 (예: 특정 요일만)

## DB 스키마 (schedules 테이블)
| 컬럼 | 설명 |
|------|------|
| id | 스케줄 고유 ID |
| user_id | 사용자 ID |
| chat_id | Telegram 채팅 ID |
| hour, minute | 실행 시각 (0-23시, 0-59분) |
| message | AI에게 보낼 메시지 (chat/workspace 유형) |
| name | 스케줄 이름 (사용자 표시용) |
| schedule_type | chat / workspace / plugin |
| trigger_type | cron / run_at / cron_expr |
| cron_expr | cron 표현식 (trigger_type=cron_expr일 때) |
| run_at_local | 1회성 실행 시각 (trigger_type=run_at일 때) |
| ai_provider | AI 제공자 (claude / codex) |
| model | AI 모델 프로필 키 |
| workspace_path | 워크스페이스 경로 (workspace 유형) |
| plugin_name | 플러그인 이름 (plugin 유형) |
| action_name | 플러그인 액션 이름 (plugin 유형) |
| enabled | 활성화 여부 (1=ON, 0=OFF) |
| last_run | 마지막 실행 시각 |
| last_error | 마지막 에러 메시지 |
| run_count | 총 실행 횟수 |

## 사용자 조작
- **추가**: 새 스케줄 등록 (시간, 메시지, 유형 선택)
- **켜기/끄기**: 스케줄 활성화/비활성화 토글
- **시간 변경**: 실행 시각 수정
- **삭제**: 스케줄 제거
- **목록 보기**: 등록된 모든 스케줄 확인

## AI 도움 가능 영역
- 스케줄 최적화 제안 (시간대 분산, 중복 제거)
- 새로운 스케줄 추천 (사용 패턴 기반)
- 스케줄 실행 결과 분석
- 에러 발생 시 원인 분석 및 해결 방안 제시
