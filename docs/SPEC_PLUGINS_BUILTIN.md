# 빌트인 플러그인 기획서

> 빌트인 플러그인(Todo, Memo, Weather)의 UI/UX 기획
> 봇 코어 기획은 [SPEC.md](SPEC.md) 참조

---

## 투두 플러그인

### 개념

일별 할일 관리. 오늘의 할일을 기준으로, 완료/삭제/내일로 이동 가능. 주간 뷰와 어제 미완료 이월 기능 제공.

### 트리거

- 자연어: `todo`, `할일`, `투두` (메시지 시작)
- 제외: 질문 패턴 ("투두란 뭐야", "어떻게", "왜" 등) → AI에게 넘김

### 하루 플로우

```
09:00  [자동] Yesterday Report
       → 어제 미완료 항목 표시 + 이월 버튼

낮     [수동] 할일 추가/완료/삭제
       → "할일" 입력 → 오늘 리스트 + 액션 버튼

21:00  [자동] Daily Wrap-up
       → 오늘 진행률 + 미완료 항목 → 내일로 이동 유도
```

### 오늘 리스트 화면

```
Todos for 2026-03-07

⬜ 1. Buy groceries
✅ 2. Read chapter 5
⬜ 3. Call dentist

1/3 completed

[1. Buy groceries]  [3. Call dentist]  ← 미완료 항목만 버튼 (20자 truncate)
[Multi-select]
[Prev] [Week] [Next]
[Add] [Refresh]
```

- 빈 리스트: `No todos yet.` + `[Add]` 버튼

### 항목 상세 → 액션

항목 클릭 시:
```
Todo

Buy groceries

[Done] [Delete]
[Tomorrow]
[Back]
```

각 액션 후 피드백 메시지 + 리스트 갱신:
- Done: `Marked as done!`
- Delete: `Deleted!`
- Tomorrow: `Moved to tomorrow!`

### 추가 (ForceReply)

`[Add]` → ForceReply 프롬프트 → 사용자 입력 (줄바꿈으로 복수 항목 가능) → 결과:
```
3 added!

- Buy milk
- Clean house
- Fix bug

[View list] [Add more]
```

### 멀티 선택 모드

```
Multi-select

Tap items to select/deselect.

⬜ Buy groceries
☑️ Call dentist

1 selected

[⬜ Buy groceries]  [☑️ Call dentist]    ← 18자 truncate
[Done(1)] [Delete(1)] [Tomorrow(1)]   ← 선택 시에만 표시
[Deselect all] [Back]
```

### 날짜 네비게이션

- `[Prev]`/`[Next]`: 하루씩 이동, 해당 날짜의 할일 표시
- 오늘이 아닌 날짜: 헤더에 `MM/DD` 표시
- `[Today]` 버튼으로 오늘로 복귀

### 주간 뷰

기준일을 중심으로 전후 3일 (총 7일) 표시. 월~일 고정이 아닌 중심일 기반.

```
Weekly Todos (03/04 ~ 03/10)

03/04(Mon): ✅ 3/3        ← 전체 완료
03/05(Tue): ⬜ 1/4        ← 미완료 있음
03/06(Wed): —             ← 할일 없음
👉 03/07(Thu): ⬜ 2/5      ← 오늘 표시

[4(Mon)] [5(Tue)] [6(Wed)] [📍7(Thu)]  ← 날짜 클릭 → 해당일 리스트
[8(Fri)] [9(Sat)] [10(Sun)]
[Prev week] [Today] [Next week]
```

### 어제 미완료 이월

Yesterday Report 또는 `[Carry over]` 버튼으로 진입:

```
Incomplete from 2026-03-06

Select items to carry over to today.

⬜ Unfinished task A
☑️ Unfinished task B

1 selected

[⬜ task A] [☑️ task B]          ← 18자 truncate
[Carry selected(1)] [Carry all(2)]
[Today]
```

- 선택 이월 / 전체 이월 가능
- 빈 상태: `No incomplete items from yesterday!`

### 스케줄 액션

| 액션 | 시간 | 동작 |
|------|------|------|
| `yesterday_report` | 09:00 | 어제 미완료 표시 + 이월 버튼. 어제 할일 없으면 스킵 |
| `daily_wrap` | 21:00 | 오늘 진행률 + 미완료 목록. 전체 완료면 축하 메시지. 할일 없으면 스킵 |

---

