"""Shared upload limits; multipart bodies are spooled, images decoded one at a time."""
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 50_000_000  # Includes iPhone 8064 x 6048 (48.8 MP).
MAX_EDGE = 1920
FORM_BYTES = 131072
MEMBER_PLACE_SLOTS = 4
ADMIN_PLACE_SLOTS = 5
BODY_LIMIT_MESSAGE = "사진의 총 용량이 너무 큽니다. 사진 수를 줄여 다시 시도해주세요."
