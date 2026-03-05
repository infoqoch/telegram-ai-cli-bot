"""공유 상수 정의."""

# 스케줄 가능 시간대 (06:00 ~ 22:00)
AVAILABLE_HOURS = list(range(6, 23))

# Claude 모델
SUPPORTED_MODELS = ["opus", "sonnet", "haiku"]
DEFAULT_MODEL = "sonnet"
