# 일기 관리 (Diary)

하루 하나의 일기를 작성하고 관리하는 플러그인.

## DB 스키마

```sql
diaries (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    date TEXT NOT NULL,         -- YYYY-MM-DD (하루 1개, UNIQUE)
    content TEXT NOT NULL,      -- 일기 내용
    created_at TEXT,
    updated_at TEXT
)
```

## 기능

- 오늘/어제 일기 작성
- 일기 수정 및 삭제
- 월별 목록 조회 (월 탐색)
- 날짜별 개별 조회
- 일기 작성 알림 스케줄

## AI 활용

- 일기 작성 도우미 (문체 다듬기, 내용 보강)
- 감정/기분 분석
- 월간 회고 및 요약 생성
- 성찰 질문 제안
- 반복 패턴이나 성장 포인트 발견

## 제약사항

- 하루 1개 일기만 허용 (chat_id + date UNIQUE)
- 사용자(chat_id)별 격리
- 월 단위로 목록 관리
