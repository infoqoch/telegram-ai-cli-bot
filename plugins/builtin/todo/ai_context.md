# 할일 관리 (Todo)

할일(투두) 목록을 날짜별로 관리하는 플러그인.

## DB 스키마

```sql
todos (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    date TEXT NOT NULL,        -- YYYY-MM-DD
    slot TEXT DEFAULT 'default',
    text TEXT NOT NULL,        -- 할일 내용
    done INTEGER DEFAULT 0,   -- 0=미완료, 1=완료
    created_at TEXT,
    updated_at TEXT
)
```

## 기능

- 할일 추가 (여러 줄 입력으로 일괄 추가)
- 완료 처리 / 삭제
- 내일로 이동 (미완료 항목)
- 다중 선택 후 일괄 완료/삭제/이동
- 날짜별 조회 (이전/다음 날 탐색)
- 주간 뷰 (7일간 진행률 요약)
- 어제 미완료 항목 이월

## AI 활용

- 할일 우선순위 제안
- 카테고리 분류 및 그룹화
- 완료 패턴 분석
- 일일/주간 계획 수립 도우미
- 반복 할일 패턴 파악

## 제약사항

- 날짜 기반 관리 (date 컬럼)
- 사용자(chat_id)별 격리
- 하루 단위로 할일 목록 구성