## 메모 플러그인

### 개념

짧은 텍스트 메모 저장. 최대 30개 (모바일에서 스크롤 없이 관리 가능한 적정 개수). CRUD + 멀티 삭제.

### 트리거

- 자연어: `메모`, `memo` (정확 일치)
- 제외: 질문 패턴 → AI에게 넘김

### 메인 화면

```
Memo

Saved: 5
(최대: Saved: 30 (max 30))

[List] [Add]
```

### 리스트 화면

```
Memo List

#1 Meeting notes for project review...
2026-03-05

#2 Shopping list
2026-03-06

[🗑️ #1 Meeting notes] [🗑️ #2 Shopping li...]  ← 15자 truncate
[Multi-delete]                                   ← 2개 이상일 때만
[Add] [Refresh]
[Back]
```

- 빈 상태: `No saved memos.` + `[Add]` + `[Back]`

### 추가 (ForceReply)

`[Add]` → ForceReply → 입력 → 저장:
```
Memo saved!

#3 This is my new memo content

[List] [Add]
```

- 빈 입력: `Memo content is empty.`
- 30개 초과: `Maximum 30 memos reached. Delete some before adding new ones.`

### 단일 삭제 (2단계 확인)

`[🗑️ #N]` → 확인 화면:
```
Delete?

#5 This is the memo content...

[Delete] [Cancel]
```

삭제 완료: `Deleted: ~~content~~` (취소선, 20자 truncate) + 리스트 갱신.

### 멀티 삭제

`[Multi-delete]` → 선택 모드:
```
Select Memos to Delete

Tap to select.

[⬜ #1 Short preview...] [✅ #2 Selected memo]  ← 20자 truncate
[Delete 1]               ← 선택 시에만
[Cancel]
```

확인 화면:
```
Delete 2 Memos?

- #1 Meeting notes for project re...    ← 30자 truncate
- #2 Shopping list

Are you sure?
[Delete] [Cancel]
```

---

## 날씨 플러그인

### 개념

도시별 현재 날씨 + 3일 예보. 기본 위치 저장 가능. Open-Meteo API (무료, 키 불필요).

### 트리거

- 자연어: `날씨`, `기온`, `weather`
- 도시 지정: `서울 날씨` (한국어 도시명 + 날씨)
- 위치 설정: `위치 설정: 서울`, `날씨 위치: 서울`, `서울 날씨 설정`

### 날씨 조회 시나리오

**저장된 위치 있음:** `날씨` → 저장 위치의 날씨 즉시 표시.

**저장된 위치 없음:** `날씨` → 도시 선택 화면 (퀵 시티 7개, 2열 그리드).

**도시 지정:** `서울 날씨` → 해당 도시 날씨 즉시 표시.

### 날씨 표시 화면

```
☀️ 서울 Weather

Current
- Weather: Clear
- Temp: 15.2°C
- Humidity: 45%
- Wind: 8.3 km/h

3-Day Forecast
03/07 ☀️ 5° / 16°
03/08 ⛅ 7° / 14°
03/09 🌧️ 3° / 10°

[서울] [부산] [대구] [인천]    ← 퀵 시티 (4+3 그리드)
[광주] [대전] [제주]
[Refresh] [Other city]
```

- 도시 선택 화면(저장 위치 없음)에서는 2열 그리드, 날씨 결과 화면에서는 4+3 그리드

### 위치 설정

`위치 설정: 서울` →
```
Location set!

서울 (South Korea)
Lat: 37.5665, Lon: 126.9780

[Check weather]
```

- 도시 못 찾음: `'{name}' not found. Try a different location.`

### 날씨 아이콘 매핑

| 코드 | 아이콘 | 설명 |
|------|--------|------|
| 0 | ☀️ | Clear |
| 1 | 🌤️ | Mostly clear |
| 2 | ⛅ | Partly cloudy |
| 3 | ☁️ | Overcast |
| 45, 48 | 🌫️ | Fog / Rime fog |
| 51, 53, 55 | 🌧️ | Drizzle |
| 61, 63 | 🌧️ | Rain |
| 65 | 🌧️ | Heavy rain |
| 71, 73 | 🌨️ | Snow |
| 75 | ❄️ | Heavy snow |
| 80, 81 | 🌦️ | Showers |
| 82 | ⛈️ | Heavy showers |
| 95, 96, 99 | ⛈️ | Thunderstorm |
