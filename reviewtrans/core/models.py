from __future__ import annotations

import copy
import dataclasses
import time
import types
import typing
import uuid
from dataclasses import dataclass, field
from typing import Any, TypeVar, get_args, get_origin, get_type_hints

T = TypeVar("T")

# ---------------------------------------------------------------- serialization


def to_dict(obj: Any) -> Any:
    return dataclasses.asdict(obj)


def from_dict(cls: type[T], data: Any) -> T:
    """Dựng dataclass từ dict, bỏ qua khoá lạ và giữ mặc định cho khoá thiếu."""
    if not isinstance(data, dict):
        return cls()
    hints = get_type_hints(cls)
    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.name in data:
            try:
                kwargs[f.name] = _convert(hints[f.name], data[f.name])
            except (TypeError, ValueError):
                continue
    return cls(**kwargs)


def _convert(tp: Any, value: Any) -> Any:
    origin = get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        if value is None:
            return None
        args = [arg for arg in get_args(tp) if arg is not type(None)]
        return _convert(args[0], value) if args else value
    if dataclasses.is_dataclass(tp):
        return from_dict(tp, value)
    if origin is list:
        (arg,) = get_args(tp) or (Any,)
        return [_convert(arg, item) for item in value] if isinstance(value, list) else []
    if origin is dict:
        return dict(value) if isinstance(value, dict) else {}
    if tp is float:
        return float(value)
    if tp is int:
        return int(value)
    if tp is bool:
        return bool(value)
    if tp is str:
        return "" if value is None else str(value)
    return value


def clone(obj: T) -> T:
    return copy.deepcopy(obj)


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def now() -> float:
    return time.time()


# ---------------------------------------------------------------- style & layers


@dataclass
class SubtitleStyle:
    name: str = "Mặc định"
    font_family: str = "Arial"
    font_size: float = 56.0  # px ở chiều cao 1080, tự scale theo video
    bold: bool = True
    italic: bool = False
    uppercase: bool = False
    text_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_width: float = 3.0
    shadow_depth: float = 1.5
    shadow_color: str = "#000000"
    shadow_opacity: int = 70  # %
    bg_enabled: bool = False
    bg_color: str = "#000000"
    bg_opacity: int = 60  # %
    bg_padding: float = 10.0  # px ở 1080
    position: str = "bottom"  # bottom | middle | top
    margin_v: float = 7.0  # % chiều cao
    margin_h: float = 6.0  # % chiều rộng
    max_chars_per_line: int = 0  # 0 = tự xuống dòng theo độ rộng


LAYER_IMAGE = "image"
LAYER_TEXT = "text"
LAYER_BLUR = "blur"
LAYER_TYPES = {LAYER_IMAGE: "Ảnh", LAYER_TEXT: "Chữ", LAYER_BLUR: "Vùng che"}


@dataclass
class Layer:
    id: str = field(default_factory=new_id)
    type: str = LAYER_IMAGE
    name: str = ""
    enabled: bool = True
    start: float = 0.0
    end: float = -1.0  # -1 = tới hết video
    # Hình chữ nhật chuẩn hoá theo khung video (0..1)
    x: float = 0.05
    y: float = 0.05
    w: float = 0.2
    h: float = 0.1
    opacity: float = 1.0
    # ảnh
    image_path: str = ""
    keep_aspect: bool = True
    # chữ
    text: str = ""
    font_family: str = "Arial"
    font_size: float = 48.0  # px ở 1080
    bold: bool = True
    italic: bool = False
    text_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_width: float = 2.0
    bg_enabled: bool = False
    bg_color: str = "#000000"
    bg_opacity: int = 50
    align: str = "center"  # left | center | right
    # vùng che
    blur_mode: str = "blur"  # blur | pixelate | fill
    blur_strength: int = 20
    fill_color: str = "#000000"

    def label(self) -> str:
        return self.name or f"{LAYER_TYPES.get(self.type, self.type)} {self.id[:4]}"

    def active_at(self, t: float, duration: float) -> bool:
        end = duration if self.end < 0 else self.end
        return self.enabled and self.start <= t <= end

    def time_range(self, duration: float) -> tuple[float, float]:
        end = duration if self.end < 0 else self.end
        return self.start, max(self.start, end)


@dataclass
class AudioSettings:
    original_mode: str = "duck"  # keep | duck | mute
    original_volume: int = 100  # %
    duck_volume: int = 20  # % âm gốc trong lúc có lồng tiếng
    dub_volume: int = 100
    bgm_path: str = ""
    bgm_volume: int = 25
    fit_mode: str = "fit"  # fit: tăng tốc cho vừa khe | natural: giữ nguyên
    use_gap: bool = True  # tận dụng khoảng lặng tới câu kế tiếp trước khi tăng tốc
    max_speed: float = 1.8
    speed: float = 1.0  # tốc độ đọc cơ bản gửi cho TTS
    pitch: float = 0.0  # nửa cung


@dataclass
class TrackState:
    subtitles: bool = True
    dub: bool = True
    layers: bool = True
    original_audio: bool = True
    bgm: bool = True


