"""Chọn provider dịch/TTS theo thứ tự ưu tiên: video > project > mặc định (global).

Mỗi cấp có 2 trường: provider và giá trị phụ (model với dịch, giọng với TTS).
Giá trị phụ chỉ có nghĩa với provider của cấp đó hoặc cấp dưới: nếu video chọn provider khác,
model/giọng đặt ở project không được áp sang provider mới.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import AppSettings, ProviderProfile
from .models import Project, VideoDoc

SOURCE_LABELS = {"video": "video", "project": "project", "global": "mặc định"}

# kind -> (trường provider, trường giá trị phụ) trên Project/VideoDoc
FIELDS = {
    "translate": ("translate_profile", "translate_model"),
    "tts": ("tts_profile", "tts_voice"),
}


@dataclass
class Resolved:
    profile: ProviderProfile | None
    value: str  # model (dịch) hoặc giọng (TTS); rỗng = mặc định của profile
    profile_source: str  # video | project | global
    value_source: str  # video | project | "" (dùng mặc định của profile)

    def describe(self, kind: str) -> str:
        if self.profile is None:
            return "chưa có provider"
        extra = self.value or (self.profile.model if kind == "translate" else self.profile.option("voice", ""))
        text = f"{self.profile.name}"
        if extra:
            text += f" · {extra}"
        return f"{text} (từ {SOURCE_LABELS[self.profile_source]})"


def _profiles(settings: AppSettings, kind: str) -> list[ProviderProfile]:
    return settings.translate_profiles if kind == "translate" else settings.tts_profiles


def _find(settings: AppSettings, kind: str, profile_id: str) -> ProviderProfile | None:
    if not profile_id:
        return None
    return next((p for p in _profiles(settings, kind) if p.id == profile_id), None)


def global_profile(settings: AppSettings, kind: str) -> ProviderProfile | None:
    default_id = settings.default_translate_profile if kind == "translate" else settings.default_tts_profile
    profiles = _profiles(settings, kind)
    return _find(settings, kind, default_id) or (profiles[0] if profiles else None)


def resolve(
    settings: AppSettings,
    kind: str,
    project: Project | None,
    doc: VideoDoc | None = None,
) -> Resolved:
    """Truyền doc=None để xem giá trị mà video đang kế thừa, project=None để xem giá trị global."""
    profile_field, value_field = FIELDS[kind]
    levels = []
    if doc is not None:
        levels.append(("video", doc))
    if project is not None:
        levels.append(("project", project))
    value, value_source = "", ""
    for source, obj in levels:
        own_value = str(getattr(obj, value_field, "") or "").strip()
        if own_value and not value:
            value, value_source = own_value, source
        profile = _find(settings, kind, getattr(obj, profile_field, ""))
        if profile is not None:
            return Resolved(profile, value, source, value_source)
    return Resolved(global_profile(settings, kind), value, "global", value_source)
