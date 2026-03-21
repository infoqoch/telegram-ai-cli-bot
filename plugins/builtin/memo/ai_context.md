# 메모 관리 (Memo)

간단한 텍스트 메모를 저장하고 관리하는 플러그인.

## DB 스키마

```sql
memos (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    content TEXT NOT NULL,     -- 메모 내용
    created_at TEXT
)
```

## 기능

- 메모 추가
- 메모 삭제 (개별/다중 선택)
- 메모 목록 조회
- 최대 30개 제한

## AI 활용

- 메모 내용 기반 카테고리 분류
- 관련 메모 그룹화 및 요약
- 주제별 메모 검색
- 메모 정리 및 구조화 제안

## 제약사항

- 사용자(chat_id)별 격리
- 최대 30개 저장 제한
- 날짜 구분 없이 전체 목록 관리
