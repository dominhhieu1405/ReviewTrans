from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from ...core import hardware
from ...core.config import whisper_thread_count
from ...core.langs import SOURCE_LANGUAGES, TARGET_LANGUAGES, display_name
from ...core.pipeline.resources import KNOWN_WHISPER_MODELS, downloaded_whisper_models
from ..jobs import run_background
from ..state import AppState
from ..theme import SUCCESS, TEXT_DIM
from ..widgets.common import ComboBox, PathEdit, SpinBox, form_layout, hint, push_button, scroll_wrap, title_label

CODECS = hardware.CODECS
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
        self.threads = SpinBox(0, 64, 0)
        self.threads.setSpecialValueText(f"Tự động ({whisper_thread_count(0)})")
        self.asr_device = ComboBox(hardware.ASR_DEVICES)
        self.batch = SpinBox(5, 300, 60, " câu")
        self.tts_concurrency = SpinBox(1, 16, 3, " luồng")
        form.addRow("Model Whisper mặc định", self.whisper)
        form.addRow("Whisper chạy trên", self.asr_device)
        form.addRow("Số luồng CPU Whisper", self.threads)
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
        self.hwaccel = QtWidgets.QCheckBox("Giải mã video nguồn bằng GPU (hwaccel auto)")
        form.addRow("Codec", self.codec)
        form.addRow("Chất lượng (CRF/CQ)", self.crf)
        form.addRow("Preset x264/x265 (CPU)", self.preset)
        form.addRow("Bitrate âm thanh", self.audio_bitrate)
        form.addRow("", self.hwaccel)
        layout.addWidget(render)

        accel = QtWidgets.QGroupBox("Tăng tốc phần cứng")
        accel_layout = QtWidgets.QVBoxLayout(accel)
        self.hw_status = QtWidgets.QLabel("Chưa dò phần cứng.")
        self.hw_status.setWordWrap(True)
        self.hw_status.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.hw_status.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        accel_layout.addWidget(self.hw_status)
        row = QtWidgets.QHBoxLayout()
        self.hw_button = push_button("Dò lại phần cứng", lambda: self.detect_hardware(force=True), "refresh")
        row.addWidget(self.hw_button)
        row.addStretch()
        accel_layout.addLayout(row)
        accel_layout.addWidget(hint(
            "Mỗi bộ mã hoá GPU được chạy thử thật trước khi dùng. Nếu GPU lỗi khi xuất, ứng dụng tự xuất lại bằng CPU. "
            "Whisper dùng GPU cần tải bản CUDA (NVIDIA) hoặc Vulkan (AMD/Intel/NVIDIA) ở trang Tài nguyên."
        ))
        layout.addWidget(accel)
        layout.addWidget(hint("CRF càng nhỏ chất lượng càng cao (18–23 là hợp lý). Đổi player cần khởi động lại ứng dụng."))
        layout.addStretch()
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.addWidget(scroll_wrap(inner))

        self.save_timer = QtCore.QTimer(self, singleShot=True, interval=500, timeout=self.commit)
        for widget in (self.source_lang, self.target_lang, self.player, self.codec, self.preset, self.audio_bitrate, self.asr_device):
            widget.currentIndexChanged.connect(self.save_timer.start)
        for widget in (self.threads, self.batch, self.tts_concurrency, self.crf):
            widget.valueChanged.connect(self.save_timer.start)
        self.whisper.currentTextChanged.connect(self.save_timer.start)
        self.hwaccel.toggled.connect(self.save_timer.start)
        self.projects_root.changed.connect(self.save_timer.start)
        self.load()
        self._detecting = False
        self.show_hardware(hardware.load_cached())

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if hardware.load_cached() is None:
            self.detect_hardware()
        else:
            self.show_hardware(hardware.load_cached())

    def detect_hardware(self, force: bool = False) -> None:
        if self._detecting:
            return
        self._detecting = True
        self.hw_button.setEnabled(False)
        self.hw_status.setText("Đang dò GPU và chạy thử các bộ mã hoá…")

        def finish(info) -> None:
            self._detecting = False
            self.hw_button.setEnabled(True)
            self.show_hardware(info)

        def fail(message: str) -> None:
            self._detecting = False
            self.hw_button.setEnabled(True)
            self.hw_status.setText(f"Dò phần cứng lỗi: {message}")

        run_background(lambda _p, _s: hardware.detect(force=force), finish, fail)

    def show_hardware(self, info) -> None:
        if info is None:
            return
        dim = f"color:{TEXT_DIM}"
        gpus = "<br>".join(f"• {g.label} — {hardware.VENDOR_LABELS.get(g.vendor, g.vendor)}" for g in info.gpus) or "• Không thấy GPU"
        if info.cuda_version:
            gpus += f"<br><span style='{dim}'>Driver NVIDIA {info.nvidia_driver}, CUDA {info.cuda_version}</span>"
        encoders = ", ".join(f"<b style='color:{SUCCESS}'>{c}</b>" for c in info.encoders) or "không có (dùng CPU)"
        chosen, note = hardware.resolve_encoder(self.state.settings.render.video_codec, info)
        variants = hardware.whisper_variants()
        whisper = ", ".join(hardware.VARIANT_NAMES.get(v, v) for v in variants) or "chưa có"
        asr = hardware.whisper_plan(self.state.settings.asr_device, info)
        asr_text = " → ".join(hardware.VARIANT_NAMES.get(n, n) for n, _, _ in asr) or "—"
        self.hw_status.setText(
            f"<b>GPU</b><br>{gpus}<br><br>"
            f"<b>Mã hoá video bằng GPU:</b> {encoders}<br>"
            f"<span style='{dim}'>Xuất video sẽ dùng: {chosen.codec}{(' (' + chosen.device + ')') if chosen.device else ''}"
            f"{(' — ' + note) if note and not note.startswith('Tự động') else ''}</span><br><br>"
            f"<b>Whisper:</b> bản đã có: {whisper}<br><span style='{dim}'>Thứ tự thử: {asr_text}</span>"
        )

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
        self.asr_device.set_value(s.asr_device)
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
        s.asr_device = self.asr_device.value()
        s.translate_batch_size = self.batch.value()
        s.tts_concurrency = self.tts_concurrency.value()
        s.render.video_codec = self.codec.value()
        s.render.crf = self.crf.value()
        s.render.preset = self.preset.value()
        s.render.audio_bitrate = self.audio_bitrate.value()
        s.render.hwaccel_decode = self.hwaccel.isChecked()
        self.state.save_settings()
        self.show_hardware(hardware.load_cached())
