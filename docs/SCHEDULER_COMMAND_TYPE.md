# Scheduler `command` Type

## 개요
기존 `schedules` 테이블은 AI 모델을 통해 자연어 프롬프트를 해석하고 실행하는 방식(`chat`, `workspace`)과, 파이썬 플러그인을 직접 실행하는 방식(`plugin`)만을 지원했습니다.
하지만 백그라운드 스크립트(예: 예약 가능 여부 확인, 크롤링 등)를 주기적으로 실행하고 결과만 텔레그램으로 받기에는 AI API 비용과 시간이 낭비되는 문제가 있었습니다.

이를 최적화하기 위해 `command`라는 새로운 스케줄 타입을 추가했습니다.

## 작동 방식
- `schedule_type`이 `command`인 경우, `message` 필드에 있는 문자열을 자연어 프롬프트가 아닌 **OS 터미널 쉘(Shell) 명령어**로 취급합니다.
- 지정된 크론(Cron) 주기가 되면 봇 프로세스가 해당 명령어를 `asyncio.create_subprocess_shell`을 통해 쉘에서 직접 실행합니다.
- 스크립트 실행 후 `stdout` 및 `stderr` 출력이 **존재하는 경우에만** 그 내용을 그대로 텔레그램으로 전송합니다.
- 출력이 비어있는 경우(Empty), 봇은 아무런 행동을 하지 않고 조용히 스케줄 실행을 종료합니다.

## 적용 사례 (DB Update)
기존에 자연어로 등록되었던 스케줄을 `command` 타입으로 최적화하려면 다음과 같이 DB를 업데이트합니다.

```sql
UPDATE schedules 
SET 
    schedule_type = 'command',
    message = 'venv/bin/python plugins/custom/naver_booking/get_availability.py'
WHERE id = 'naver_booking_check';
```

이 설정을 통해 AI 모델 추론 과정(LLM Inference)이 완전히 생략되어 시스템 리소스가 대폭 절감됩니다.
