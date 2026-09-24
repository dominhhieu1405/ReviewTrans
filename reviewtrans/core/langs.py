from __future__ import annotations

# code nội bộ -> (tên hiển thị, tên tiếng Anh cho LLM, Google, Microsoft, Whisper)
LANGUAGES: dict[str, tuple[str, str, str, str, str]] = {
    "auto": ("Tự nhận diện", "the source language", "auto", "", "auto"),
    "zh": ("Tiếng Trung (giản thể)", "Simplified Chinese", "zh-CN", "zh-Hans", "zh"),
    "zh-tw": ("Tiếng Trung (phồn thể)", "Traditional Chinese", "zh-TW", "zh-Hant", "zh"),
    "vi": ("Tiếng Việt", "Vietnamese", "vi", "vi", "vi"),
    "en": ("Tiếng Anh", "English", "en", "en", "en"),
    "ja": ("Tiếng Nhật", "Japanese", "ja", "ja", "ja"),
    "ko": ("Tiếng Hàn", "Korean", "ko", "ko", "ko"),
    "th": ("Tiếng Thái", "Thai", "th", "th", "th"),
    "id": ("Tiếng Indonesia", "Indonesian", "id", "id", "id"),
    "fr": ("Tiếng Pháp", "French", "fr", "fr", "fr"),
    "de": ("Tiếng Đức", "German", "de", "de", "de"),
    "es": ("Tiếng Tây Ban Nha", "Spanish", "es", "es", "es"),
    "ru": ("Tiếng Nga", "Russian", "ru", "ru", "ru"),
    "pt": ("Tiếng Bồ Đào Nha", "Portuguese", "pt", "pt", "pt"),
    "hi": ("Tiếng Hindi", "Hindi", "hi", "hi", "hi"),
}

SOURCE_LANGUAGES = list(LANGUAGES.keys())
TARGET_LANGUAGES = [code for code in LANGUAGES if code != "auto"]


def display_name(code: str) -> str:
    return LANGUAGES.get(code, (code,))[0]


def english_name(code: str) -> str:
    entry = LANGUAGES.get(code)
    return entry[1] if entry else code


def google_code(code: str) -> str:
    entry = LANGUAGES.get(code)
    return entry[2] if entry else code


def microsoft_code(code: str) -> str:
    entry = LANGUAGES.get(code)
    return entry[3] if entry else code


def whisper_code(code: str) -> str:
    entry = LANGUAGES.get(code)
    return entry[4] if entry else code
