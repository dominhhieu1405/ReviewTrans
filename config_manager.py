from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AppConfig:
    openai_api_key: str = ""
    gemini_api_key: str = ""
    custom_tts_key: str = ""
    custom_tts_url: str = ""
    vbee_app_id: str = ""
    vbee_token: str = ""
    vbee_voice_code: str = ""
    vbee_max_retries: int = 10
    default_target_language: str = "vi"
    default_provider: str = "Gemini"
    default_tts_provider: str = "Custom API"
    default_translate_all: bool = True
    default_enable_tts: bool = False
    default_enable_subtitles: bool = True
    default_whisper_language: str = "zh"
    last_opened_directory: str = ""
    extra: dict = field(default_factory=dict)


class ConfigManager:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or self._default_config_path()
        self.config = AppConfig()
        self.load()

    def _default_config_path(self) -> Path:
        base_dir = Path.home() / ".video_translation_studio"
        base_dir.mkdir(parents=True, exist_ok=True)
        print(base_dir)
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
            custom_tts_url=data.get("custom_tts_url", ""),
            vbee_app_id=data.get("vbee_app_id", ""),
            vbee_token=data.get("vbee_token", ""),
            vbee_voice_code=data.get("vbee_voice_code", ""),
            vbee_max_retries=data.get("vbee_max_retries", 10),
            default_target_language=data.get("default_target_language", "vi"),
            default_provider=data.get("default_provider", "Gemini"),
            default_tts_provider=data.get("default_tts_provider", "Edge TTS"),
            default_translate_all=data.get("default_translate_all", True),
            default_enable_tts=data.get("default_enable_tts", False),
            default_enable_subtitles=data.get("default_enable_subtitles", True),
            default_whisper_language=data.get("default_whisper_language", "zh"),
            last_opened_directory=data.get("last_opened_directory", ""),
            extra=data.get("extra", {}),
        )
        return self.config

    def save(self) -> None:
        data = {
            "openai_api_key": self.config.openai_api_key,
            "gemini_api_key": self.config.gemini_api_key,
            "custom_tts_key": self.config.custom_tts_key,
            "custom_tts_url": self.config.custom_tts_url,
            "vbee_app_id": self.config.vbee_app_id,
            "vbee_token": self.config.vbee_token,
            "vbee_voice_code": self.config.vbee_voice_code,
            "vbee_max_retries": self.config.vbee_max_retries,
            "default_target_language": self.config.default_target_language,
            "default_provider": self.config.default_provider,
            "default_tts_provider": self.config.default_tts_provider,
            "default_translate_all": self.config.default_translate_all,
            "default_enable_tts": self.config.default_enable_tts,
            "default_enable_subtitles": self.config.default_enable_subtitles,
            "default_whisper_language": self.config.default_whisper_language,
            "last_opened_directory": self.config.last_opened_directory,
            "extra": self.config.extra,
        }
        with self.config_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
