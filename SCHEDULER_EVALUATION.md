# 스케줄러 오작동 분석 및 평가 (Scheduler Evaluation)

방금 스케줄러(특히 Antigravity 관련 스케줄)가 의도한 대로 동작하지 않았던 원인을 코드와 로그 수준에서 분석한 결과입니다.

## 1. 문제의 핵심 원인 (Root Cause)
가장 큰 문제는 **스케줄러가 어떤 AI 프로바이더를 사용할지 결정하는 로직에 하드코딩된 제약이 있었다는 점**입니다. 
`src/schedule_utils.py` 파일 내의 `resolve_provider` 함수를 보면, 사용자가 지정한 AI 프로바이더(`ai_provider`)가 유효한지 검사하는 구문이 있었습니다.

```python
# 과거 (버그) 코드
def resolve_provider(schedule, *, fallback: str = "claude") -> str:
    provider = getattr(schedule, "ai_provider", None)
    if not isinstance(provider, str) or provider not in {"claude", "codex", "gemini"}:
        return fallback
    return provider
```

보시다시피 허용되는 프로바이더 목록이 `{"claude", "codex", "gemini"}`로 고정(하드코딩)되어 있었습니다.
이로 인해 **사용자가 "agy"(Antigravity) 기반의 스케줄을 생성하고 실행하려고 해도, 검사 로직에서 `agy`가 목록에 없다고 판단하여 강제로 `fallback`인 "claude"로 라우팅**해버리는 치명적인 버그가 발생했습니다.

결과적으로 Agy를 기대한 작업이 Claude 클라이언트(ClaudeClient)로 넘어가면서 모델명 호환성 오류(`agy-pro-high`를 Claude가 알 수 없음) 또는 엉뚱한 컨텍스트 오류로 이어져 실패하거나 무시된 것입니다.

## 2. 시스템 로그 관찰 및 오해 소지
추가로 `supervisor-*.log` 로그를 살펴보면 다음과 같은 기록이 있습니다.
`Schedule d41a63bd executed (no notification needed)`

이것은 버그라기보다는 스케줄러의 정상적인 플러그인 처리 로직입니다. 
`src/services/schedule_execution_service.py`에 따르면, AI 응답이 아니라 **플러그인 스케줄**이 실행되었고 그 결과가 특별히 사용자에게 알림을 보낼 필요가 없는 상태(`None` 반환)일 때 남는 로그입니다. 만약 "스케줄이 실행은 되는데 아무 알림도 오지 않는다"라고 느꼈다면, 이는 AI 연동 실패와는 별개로 플러그인 로직이 '알림 불필요' 상태를 반환했기 때문일 수도 있습니다.

## 3. 평가 및 해결책 (Resolution)
이 문제는 스케줄러 아키텍처에 새로운 AI(Agy)를 추가할 때 라우팅 필터를 업데이트하지 않아 생긴 고전적인 누락 버그입니다.

다행히 현재 작업 공간의 수정 내역(Git unstaged changes)을 확인해 보니, 이 버그에 대한 올바른 픽스가 적용되어 있습니다:
```python
# 수정된 코드 (현재 상태)
from src.ai.catalog import SUPPORTED_PROVIDERS

def resolve_provider(schedule, *, fallback: str = "claude") -> str:
    provider = getattr(schedule, "ai_provider", None)
    if not isinstance(provider, str) or provider not in SUPPORTED_PROVIDERS:
        return fallback
    return provider
```
이제 중앙 레지스트리의 `SUPPORTED_PROVIDERS`를 동적으로 참조하므로, `agy` 스케줄이 정상적으로 `AgyClient`로 라우팅되어 백그라운드 명령어(`agy --print`)가 정상 실행될 것입니다.

**💡 최종 결론:**
Agy 스케줄이 실패한 이유는 스케줄러 유틸리티가 `agy`를 미지원 프로바이더로 간주하여 강제로 Claude로 납치(Hijack)했기 때문입니다. 현재 코드는 수정되었으니 봇을 재시작(Reload)하면 스케줄러가 Antigravity와 정상적으로 연동될 것입니다.
