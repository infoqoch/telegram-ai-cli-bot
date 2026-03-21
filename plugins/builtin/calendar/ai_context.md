# Google 캘린더 (Calendar)

Google Calendar API를 연동하여 일정을 관리하는 플러그인.

## 외부 API

Google Calendar API (서비스 계정 인증). DB 테이블 없이 Google 서버에 직접 CRUD.

### CalendarEvent 구조

- `id`: Google 이벤트 ID
- `summary`: 일정 제목
- `start` / `end`: 시작/종료 시간 (datetime)
- `location`: 장소 (선택)
- `description`: 설명 (선택)
- `all_day`: 종일 일정 여부

## 기능

- 오늘 일정 보기 (일별 허브 뷰)
- 날짜 탐색 (이전/다음, 캘린더 그리드)
- 일정 추가 (날짜 → 시간 → 제목 입력)
- 종일 일정 추가
- 일정 수정 (제목, 날짜/시간)
- 일정 삭제
- 아침 브리핑 스케줄

## AI 활용

- 일정 충돌 감지 및 조정 제안
- 하루/주간 일정 최적화
- 일정 기반 시간 관리 조언
- 반복 일정 패턴 분석
- 여유 시간 파악 및 활용 제안

## MCP 도구 (사용 가능 시)

MCP 도구가 활성화된 경우, 아래 도구로 캘린더 데이터를 직접 조회/생성할 수 있다.
특정 기간의 일정이 필요하면 컨텍스트 데이터 대신 도구를 사용하라.

- `calendar_list_events(start_date, end_date)`: 기간별 일정 조회 (YYYY-MM-DD 형식)
- `calendar_create_event(summary, start, all_day)`: 새 일정 생성 (start는 YYYY-MM-DDTHH:MM 형식)

## 제약사항

- Google 서비스 계정 설정 필요 (GOOGLE_SERVICE_ACCOUNT_FILE, GOOGLE_CALENDAR_ID)
- 설정 안 된 경우 사용 불가
- 실시간 API 호출