@dataclass
class Segment:
    id: int = 0
    start: float = 0.0
    end: float = 0.0
    source: str = ""
    text: str = ""
    speaker: str = ""
    voice: str = ""  # giọng riêng cho câu này (để trống = theo nhân vật/project)
    tts_file: str = ""
    tts_key: str = ""
    tts_duration: float = 0.0
    locked: bool = False  # không bị dịch đè khi chạy lại

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def display_text(self) -> str:
        return self.text or self.source


# ---------------------------------------------------------------- video & project

STAGES = ["asr", "translate", "tts", "mix", "render"]
STAGE_LABELS = {
    "asr": "Nhận dạng",
    "translate": "Dịch",
    "tts": "Lồng tiếng",
    "mix": "Trộn âm",
    "render": "Xuất video",
}
STATE_NONE = "none"
STATE_DONE = "done"
STATE_STALE = "stale"
STATE_ERROR = "error"
STATE_RUNNING = "running"


@dataclass
class VideoTemplate:
    """Mặc định áp cho video mới trong project."""

    style: SubtitleStyle = field(default_factory=SubtitleStyle)
    layers: list[Layer] = field(default_factory=list)
    audio: AudioSettings = field(default_factory=AudioSettings)
    tracks: TrackState = field(default_factory=TrackState)
    flip_horizontal: bool = False


@dataclass
class VideoDoc:
    id: str = field(default_factory=new_id)
    name: str = ""
    source_path: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = True
    source_mode: str = "asr"  # asr | srt
    srt_path: str = ""
    whisper_model: str = ""  # trống = theo project
    source_language: str = ""  # trống = theo project
    # provider riêng cho video (trống = kế thừa project → mặc định)
    translate_profile: str = ""
    translate_model: str = ""
    tts_profile: str = ""
    tts_voice: str = ""
    style: SubtitleStyle = field(default_factory=SubtitleStyle)
    layers: list[Layer] = field(default_factory=list)
    audio: AudioSettings = field(default_factory=AudioSettings)
    tracks: TrackState = field(default_factory=TrackState)
    flip_horizontal: bool = False
    stages: dict[str, str] = field(default_factory=dict)
    stage_errors: dict[str, str] = field(default_factory=dict)
    last_output: str = ""
    updated_at: float = field(default_factory=now)

    def stage(self, name: str) -> str:
        return self.stages.get(name, STATE_NONE)

    def set_stage(self, name: str, state: str, error: str = "") -> None:
        self.stages[name] = state
        if error:
            self.stage_errors[name] = error
        else:
            self.stage_errors.pop(name, None)

    def mark_stale_from(self, name: str) -> None:
        """Đánh dấu các bước sau `name` cần chạy lại."""
        if name not in STAGES:
            return
        for later in STAGES[STAGES.index(name):]:
            if self.stage(later) == STATE_DONE:
                self.stages[later] = STATE_STALE

    def apply_template(self, template: VideoTemplate) -> None:
        self.style = clone(template.style)
        self.layers = [clone(layer) for layer in template.layers]
        for layer in self.layers:
            layer.id = new_id()
        self.audio = clone(template.audio)
        self.tracks = clone(template.tracks)
        self.flip_horizontal = template.flip_horizontal


@dataclass
class VideoRef:
    id: str = ""
    name: str = ""
    source_path: str = ""


@dataclass
class Project:
    id: str = field(default_factory=new_id)
    name: str = "Project mới"
    created_at: float = field(default_factory=now)
    updated_at: float = field(default_factory=now)
    source_language: str = "zh"
    target_language: str = "vi"
    translate_profile: str = ""
    translate_model: str = ""  # trống = model của profile
    context_profile: str = ""  # LLM cập nhật ngữ cảnh (trống = dùng profile dịch nếu là LLM)
    context_auto_update: bool = True
    tts_profile: str = ""
    tts_voice: str = ""
    whisper_model: str = "small"
    instructions: str = ""
    output_dir: str = ""
    template: VideoTemplate = field(default_factory=VideoTemplate)
    videos: list[VideoRef] = field(default_factory=list)


# ---------------------------------------------------------------- context


@dataclass
class GlossaryEntry:
    source: str = ""
    target: str = ""
    note: str = ""
    locked: bool = False  # người dùng đã chốt, không cho tự cập nhật ghi đè
    auto: bool = False  # do LLM thêm


@dataclass
class Character:
    source: str = ""  # tên gốc
    target: str = ""  # tên đã dịch
    gender: str = ""  # nam | nữ | khác
    role: str = ""  # vai trò / quan hệ
    addressing: str = ""  # cách xưng hô, ví dụ: "gọi A là sư phụ, tự xưng đệ tử"
    voice: str = ""  # giọng TTS riêng
    note: str = ""
    locked: bool = False
    auto: bool = False


@dataclass
class ContextLog:
    time: float = field(default_factory=now)
    video: str = ""
    message: str = ""


@dataclass
class ProjectContext:
    summary: str = ""
    style_notes: str = ""  # thể loại, giọng văn
    glossary: list[GlossaryEntry] = field(default_factory=list)
    characters: list[Character] = field(default_factory=list)
    changelog: list[ContextLog] = field(default_factory=list)
    processed_videos: list[str] = field(default_factory=list)
