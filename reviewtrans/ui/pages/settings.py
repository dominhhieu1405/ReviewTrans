from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from ...core.langs import SOURCE_LANGUAGES, TARGET_LANGUAGES, display_name
from ...core.pipeline.resources import KNOWN_WHISPER_MODELS, downloaded_whisper_models
from ..state import AppState
from ..widgets.common import ComboBox, PathEdit, SpinBox, form_layout, hint, scroll_wrap, title_label

CODECS = [
    ("libx264", "H.264 (CPU, tương thích nhất)"),
    ("libx265", "H.265 (CPU, nhỏ hơn)"),
    ("h264_nvenc", "H.264 NVIDIA NVENC"),
    ("hevc_nvenc", "H.265 NVIDIA NVENC"),
]
PRESETS = [(p, p) for p in ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow")]


class SettingsPage(QtWidgets.QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self._loading = False
        inner = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(inner)
        layout.addWidget(title_label("Cài đặt", "Cài đặt chung của ứng dụng."))

        general = QtWidgets.QGroupBox("Chung")
        form = form_layout()
        general.setLayout(form)
        self.projects_root = PathEdit("dir")
        self.source_lang = ComboBox([(c, display_name(c)) for c in SOURCE_LANGUAGES])
        self.target_lang = ComboBox([(c, display_name(c)) for c in TARGET_LANGUAGES])
        self.player = ComboBox([("auto", "Tự động (libmpv nếu có)"), ("mpv", "libmpv"), ("qt", "Qt Multimedia")])
        form.addRow("Thư mục chứa project", self.projects_root)
        form.addRow("Ngôn ngữ gốc mặc định", self.source_lang)
        form.addRow("Ngôn ngữ đích mặc định", self.target_lang)
        form.addRow("Player", self.player)
        layout.addWidget(general)

        processing = QtWidgets.QGroupBox("Xử lý")
        form = form_layout()
        processing.setLayout(form)
        self.whisper = ComboBox()
        self.whisper.setEditable(True)
        self.threads = SpinBox(1, 64, 4)
        self.batch = SpinBox(5, 300, 60, " câu")
        self.tts_concurrency = SpinBox(1, 16, 3, " luồng")
        form.addRow("Model Whisper mặc định", self.whisper)
        form.addRow("Số luồng Whisper", self.threads)
        form.addRow("Số câu mỗi lần dịch", self.batch)
        form.addRow("Luồng TTS song song", self.tts_concurrency)
        layout.addWidget(processing)

        render = QtWidgets.QGroupBox("Xuất video")
        form = form_layout()
        render.setLayout(form)
        self.codec = ComboBox(CODECS)
        self.crf = SpinBox(10, 40, 20)
        self.preset = ComboBox(PRESETS)
        self.audio_bitrate = ComboBox([(b, b) for b in ("128k", "160k", "192k", "256k", "320k")])
        self.hwaccel = QtWidgets.QCheckBox("Giải mã bằng GPU (hwaccel auto)")
        form.addRow("Codec", self.codec)
        form.addRow("Chất lượng (CRF/CQ)", self.crf)
        form.addRow("Preset x264/x265", self.preset)
        form.addRow("Bitrate âm thanh", self.audio_bitrate)
        form.addRow("", self.hwaccel)
        layout.addWidget(render)
        layout.addWidget(hint("CRF càng nhỏ chất lượng càng cao (18–23 là hợp lý). Đổi player cần khởi động lại ứng dụng."))
        layout.addStretch()
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.addWidget(scroll_wrap(inner))

        self.save_timer = QtCore.QTimer(self, singleShot=True, interval=500, timeout=self.commit)
        for widget in (self.source_lang, self.target_lang, self.player, self.codec, self.preset, self.audio_bitrate):
            widget.currentIndexChanged.connect(self.save_timer.start)
        for widget in (self.threads, self.batch, self.tts_concurrency, self.crf):
            widget.valueChanged.connect(self.save_timer.start)
        self.whisper.currentTextChanged.connect(self.save_timer.start)
        self.hwaccel.toggled.connect(self.save_timer.start)
        self.projects_root.changed.connect(self.save_timer.start)
        self.load()

    def load(self) -> None:
        s = self.state.settings
        self._loading = True
        self.projects_root.set_value(str(s.projects_root_path()))
        self.source_lang.set_value(s.default_source_language)
        self.target_lang.set_value(s.default_target_language)
        self.player.set_value(s.player_backend)
        models = sorted(set(downloaded_whisper_models() + KNOWN_WHISPER_MODELS))
        self.whisper.set_items([(m, m) for m in models], keep=False)
        self.whisper.setEditText(s.default_whisper_model)
        self.threads.setValue(s.whisper_threads)
        self.batch.setValue(s.translate_batch_size)
        self.tts_concurrency.setValue(s.tts_concurrency)
        self.codec.set_value(s.render.video_codec)
        self.crf.setValue(s.render.crf)
        self.preset.set_value(s.render.preset)
        self.audio_bitrate.set_value(s.render.audio_bitrate)
        self.hwaccel.setChecked(s.render.hwaccel_decode)
        self._loading = False

    def commit(self) -> None:
        if self._loading:
            return
        s = self.state.settings
        s.projects_root = self.projects_root.value()
        s.default_source_language = self.source_lang.value()
        s.default_target_language = self.target_lang.value()
        s.player_backend = self.player.value()
        s.default_whisper_model = self.whisper.currentText().strip() or "small"
        s.whisper_threads = self.threads.value()
        s.translate_batch_size = self.batch.value()
        s.tts_concurrency = self.tts_concurrency.value()
        s.render.video_codec = self.codec.value()
        s.render.crf = self.crf.value()
        s.render.preset = self.preset.value()
        s.render.audio_bitrate = self.audio_bitrate.value()
        s.render.hwaccel_decode = self.hwaccel.isChecked()
        self.state.save_settings()
