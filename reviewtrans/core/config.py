from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .models import SubtitleStyle, clone, from_dict, new_id, to_dict
from .paths import default_projects_root, user_data_dir

# kind -> nhãn hiển thị
TRANSLATE_KINDS = {
    "google": "Google Translate",
    "microsoft": "Microsoft Translator",
    "openai": "API kiểu OpenAI",
    "gemini": "API kiểu Gemini",
    "anthropic": "Anthropic (Claude)",
}
LLM_KINDS = {"openai", "gemini", "anthropic"}

TTS_KINDS = {
    "edge": "Edge TTS (miễn phí)",
    "vbee": "vBee",
    "openai_speech": "API kiểu OpenAI (/v1/audio/speech)",
    "legacy_custom": "Custom API (bản cũ)",
}


@dataclass
class ProviderProfile:
    id: str = field(default_factory=new_id)
    name: str = ""
    kind: str = ""
    base_url: str = ""
    api_keys: list[str] = field(default_factory=list)
    model: str = ""
    region: str = ""
    options: dict = field(default_factory=dict)

    def option(self, key: str, default=None):
        value = self.options.get(key)
        return default if value in (None, "") else value

    @property
    def is_llm(self) -> bool:
        return self.kind in LLM_KINDS


@dataclass
class RenderSettings:
    video_codec: str = "libx264"  # libx264 | libx265 | h264_nvenc | hevc_nvenc
    crf: int = 20
    preset: str = "medium"
    audio_bitrate: str = "192k"
    hwaccel_decode: bool = False


@dataclass
class AppSettings:
    projects_root: str = ""
    recent_projects: list[str] = field(default_factory=list)
    last_project: str = ""
    default_source_language: str = "zh"
    default_target_language: str = "vi"
    default_translate_profile: str = ""
    default_tts_profile: str = ""
    default_whisper_model: str = "small"
    whisper_threads: int = 4
    translate_batch_size: int = 60
    tts_concurrency: int = 3
    player_backend: str = "auto"  # auto | mpv | qt
    render: RenderSettings = field(default_factory=RenderSettings)
    translate_profiles: list[ProviderProfile] = field(default_factory=list)
    tts_profiles: list[ProviderProfile] = field(default_factory=list)
    subtitle_presets: list[SubtitleStyle] = field(default_factory=list)
    window_state: dict = field(default_factory=dict)

    def projects_root_path(self) -> Path:
        return Path(self.projects_root) if self.projects_root else default_projects_root()

    def find_translate(self, profile_id: str) -> ProviderProfile | None:
        return _find(self.translate_profiles, profile_id)

    def find_tts(self, profile_id: str) -> ProviderProfile | None:
        return _find(self.tts_profiles, profile_id)

    def add_recent(self, path: str) -> None:
        path = str(Path(path))
        self.recent_projects = [p for p in self.recent_projects if Path(p) != Path(path)]
        self.recent_projects.insert(0, path)
        self.recent_projects = self.recent_projects[:30]
        self.last_project = path


def _find(profiles: list[ProviderProfile], profile_id: str) -> ProviderProfile | None:
    for profile in profiles:
        if profile.id == profile_id:
            return profile
    return profiles[0] if profiles and not profile_id else None


def builtin_presets() -> list[SubtitleStyle]:
    classic = SubtitleStyle(name="Review cổ điển")
    yellow = SubtitleStyle(name="Vàng viền đen", text_color="#FFE14D", outline_width=3.5)
    boxed = SubtitleStyle(
        name="Hộp nền mờ", bg_enabled=True, outline_width=0, shadow_depth=0, bg_opacity=65
    )
    tiktok = SubtitleStyle(
        name="Short/TikTok", font_size=72, position="middle", margin_v=0,
        outline_width=5, uppercase=True, text_color="#FFFFFF",
    )
    return [classic, yellow, boxed, tiktok]


def default_settings() -> AppSettings:
    settings = AppSettings()
    google = ProviderProfile(name="Google Translate", kind="google")
    microsoft = ProviderProfile(name="Microsoft Translator", kind="microsoft")
    edge = ProviderProfile(name="Edge TTS", kind="edge", options={"voice": "vi-VN-HoaiMyNeural"})
    settings.translate_profiles = [google, microsoft]
    settings.tts_profiles = [edge]
    settings.default_translate_profile = google.id
    settings.default_tts_profile = edge.id
    settings.subtitle_presets = builtin_presets()
    return settings


def migrate_legacy(data: dict, settings: AppSettings) -> None:
    """Chuyển config.json của bản cũ thành các profile."""
    gemini_keys = [k for k in data.get("gemini_api_keys", []) if str(k).strip()]
    if not gemini_keys and data.get("gemini_api_key"):
        gemini_keys = [data["gemini_api_key"]]
    if gemini_keys:
        profile = ProviderProfile(name="Gemini", kind="gemini", api_keys=gemini_keys, model="gemini-2.5-flash")
        settings.translate_profiles.append(profile)
        settings.default_translate_profile = profile.id
    if data.get("openai_api_key"):
        settings.translate_profiles.append(
            ProviderProfile(name="OpenAI", kind="openai", api_keys=[data["openai_api_key"]], model="gpt-4o")
        )
    if data.get("vbee_app_id") and data.get("vbee_token"):
        settings.tts_profiles.append(
            ProviderProfile(
                name="vBee", kind="vbee", api_keys=[data["vbee_token"]],
                options={
                    "app_id": data["vbee_app_id"],
                    "voice": data.get("vbee_voice_code", ""),
                    "max_polls": data.get("vbee_max_retries", 10),
                },
            )
        )
    if data.get("custom_tts_url"):
        settings.tts_profiles.append(
            ProviderProfile(
                name="Custom API", kind="legacy_custom", base_url=data["custom_tts_url"],
                api_keys=[data["custom_tts_key"]] if data.get("custom_tts_key") else [],
            )
        )
    if data.get("default_target_language"):
        settings.default_target_language = data["default_target_language"]
    if data.get("default_whisper_language"):
        settings.default_source_language = data["default_whisper_language"]


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_dir() / "settings.json"
        self._lock = threading.Lock()
        self.settings = self._load()

    def _load(self) -> AppSettings:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                settings = from_dict(AppSettings, data)
                if not settings.subtitle_presets:
                    settings.subtitle_presets = builtin_presets()
                return settings
            except (OSError, json.JSONDecodeError):
                backup = self.path.with_suffix(".broken.json")
                try:
                    self.path.replace(backup)
                except OSError:
                    pass
        settings = default_settings()
        legacy = self.path.parent / "config.json"
        if legacy.exists():
            try:
                migrate_legacy(json.loads(legacy.read_text(encoding="utf-8")), settings)
            except (OSError, json.JSONDecodeError):
                pass
        settings.projects_root = str(default_projects_root())
        self.settings = settings
        self.save()
        return settings

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(to_dict(self.settings), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def snapshot(self) -> AppSettings:
        return clone(self.settings)
