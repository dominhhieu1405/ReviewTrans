from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AppConfig:
    openai_api_key: str = ""
    gemini_api_key: str = ""
    custom_tts_key: str = ""
    default_output_dir: str = ""
    default_target_language: str = "en"
    default_provider: str = "ChatGPT"
    default_tts_provider: str = "Edge TTS"
    default_translate_all: bool = True
    default_enable_tts: bool = False
    default_enable_subtitles: bool = True
    default_whisper_language: str = "auto"
    extra: dict = field(default_factory=dict)


class ConfigManager:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or self._default_config_path()
        self.config = AppConfig()
        self.load()

    def _default_config_path(self) -> Path:
        base_dir = Path.home() / ".video_translation_studio"
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir / "config.json"

    def load(self) -> AppConfig:
        if not self.config_path.exists():
            self.save()
            return self.config
        with self.config_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        self.config = AppConfig(
            openai_api_key=data.get("openai_api_key", ""),
            gemini_api_key=data.get("gemini_api_key", ""),
            custom_tts_key=data.get("custom_tts_key", ""),
            default_output_dir=data.get("default_output_dir", ""),
            default_target_language=data.get("default_target_language", "en"),
            default_provider=data.get("default_provider", "ChatGPT"),
            default_tts_provider=data.get("default_tts_provider", "Edge TTS"),
            default_translate_all=data.get("default_translate_all", True),
            default_enable_tts=data.get("default_enable_tts", False),
            default_enable_subtitles=data.get("default_enable_subtitles", True),
            default_whisper_language=data.get("default_whisper_language", "auto"),
            extra=data.get("extra", {}),
        )
        return self.config

    def save(self) -> None:
        data = {
            "openai_api_key": self.config.openai_api_key,
            "gemini_api_key": self.config.gemini_api_key,
            "custom_tts_key": self.config.custom_tts_key,
            "default_output_dir": self.config.default_output_dir,
            "default_target_language": self.config.default_target_language,
            "default_provider": self.config.default_provider,
            "default_tts_provider": self.config.default_tts_provider,
            "default_translate_all": self.config.default_translate_all,
            "default_enable_tts": self.config.default_enable_tts,
            "default_enable_subtitles": self.config.default_enable_subtitles,
            "default_whisper_language": self.config.default_whisper_language,
            "extra": self.config.extra,
        }
        with self.config_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
