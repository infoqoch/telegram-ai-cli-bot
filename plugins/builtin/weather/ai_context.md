# 날씨 조회 (Weather)

Open-Meteo API를 이용한 날씨 정보 조회 플러그인.

## DB 스키마

```sql
weather_locations (
    chat_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,        -- 지역명
    country TEXT,              -- 국가
    lat REAL NOT NULL,         -- 위도
    lon REAL NOT NULL,         -- 경도
    updated_at TEXT
)
```

## 기능

- 도/광역시 → 시/군 2단계 지역 선택
- 현재 날씨 조회 (기온, 습도, 풍속, 날씨 상태)
- 3일간 예보 (최저/최고 기온)
- 위치 저장 (마지막 조회 지역 기억)

## AI 활용

- 날씨 기반 옷차림/활동 추천
- 여행 계획 시 날씨 참고
- 주간 날씨 트렌드 분석
- 기상 상황에 따른 일정 조정 제안

## 제약사항

- 사용자당 1개 위치만 저장
- Open-Meteo API 기반 (한국 도시 CSV 매핑)
- 실시간 API 호출 (캐시 없음)
