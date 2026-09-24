from __future__ import annotations

from pathlib import Path

from PyQt6 import QtCore, QtGui, QtMultimedia, QtWidgets

from ...core.langs import SOURCE_LANGUAGES, display_name
from ...core.models import (
    STAGE_LABELS,
    STATE_DONE,
    STATE_NONE,
    Segment,
    SubtitleStyle,
    VideoDoc,
    clone,
)
from ...core.pipeline import RunContext
from ...core.pipeline.mix import plan_clips, run_mix
from ...core.pipeline.render import run_render
from ...core.pipeline.resources import KNOWN_WHISPER_MODELS, downloaded_whisper_models
from ...core.pipeline.translate import translate_segments
from ...core.pipeline.tts import resolve_voice, run_tts, segment_key, tts_path, tts_resolved
from ...core.resolve import resolve
from ...core.providers.tts import create_tts
from ...core.srt import format_clock, render_srt
from ..icons import icon
from ..jobs import run_background
from ..player import create_player
from ..state import AppState
from ..theme import BORDER, STATE_COLORS, TEXT_DIM, WARNING
from ..widgets.audio_panel import AudioPanel
from ..widgets.common import (
    ComboBox,
    DoubleSpinBox,
    PathEdit,
    confirm,
    error,
    form_layout,
    hint,
    info,
    open_path,
    push_button,
    scroll_wrap,
    tool_button,
)
from ..widgets.layer_panel import LayerPanel
from ..widgets.segment_table import SegmentTable
from ..widgets.style_editor import StyleEditor
from ..widgets.timeline import TimelinePanel
from ..widgets.provider_field import ProviderField
from ..widgets.waveform import load_peaks

ALL_STEPS = ["asr", "translate", "context", "tts", "mix", "render"]


class StageButton(QtWidgets.QToolButton):
    """Nút chạy một bước, vạch màu dưới cho biết trạng thái bước đó."""

    LABELS = {"done": "xong", "stale": "cần chạy lại", "error": "lỗi", "running": "đang chạy", "none": "chưa chạy"}

    def __init__(self, stage: str, label: str, icon_name: str, callback, emphasize: bool = False):
        super().__init__()
        self.stage = stage
        self.emphasize = emphasize
        self.setText(label)
        self.setIcon(icon(icon_name))
        self.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.clicked.connect(callback)
        self.set_state(STATE_NONE)

    def set_state(self, state: str, error_text: str = "") -> None:
        color = STATE_COLORS.get(state, TEXT_DIM)
        weight = "font-weight: 600;" if self.emphasize else ""
        self.setStyleSheet(f"QToolButton {{ border-bottom: 3px solid {color}; padding: 4px 10px; {weight} }}")
        self.setToolTip(
            f"Chạy bước {STAGE_LABELS[self.stage]} — trạng thái: {self.LABELS.get(state, state)}"
            + (f"\n{error_text}" if error_text else "")
        )


class SegmentInspector(QtWidgets.QWidget):
    changed = QtCore.pyqtSignal(object, str)
    action = QtCore.pyqtSignal(str, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.segment: Segment | None = None
        self._loading = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        box = QtWidgets.QGroupBox("Câu đang chọn")
        form = form_layout()
        box.setLayout(form)
        self.title = QtWidgets.QLabel("—")
        self.start = DoubleSpinBox(0, 36000, 0, 0.05, 3, " s")
        self.end = DoubleSpinBox(0, 36000, 0, 0.05, 3, " s")
        self.source = QtWidgets.QPlainTextEdit()
        self.source.setMaximumHeight(70)
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setMaximumHeight(90)
        self.speaker = ComboBox()
        self.speaker.setEditable(True)
        self.voice = ComboBox()
        self.voice.setEditable(True)
        self.info = QtWidgets.QLabel()
        self.info.setStyleSheet(f"color: {TEXT_DIM};")
        self.info.setWordWrap(True)
        form.addRow("", self.title)
        form.addRow("Bắt đầu", self.start)
        form.addRow("Kết thúc", self.end)
        form.addRow("Câu gốc", self.source)
        form.addRow("Bản dịch", self.text)
        form.addRow("Người nói", self.speaker)
        form.addRow("Giọng riêng", self.voice)
        form.addRow("", self.info)
        layout.addWidget(box)
        buttons = QtWidgets.QGridLayout()
        for index, (key, label, name) in enumerate((
            ("translate", "Dịch lại", "translate"), ("tts", "Lồng tiếng lại", "speaker"),
            ("listen", "Nghe", "play"), ("lock", "Khoá/Mở", "lock"),
        )):
            button = push_button(label, lambda _=False, k=key: self._emit_action(k), name)
            buttons.addWidget(button, index // 2, index % 2)
        layout.addLayout(buttons)
        layout.addWidget(hint("Mẹo: nhấp đúp ô trong bảng để sửa nhanh; Ctrl+←/→ để nhảy câu; Space để phát/dừng."))
        layout.addStretch()
        self.start.valueChanged.connect(lambda _v: self._commit("start"))
        self.end.valueChanged.connect(lambda _v: self._commit("end"))
        self.source.textChanged.connect(lambda: self._commit("source"))
        self.text.textChanged.connect(lambda: self._commit("text"))
        self.speaker.currentTextChanged.connect(lambda _t: self._commit("speaker"))
        self.voice.currentTextChanged.connect(lambda _t: self._commit("voice"))
        self.setEnabled(False)

    def _emit_action(self, key: str) -> None:
        if self.segment:
            self.action.emit(key, [self.segment.id])

    def set_choices(self, speakers: list[str], voices: list[tuple[str, str]]) -> None:
        self._loading = True
        self.speaker.set_items([("", "")] + [(s, s) for s in speakers])
        self.voice.set_items([("", "(theo nhân vật / project)")] + voices)
        self._loading = False
        self.show_segment(self.segment)

    def show_segment(self, segment: Segment | None, info_text: str = "") -> None:
        self.segment = segment
        self.setEnabled(segment is not None)
        if segment is None:
            self.title.setText("—")
            return
        self._loading = True
        self.title.setText(f"Câu #{segment.id}" + ("  🔒 đã khoá" if segment.locked else ""))
        self.start.setValue(segment.start)
        self.end.setValue(segment.end)
        if self.source.toPlainText() != segment.source:
            self.source.setPlainText(segment.source)
        if self.text.toPlainText() != segment.text:
            self.text.setPlainText(segment.text)
        self.speaker.setEditText(segment.speaker)
        index = self.voice.findData(segment.voice)
        if index >= 0:
            self.voice.setCurrentIndex(index)
        else:
            self.voice.setEditText(segment.voice)
        self.info.setText(info_text)
        self._loading = False

    def _commit(self, field: str) -> None:
        if self._loading or self.segment is None:
            return
        seg = self.segment
        if field == "start":
            if self.start.value() >= seg.end:
                return
            seg.start = self.start.value()
        elif field == "end":
            if self.end.value() <= seg.start:
                return
            seg.end = self.end.value()
        elif field == "source":
            seg.source = self.source.toPlainText()
        elif field == "text":
            seg.text = self.text.toPlainText()
        elif field == "speaker":
            seg.speaker = self.speaker.currentText().strip()
        elif field == "voice":
            value = self.voice.currentData()
            seg.voice = value if isinstance(value, str) and self.voice.currentText() == self.voice.itemText(self.voice.currentIndex()) else self.voice.currentText().strip()
        self.changed.emit(seg, field)


class VideoSettingsPanel(QtWidgets.QWidget):
    changed = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc: VideoDoc | None = None
        self._loading = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        box = QtWidgets.QGroupBox("Nguồn")
        form = form_layout()
        box.setLayout(form)
        self.name = QtWidgets.QLineEdit()
        self.source = PathEdit("file", "Video (*.*)")
        self.info = QtWidgets.QLabel()
        self.info.setStyleSheet(f"color: {TEXT_DIM};")
        self.mode = ComboBox([("asr", "Nhận dạng giọng nói (Whisper)"), ("srt", "Dùng file SRT có sẵn")])
        self.srt = PathEdit("file", "Phụ đề (*.srt);;Tất cả (*.*)")
        self.language = ComboBox([("", "Theo project")] + [(c, display_name(c)) for c in SOURCE_LANGUAGES])
        self.whisper = ComboBox()
        self.whisper.setEditable(True)
        self.flip = QtWidgets.QCheckBox("Lật ngang video (tránh bản quyền)")
        form.addRow("Tên", self.name)
        form.addRow("File video", self.source)
        form.addRow("", self.info)
        form.addRow("Lấy câu thoại", self.mode)
        form.addRow("File SRT", self.srt)
        form.addRow("Ngôn ngữ gốc", self.language)
        form.addRow("Model Whisper", self.whisper)
        form.addRow("", self.flip)
        layout.addWidget(box)
        out = QtWidgets.QGroupBox("Xuất")
        out_layout = QtWidgets.QVBoxLayout(out)
        self.output = QtWidgets.QLabel("Chưa xuất")
        self.output.setWordWrap(True)
        out_layout.addWidget(self.output)
        row = QtWidgets.QHBoxLayout()
        self.open_output = push_button("Mở file", lambda: self.doc and self.doc.last_output and open_path(self.doc.last_output))
        self.open_folder = push_button("Mở thư mục", lambda: self.doc and self.doc.last_output and open_path(str(Path(self.doc.last_output).parent)))
        self.export_srt = push_button("Xuất SRT…", lambda: self.changed.emit("export_srt"))
        row.addWidget(self.open_output)
        row.addWidget(self.open_folder)
        row.addWidget(self.export_srt)
        out_layout.addLayout(row)
        layout.addWidget(out)
        layout.addStretch()
        self.name.editingFinished.connect(lambda: self._commit("name"))
        self.source.changed.connect(lambda _p: self._commit("source"))
        self.mode.currentIndexChanged.connect(lambda _i: self._commit("mode"))
        self.srt.changed.connect(lambda _p: self._commit("srt"))
        self.language.currentIndexChanged.connect(lambda _i: self._commit("language"))
        self.whisper.currentTextChanged.connect(lambda _t: self._commit("whisper"))
        self.flip.toggled.connect(lambda _c: self._commit("flip"))

    def set_document(self, doc: VideoDoc | None, project_whisper: str = "") -> None:
        self.doc = doc
        if doc is None:
            return
        self._loading = True
        self.name.setText(doc.name)
        self.source.set_value(doc.source_path)
        self.info.setText(f"{doc.width}×{doc.height} · {doc.fps:.2f} fps · {format_clock(doc.duration)}")
        self.mode.set_value(doc.source_mode)
        self.srt.set_value(doc.srt_path)
        self.srt.setEnabled(doc.source_mode == "srt")
        self.language.set_value(doc.source_language)
        models = sorted(set(downloaded_whisper_models() + KNOWN_WHISPER_MODELS))
        self.whisper.set_items([("", f"Theo project ({project_whisper})")] + [(m, m) for m in models], keep=False)
        self.whisper.set_value(doc.whisper_model)
        self.flip.setChecked(doc.flip_horizontal)
        self.output.setText(doc.last_output or "Chưa xuất")
        self._loading = False

    def _commit(self, field: str) -> None:
        if self._loading or self.doc is None:
            return
        d = self.doc
        d.name = self.name.text().strip() or d.name
        d.source_path = self.source.value() or d.source_path
        d.source_mode = self.mode.value()
        self.srt.setEnabled(d.source_mode == "srt")
        d.srt_path = self.srt.value()
        d.source_language = self.language.value() or ""
        value = self.whisper.currentData()
        d.whisper_model = value if value is not None else self.whisper.currentText().strip()
        d.flip_horizontal = self.flip.isChecked()
        self.changed.emit(field)


class EditorPage(QtWidgets.QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.doc: VideoDoc | None = None
        self.segments: list[Segment] = []
        self.dirty = False
        self.read_only = False
        self._override_signature = ""
        self._pending_reload = False
        self._selected_segment = -1
        self._voices: list[tuple[str, str]] = []

        self.save_timer = QtCore.QTimer(self, singleShot=True, interval=800, timeout=self.save_now)
        self.status_timer = QtCore.QTimer(self, singleShot=True, interval=400, timeout=self._refresh_tts_status)
        self.reload_timer = QtCore.QTimer(self, singleShot=True, interval=500, timeout=self._reload_from_disk)
        # bản nghe thử: tự trộn lại ở nền mỗi khi âm lượng/track/thời gian câu đổi
        self.remix_timer = QtCore.QTimer(self, singleShot=True, interval=900, timeout=self._auto_remix)
        self._preview_version = 0
        self._preview_path: Path | None = None
        self._remix_running = False

        self.backend, self.canvas, fallback_reason = create_player(state.settings.player_backend)
        self.player_note = fallback_reason
        self.clip_player = QtMultimedia.QMediaPlayer(self)
        self.clip_audio = QtMultimedia.QAudioOutput(self)
        self.clip_player.setAudioOutput(self.clip_audio)

        self._build_ui()
        self._connect()
        self._install_shortcuts()
        self.set_empty()

    # ================================================================ UI

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        top = QtWidgets.QHBoxLayout()
        self.video_combo = ComboBox()
        self.video_combo.setMinimumWidth(260)
        top.addWidget(tool_button("prev", "Video trước", lambda: self._step_video(-1)))
        top.addWidget(self.video_combo)
        top.addWidget(tool_button("next", "Video sau", lambda: self._step_video(1)))
        top.addSpacing(12)
        self.provider_badge = QtWidgets.QToolButton()
        self.provider_badge.setToolTip("Provider dịch/TTS đang dùng cho video này — bấm để đổi (tab Provider)")
        self.provider_badge.setStyleSheet("QToolButton { border: none; color: #9aa0a8; text-align: left; }")
        self.provider_badge.clicked.connect(lambda: self.inspector.setCurrentWidget(self.provider_tab))
        top.addWidget(self.provider_badge, 1)
        top.addStretch()
        stage_defs = [
            ("asr", "Nhận dạng", "mic", ["asr"]),
            ("translate", "Dịch", "translate", ["translate", "context"]),
            ("tts", "Lồng tiếng", "speaker", ["tts"]),
            ("mix", "Trộn âm", "mix", ["mix"]),
            ("render", "Xuất video", "export", ["mix", "render"]),
        ]
        self.chips: dict[str, StageButton] = {}
        self.run_buttons: list[QtWidgets.QAbstractButton] = []
        for stage, label, name, steps in stage_defs:
            button = StageButton(stage, label, name, lambda _=False, st=steps: self.run_steps(st), emphasize=stage == "render")
            self.chips[stage] = button
            self.run_buttons.append(button)
            top.addWidget(button)
        run_all = QtWidgets.QToolButton()
        run_all.setText("Chạy tất cả ▾")
        run_all.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QtWidgets.QMenu(run_all)
        menu.addAction("Chạy tất cả bước còn thiếu", lambda: self.run_steps(ALL_STEPS))
        menu.addAction("Dịch lại toàn bộ (ghi đè, trừ câu khoá)", lambda: self.run_steps(["translate", "context"], only_missing=False))
        menu.addAction("Tạo lại toàn bộ lồng tiếng", lambda: self.run_steps(["tts"], only_missing=False))
        menu.addAction("Cập nhật ngữ cảnh project từ video này", lambda: self.run_steps(["context"], force_context=True))
        menu.addSeparator()
        menu.addAction("Xuất thử 15 giây từ vị trí phát", self.export_preview)
        menu.addSeparator()
        menu.addAction("Lưu style + layer + âm thanh làm mẫu project", self.save_as_template)
        menu.addAction("Áp style + layer + âm thanh cho mọi video khác", self.apply_to_all_videos)
        run_all.setMenu(menu)
        self.run_buttons.append(run_all)
        top.addWidget(run_all)
        root.addLayout(top)

        self.banner = QtWidgets.QLabel()
        self.banner.setStyleSheet(
            f"QLabel {{ background: #3a3020; color: {WARNING}; border: 1px solid {WARNING}; border-radius: 4px; padding: 4px 8px; }}"
        )
        self.banner.hide()
        root.addWidget(self.banner)

        self.empty = QtWidgets.QLabel("Mở một project và chọn video để bắt đầu chỉnh sửa.")
        self.empty.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet(f"color: {TEXT_DIM}; font-size: 15px;")
        root.addWidget(self.empty, 1)

        self.body = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        root.addWidget(self.body, 1)

        upper = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.upper_splitter = upper
        self._layout_restored = False
        self._user_layout = False  # người dùng đã tự kéo thanh chia → không tự chia lại nữa
        upper.splitterMoved.connect(self._on_user_split)
        # --- trái: danh sách câu
        left = QtWidgets.QWidget()
        left.setMinimumWidth(340)  # đủ chỗ cho cột câu gốc + bản dịch
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        search_row = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Tìm trong câu gốc / bản dịch…")
        self.search.setClearButtonEnabled(True)
        search_row.addWidget(self.search)
        self.count_label = QtWidgets.QLabel()
        self.count_label.setStyleSheet(f"color: {TEXT_DIM};")
        search_row.addWidget(self.count_label)
        left_layout.addLayout(search_row)
        self.table = SegmentTable()
        left_layout.addWidget(self.table, 1)
        upper.addWidget(left)

        # --- giữa: player
        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        frame = QtWidgets.QFrame()
        frame.setStyleSheet(f"QFrame {{ background: #000; border: 1px solid {BORDER}; }}")
        frame_layout = QtWidgets.QVBoxLayout(frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.addWidget(self.canvas)
        center_layout.addWidget(frame, 1)
        # hàng 1: điều khiển phát + âm lượng
        transport = QtWidgets.QHBoxLayout()
        transport.addWidget(tool_button("prev", "Câu trước (Ctrl+←)", lambda: self.jump_segment(-1)))
        self.play_button = tool_button("play", "Phát / dừng (Space)", self.backend.toggle)
        transport.addWidget(self.play_button)
        transport.addWidget(tool_button("next", "Câu sau (Ctrl+→)", lambda: self.jump_segment(1)))
        self.time_label = QtWidgets.QLabel("00:00.0 / 00:00.0")
        self.time_label.setMinimumWidth(120)
        transport.addWidget(self.time_label)
        transport.addStretch()
        volume_icon = QtWidgets.QLabel()
        volume_icon.setPixmap(icon("volume").pixmap(16, 16))
        volume_icon.setToolTip("Âm lượng xem thử")
        transport.addWidget(volume_icon)
        self.volume = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(90)
        self.volume.setFixedWidth(90)
        transport.addWidget(self.volume)
        center_layout.addLayout(transport)
        # hàng 2: bật/tắt track khi xuất + chế độ nghe
        options = QtWidgets.QHBoxLayout()
        self.sub_toggle = tool_button("text", "Bật/tắt phụ đề trong video xuất", checkable=True, text="Phụ đề")
        self.dub_toggle = tool_button("speaker", "Bật/tắt lồng tiếng trong video xuất", checkable=True, text="Lồng tiếng")
        options.addWidget(self.sub_toggle)
        options.addWidget(self.dub_toggle)
        options.addStretch()
        self.listen_mode = ComboBox([("mix", "Nghe: bản trộn"), ("original", "Nghe: âm gốc")])
        self.listen_mode.setToolTip("Bản trộn = âm gốc đã chỉnh + lồng tiếng + nhạc nền, giống khi xuất")
        options.addWidget(self.listen_mode)
        self.show_source = QtWidgets.QCheckBox("Hiện câu gốc")
        options.addWidget(self.show_source)
        center_layout.addLayout(options)
        self.player_label = QtWidgets.QLabel()
        self.player_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        self.player_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        center_layout.addWidget(self.player_label)
        upper.addWidget(center)

        # --- phải: inspector
        self.inspector = QtWidgets.QTabWidget()
        self.segment_panel = SegmentInspector()
        self.style_editor = StyleEditor()
        style_page = QtWidgets.QWidget()
        style_layout = QtWidgets.QVBoxLayout(style_page)
        style_layout.setContentsMargins(4, 4, 4, 4)
        preset_row = QtWidgets.QHBoxLayout()
        self.preset_combo = ComboBox()
        preset_row.addWidget(self.preset_combo, 1)
        preset_row.addWidget(push_button("Áp", self._apply_preset))
        preset_row.addWidget(push_button("Lưu preset", self._save_preset))
        style_layout.addLayout(preset_row)
        style_layout.addWidget(self.style_editor)
        self.layer_panel = LayerPanel()
        self.audio_panel = AudioPanel()
        self.video_panel = VideoSettingsPanel()
        provider_page = QtWidgets.QWidget()
        provider_layout = QtWidgets.QVBoxLayout(provider_page)
        provider_layout.setContentsMargins(4, 4, 4, 4)
        provider_layout.addWidget(hint(
            "Cấu hình riêng cho video này. Thứ tự ưu tiên: video > project > mặc định (trang Providers). "
            "Chọn “Theo project (…)” để kế thừa."
        ))
        box = QtWidgets.QGroupBox("Dịch")
        box_layout = QtWidgets.QVBoxLayout(box)
        self.translate_field = ProviderField(self.state, "translate", "video")
        box_layout.addWidget(self.translate_field)
        provider_layout.addWidget(box)
        box = QtWidgets.QGroupBox("Lồng tiếng (TTS)")
        box_layout = QtWidgets.QVBoxLayout(box)
        self.tts_field = ProviderField(self.state, "tts", "video")
        box_layout.addWidget(self.tts_field)
        provider_layout.addWidget(box)
        provider_layout.addWidget(hint("Đổi provider/giọng TTS sẽ đánh dấu các câu cần lồng tiếng lại (cột TTS hiện “cũ”)."))
        provider_layout.addStretch()
        for widget, label in (
            (self.segment_panel, "Câu"), (style_page, "Phụ đề"), (self.layer_panel, "Layer"),
            (self.audio_panel, "Âm thanh"), (self.video_panel, "Video"), (provider_page, "Provider"),
        ):
            self.inspector.addTab(scroll_wrap(widget), label)
        self.provider_tab = self.inspector.widget(self.inspector.count() - 1)
        self.inspector.setMinimumWidth(350)
        upper.addWidget(self.inspector)
        upper.setStretchFactor(0, 3)
        upper.setStretchFactor(1, 5)
        upper.setStretchFactor(2, 3)
        upper.setSizes([520, 660, 370])
        self.body.addWidget(upper)

        self.timeline_panel = TimelinePanel()
        self.timeline = self.timeline_panel.timeline
        self.body.addWidget(self.timeline_panel)
        self.body.setStretchFactor(0, 3)
        self.body.setStretchFactor(1, 1)
        self.body.setSizes([560, 260])

    def _connect(self) -> None:
        s = self.state
        s.projectOpened.connect(self._on_project_opened)
        s.projectChanged.connect(self._refresh_video_combo)
        s.videoChanged.connect(self._on_job_video_changed)
        s.openVideoRequested.connect(self.open_video)
        s.flushRequested.connect(self.save_now)
        s.jobs.videoLocksChanged.connect(self._update_lock)
        s.contextChanged.connect(self._refresh_choices)
        s.settingsChanged.connect(self._refresh_presets)
        s.settingsChanged.connect(self._refresh_choices)
        s.settingsChanged.connect(self._update_provider_badge)
        s.projectChanged.connect(self._update_provider_badge)
        s.projectChanged.connect(self.status_timer.start)
        self.video_combo.currentIndexChanged.connect(self._on_video_combo)

        b = self.backend
        b.positionChanged.connect(self._on_position)
        b.durationChanged.connect(lambda _d: self._update_time_label())
        b.playingChanged.connect(lambda playing: self.play_button.setIcon(icon("pause" if playing else "play")))
        b.errorOccurred.connect(lambda msg: self.player_label.setText(f"{self._player_text()} — {msg}"))
        self.volume.valueChanged.connect(b.set_volume)
        b.set_volume(self.volume.value())

        ev = self.canvas.events
        ev.layerSelected.connect(self._select_layer)
        ev.layerMoving.connect(lambda lid: self.layer_panel.reload_geometry(lid))
        ev.layerEdited.connect(self._on_layer_geometry)

        self.table.segmentActivated.connect(self._on_segment_clicked)
        self.table.contextAction.connect(self.segment_action)
        self.table.model_.edited.connect(self._on_segment_edited)
        self.search.textChanged.connect(self.table.set_filter)

        self.segment_panel.changed.connect(self._on_segment_edited)
        self.segment_panel.action.connect(self.segment_action)

        tl = self.timeline
        tl.seekRequested.connect(self.seek)
        tl.segmentSelected.connect(self._on_segment_clicked)
        tl.segmentsEdited.connect(self._on_timeline_segments)
        tl.layerSelected.connect(self._select_layer)
        tl.layerEdited.connect(self._on_layer_edited)
        tl.layerToggled.connect(self._on_layer_edited)
        tl.tracksChanged.connect(self._on_tracks_changed)

        self.style_editor.styleChanged.connect(self._on_style)
        self.layer_panel.layersChanged.connect(self._on_layers_structure)
        self.layer_panel.layerChanged.connect(self._on_layer_edited)
        self.layer_panel.layerSelected.connect(self._select_layer)
        self.audio_panel.audioChanged.connect(self._on_audio)
        self.video_panel.changed.connect(self._on_video_settings)
        self.listen_mode.currentIndexChanged.connect(lambda _i: self._apply_audio_override(force=True))
        self.show_source.toggled.connect(self._on_show_source)
        self.sub_toggle.toggled.connect(lambda on: self._set_track("subtitles", on))
        self.dub_toggle.toggled.connect(lambda on: self._set_track("dub", on))

    def _install_shortcuts(self) -> None:
        def add(seq, fn):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(seq), self)
            shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(fn)

        add("Space", self.backend.toggle)
        add("Ctrl+Left", lambda: self.jump_segment(-1))
        add("Ctrl+Right", lambda: self.jump_segment(1))
        add("Alt+Left", lambda: self.seek(self.backend.position() - 3))
        add("Alt+Right", lambda: self.seek(self.backend.position() + 3))
        add("Ctrl+S", self.save_now)

    def _player_text(self) -> str:
        text = f"Player: {self.backend.name}"
        if self.player_note:
            text += " (chưa có libmpv — vào Tài nguyên để tải, phát mượt hơn)"
        return text

    # ================================================================ project / video

    def _on_project_opened(self) -> None:
        self.save_now()
        self.close_video()
        self._refresh_video_combo()
        self._refresh_presets()
        self._refresh_choices()
        project = self.state.project
        if project and project.videos:
            self.open_video(project.videos[0].id)

    def _refresh_video_combo(self) -> None:
        project = self.state.project
        current = self.doc.id if self.doc else None
        items = [(v.id, f"{i + 1}. {v.name}") for i, v in enumerate(project.videos)] if project else []
        self.video_combo.blockSignals(True)
        self.video_combo.set_items(items, keep=False)
        if current:
            self.video_combo.set_value(current)
        self.video_combo.blockSignals(False)
        if self.doc and project and all(v.id != self.doc.id for v in project.videos):
            self.close_video()

    def _on_video_combo(self, _index: int) -> None:
        video_id = self.video_combo.value()
        if video_id and (not self.doc or video_id != self.doc.id):
            self.open_video(video_id)

    def _step_video(self, direction: int) -> None:
        index = self.video_combo.currentIndex() + direction
        if 0 <= index < self.video_combo.count():
            self.video_combo.setCurrentIndex(index)

    def set_empty(self) -> None:
        self.empty.show()
        self.body.hide()
        for chip in self.chips.values():
            chip.set_state(STATE_NONE)
        for button in self.run_buttons:
            button.setEnabled(False)

    def close_video(self) -> None:
        self.save_now()
        self.backend.unload()
        self.doc = None
        self.translate_field.set_target(None)
        self.tts_field.set_target(None)
        self.segments = []
        self.canvas.set_document(None, [])
        self.timeline.set_document(None, [])
        self.table.model_.set_segments([])
        self.set_empty()

    def open_video(self, video_id: str) -> None:
        store = self.state.store
        if not store:
            return
        self.save_now()
        try:
            doc = store.load_video(video_id)
            segments = store.load_segments(video_id)
        except (OSError, FileNotFoundError) as exc:
            error(self, str(exc))
            return
        self.doc = doc
        self.segments = segments
        self.dirty = False
        self._preview_version += 1
        self._preview_path = None
        self.empty.hide()
        self.body.show()
        self.video_combo.blockSignals(True)
        self.video_combo.set_value(video_id)
        self.video_combo.blockSignals(False)
        self._bind_document(reset_player=True)
        self._load_waveform()
        self._update_lock()

    def _bind_document(self, reset_player: bool) -> None:
        doc = self.doc
        if doc is None:
            return
        self.table.model_.set_segments(self.segments)
        self.count_label.setText(f"{len(self.segments)} câu")
        self.canvas.set_document(doc, self.segments)
        self.canvas.invalidate_images()
        self.timeline.set_document(doc, self.segments)
        self.style_editor.set_style(doc.style)
        self.layer_panel.set_document(doc)
        self.audio_panel.set_document(doc)
        project = self.state.project
        self.video_panel.set_document(doc, project.whisper_model if project else "")
        self.translate_field.set_target(doc, on_save=lambda: self._on_provider_changed("translate"))
        self.tts_field.set_target(doc, on_save=lambda: self._on_provider_changed("tts"))
        self._update_provider_badge()
        self._update_chips()
        self._sync_track_toggles()
        if reset_player:
            if doc.source_path and Path(doc.source_path).exists():
                self.backend.load(doc.source_path)
            else:
                self.player_label.setText(f"Không tìm thấy file video: {doc.source_path}")
            self._override_signature = ""
            QtCore.QTimer.singleShot(60, self.timeline.zoom_fit)
        self.player_label.setText(self._player_text())
        self._apply_audio_override(force=reset_player)
        self.schedule_remix(immediate=reset_player)
        self.status_timer.start()
        self._show_selected_segment()

    def _load_waveform(self) -> None:
        store, doc = self.state.store, self.doc
        if not store or not doc:
            return
        audio = store.cache_dir(doc.id) / "audio16k.wav"
        if not audio.exists():
            self.timeline.set_peaks([], 20)
            return
        video_id = doc.id
        run_background(
            lambda _p, _s: load_peaks(audio, 20)[0],
            on_done=lambda peaks: self.doc and self.doc.id == video_id and self.timeline.set_peaks(peaks, 20),
        )

    def _update_chips(self) -> None:
        if not self.doc:
            return
        for stage, chip in self.chips.items():
            chip.set_state(self.doc.stage(stage), self.doc.stage_errors.get(stage, ""))

    # ================================================================ lưu / khoá

    def mark_dirty(self, stale_from: str | None = None) -> None:
        if not self.doc or self.read_only:
            return
        if stale_from:
            self.doc.mark_stale_from(stale_from)
            self._update_chips()
        self.dirty = True
        self.save_timer.start()

    def save_now(self) -> None:
        self.save_timer.stop()
        store = self.state.store
        if not (self.dirty and self.doc and store) or self.read_only:
            return
        store.save_video(self.doc)
        store.save_segments(self.doc.id, self.segments)
        project = self.state.project
        if project:
            for ref in project.videos:
                if ref.id == self.doc.id and (ref.name != self.doc.name or ref.source_path != self.doc.source_path):
                    ref.name, ref.source_path = self.doc.name, self.doc.source_path
                    self.state.save_project()
        self.dirty = False

    def _update_lock(self) -> None:
        locked = bool(self.doc) and self.state.is_video_locked(self.doc.id)
        self.read_only = locked
        self.banner.setVisible(locked)
        if locked:
            self.banner.setText("Video đang được xử lý trong hàng đợi — tạm khoá chỉnh sửa, dữ liệu sẽ tự cập nhật.")
        self.table.model_.read_only = locked
        self.timeline.read_only = locked
        for widget in (self.layer_panel, self.audio_panel, self.video_panel, self.style_editor, self.segment_panel,
                       self.translate_field, self.tts_field):
            widget.setEnabled(not locked)
        for button in self.run_buttons:
            button.setEnabled(bool(self.doc) and not locked)
        if not locked and self._pending_reload:
            self._reload_from_disk()

    def _on_job_video_changed(self, video_id: str) -> None:
        if self.doc and video_id == self.doc.id:
            self._pending_reload = True
            self.reload_timer.start()

    def _reload_from_disk(self) -> None:
        store = self.state.store
        if not (store and self.doc and self._pending_reload):
            return
        self._pending_reload = False
        try:
            doc = store.load_video(self.doc.id)
            segments = store.load_segments(self.doc.id)
        except (OSError, FileNotFoundError):
            return
        selected_layer = self.canvas.selected
        self.doc, self.segments = doc, segments
        self.dirty = False
        self._bind_document(reset_player=False)
        if selected_layer:
            self._select_layer(selected_layer)
        if not self.timeline.peaks:
            self._load_waveform()

    # ================================================================ player

    def seek(self, seconds: float) -> None:
        if not self.doc:
            return
        seconds = max(0.0, min(self.doc.duration or seconds, seconds))
        self.backend.seek(seconds)
        self._on_position(seconds)

    def _on_position(self, seconds: float) -> None:
        self.canvas.set_time(seconds)
        self.timeline.set_time(seconds, follow=self.backend.is_playing())
        self.layer_panel.current_time = seconds
        self._update_time_label(seconds)
        seg = self.canvas.current_segment()
        self.table.model_.set_active(seg.id if seg else -1)
        if seg and self.backend.is_playing():
            self.table.follow(seg.id)

    def _update_time_label(self, seconds: float | None = None) -> None:
        position = self.backend.position() if seconds is None else seconds
        duration = self.backend.duration() or (self.doc.duration if self.doc else 0)
        self.time_label.setText(f"{format_clock(position)} / {format_clock(duration)}")

    def jump_segment(self, direction: int) -> None:
        if not self.segments:
            return
        now = self.backend.position()
        ordered = sorted(self.segments, key=lambda s: s.start)
        if direction > 0:
            target = next((s for s in ordered if s.start > now + 0.05), None)
        else:
            previous = [s for s in ordered if s.start < now - 0.3]
            target = previous[-1] if previous else (ordered[0] if ordered else None)
        if target:
            self.seek(target.start)
            self._on_segment_clicked(target.id, seek=False)

    def _apply_audio_override(self, force: bool = False) -> None:
        if not (self.state.store and self.doc):
            return
        preview = self._preview_path
        use_mix = self.listen_mode.value() == "mix" and preview is not None and preview.exists()
        signature = str(preview) if use_mix else "original"
        if signature != self._override_signature or force:
            self._override_signature = signature
            self.backend.set_audio_override(str(preview) if use_mix else None)
        self._update_player_note()

    def _update_player_note(self) -> None:
        note = self._player_text()
        if self.listen_mode.value() == "mix":
            if self._remix_running or self.remix_timer.isActive():
                note += " — đang trộn lại bản nghe thử…"
            elif self._preview_path is None:
                note += " — chưa trộn được bản nghe thử, đang phát âm gốc"
        else:
            note += " — đang nghe âm gốc chưa chỉnh"
        self.player_label.setText(note)

    def schedule_remix(self, immediate: bool = False) -> None:
        """Trộn lại bản nghe thử (âm gốc đã chỉnh + lồng tiếng + nhạc nền) sau một nhịp nghỉ ngắn."""
        if not self.doc:
            return
        self._preview_version += 1
        self.remix_timer.start(50 if immediate else 900)
        self._update_player_note()

    def _auto_remix(self) -> None:
        store, doc = self.state.store, self.doc
        if not (store and doc):
            return
        if self._remix_running:  # đợi lượt đang chạy xong rồi trộn tiếp
            self.remix_timer.start(400)
            return
        if not doc.has_audio and not any(s.tts_file for s in self.segments) and not doc.audio.bgm_path:
            self._update_player_note()
            return
        version = self._preview_version
        video_id = doc.id
        doc_copy, segments = clone(doc), clone(self.segments)
        ctx = self._ctx()
        folder = store.cache_dir(video_id) / "preview"
        target = folder / f"mix_{version}.wav"
        self._remix_running = True
        self._update_player_note()

        def work(progress, stop):
            ctx.progress = progress
            ctx.stop_event = stop
            return run_mix(ctx, doc_copy, segments, target=target)

        def finish(path) -> None:
            self._remix_running = False
            if self.doc is None or self.doc.id != video_id or version != self._preview_version:
                return  # đã có thay đổi mới hơn → lượt sau sẽ dùng
            self._preview_path = Path(path)
            self._apply_audio_override()
            for old in folder.glob("mix_*"):
                if old != self._preview_path and old.name != f"{self._preview_path.stem}_graph.txt":
                    try:
                        old.unlink()
                    except OSError:
                        pass  # player còn giữ file cũ, lần sau dọn

        def fail(message: str) -> None:
            self._remix_running = False
            self._update_player_note()
            self.player_label.setText(self.player_label.text() + f" — lỗi trộn: {message[:120]}")

        run_background(work, finish, fail)

    def _on_show_source(self, checked: bool) -> None:
        self.canvas.show_source = checked
        self.canvas.update()

    # ================================================================ câu thoại

    def _segment(self, segment_id: int) -> Segment | None:
        return next((s for s in self.segments if s.id == segment_id), None)

    def _on_segment_clicked(self, segment_id: int, seek: bool = True) -> None:
        self._selected_segment = segment_id
        self.timeline.selected_segment = segment_id
        self.timeline.update()
        self.table.select_segment(segment_id)
        seg = self._segment(segment_id)
        if seg and seek and not self.backend.is_playing():
            self.seek(seg.start + 0.01)
        self._show_selected_segment()

    def _show_selected_segment(self) -> None:
        seg = self._segment(self._selected_segment)
        info_text = ""
        if seg and self.doc:
            clip = next((c for c in self.timeline.clips if c.segment is seg), None)
            if clip:
                info_text = f"Lồng tiếng {seg.tts_duration:.2f}s, phát ở tốc độ x{clip.tempo:.2f}"
            elif seg.text:
                info_text = "Chưa có lồng tiếng"
        self.segment_panel.show_segment(seg, info_text)

    def _on_segment_edited(self, seg: Segment, field: str) -> None:
        if field in ("text", "voice", "speaker"):
            self.mark_dirty("tts")
        elif field in ("start", "end"):
            self.mark_dirty("mix")
            self.schedule_remix()
            self.timeline.set_segments(self.segments)
            self.canvas.set_segments(self.segments)
        elif field == "source":
            self.mark_dirty()
        self.table.model_.refresh_row(seg)
        self.timeline.refresh_clips()
        self.canvas.update()
        if self.sender() is not self.segment_panel:
            self._show_selected_segment()
        self.status_timer.start()

    def _on_timeline_segments(self, ids: list) -> None:
        self.mark_dirty("mix")
        self.schedule_remix()
        self.canvas.set_segments(self.segments)
        for seg in self.segments:
            if seg.id in ids:
                self.table.model_.refresh_row(seg)
        self._show_selected_segment()

    def segment_action(self, action: str, ids: list) -> None:
        if not self.doc:
            return
        targets = [s for s in self.segments if s.id in ids]
        if not targets:
            return
        if action == "play":
            self.seek(targets[0].start)
            self.backend.play()
            return
        if action == "listen":
            path = tts_path(self._ctx(), self.doc, targets[0])
            if path is None:
                info(self, "Câu này chưa có lồng tiếng.")
                return
            self.clip_player.setSource(QtCore.QUrl.fromLocalFile(str(path)))
            self.clip_player.play()
            return
        if self.read_only:
            return
        if action == "translate":
            self._translate_selected(targets)
        elif action == "tts":
            self._tts_selected(targets)
        elif action == "lock":
            value = not all(s.locked for s in targets)
            for seg in targets:
                seg.locked = value
            self._after_bulk_edit(None)
        elif action == "copy_source":
            for seg in targets:
                seg.text = seg.source
            self._after_bulk_edit("tts")
        elif action == "clear_text":
            for seg in targets:
                seg.text = ""
            self._after_bulk_edit("tts")
        elif action == "insert":
            self._insert_after(targets[-1])
        elif action == "split":
            self._split(targets[0])
        elif action == "merge":
            self._merge(targets)
        elif action == "delete":
            if len(targets) > 3 and not confirm(self, "Xoá câu", f"Xoá {len(targets)} câu đã chọn?"):
                return
            self.segments = [s for s in self.segments if s.id not in ids]
            self._renumber()

    def _after_bulk_edit(self, stale_from: str | None) -> None:
        self.mark_dirty(stale_from)
        if stale_from in ("mix", "tts"):
            self.schedule_remix()
        self.table.model_.set_segments(self.segments)
        self.timeline.set_segments(self.segments)
        self.canvas.set_segments(self.segments)
        self.count_label.setText(f"{len(self.segments)} câu")
        self._show_selected_segment()
        self.status_timer.start()

    def _renumber(self) -> None:
        self.segments.sort(key=lambda s: s.start)
        for index, seg in enumerate(self.segments, start=1):
            seg.id = index
        self._after_bulk_edit("mix")

    def _insert_after(self, seg: Segment) -> None:
        ordered = sorted(self.segments, key=lambda s: s.start)
        following = next((s for s in ordered if s.start > seg.start), None)
        start = seg.end + 0.05
        end = min(start + 2.0, following.start - 0.05 if following else (self.doc.duration or start + 2))
        if end - start < 0.2:
            end = start + 0.5
        self.segments.append(Segment(id=max((s.id for s in self.segments), default=0) + 1, start=start, end=end))
        self._renumber()

    def _split(self, seg: Segment) -> None:
        t = self.backend.position()
        if not (seg.start + 0.1 < t < seg.end - 0.1):
            t = (seg.start + seg.end) / 2
        ratio = (t - seg.start) / max(0.01, seg.end - seg.start)

        def cut(text: str) -> tuple[str, str]:
            if not text:
                return "", ""
            pos = int(len(text) * ratio)
            space = text.rfind(" ", 0, pos + 1)
            if space > 0 and abs(space - pos) < 12:
                pos = space
            return text[:pos].strip(), text[pos:].strip()

        s1, s2 = cut(seg.source)
        t1, t2 = cut(seg.text)
        second = Segment(id=len(self.segments) + 1, start=t, end=seg.end, source=s2, text=t2, speaker=seg.speaker)
        seg.end, seg.source, seg.text = t, s1, t1
        seg.tts_file = seg.tts_key = ""
        seg.tts_duration = 0.0
        self.segments.append(second)
        self._renumber()
        self.doc.mark_stale_from("tts")

    def _merge(self, targets: list[Segment]) -> None:
        if len(targets) < 2:
            return
        targets = sorted(targets, key=lambda s: s.start)
        first = targets[0]
        first.end = max(s.end for s in targets)
        first.source = " ".join(s.source for s in targets if s.source)
        first.text = " ".join(s.text for s in targets if s.text)
        first.tts_file = first.tts_key = ""
        first.tts_duration = 0.0
        remove = {id(s) for s in targets[1:]}
        self.segments = [s for s in self.segments if id(s) not in remove]
        self._renumber()
        self.doc.mark_stale_from("tts")

    def _ctx(self) -> RunContext:
        return RunContext(store=self.state.store, project=self.state.project, settings=self.state.settings_store.snapshot())

    def _translate_selected(self, targets: list[Segment]) -> None:
        doc = clone(self.doc)
        copies = clone(self.segments)
        ids = {s.id for s in targets}
        ctx = self._ctx()

        def work(progress, stop):
            ctx.progress = progress
            ctx.stop_event = stop
            chosen = [c for c in copies if c.id in ids]
            translate_segments(ctx, doc, copies, chosen)
            return {c.id: c.text for c in chosen}

        def done(result: dict) -> None:
            for seg in self.segments:
                if seg.id in result:
                    seg.text = result[seg.id]
            self._after_bulk_edit("tts")
            self.window().statusBar().showMessage(f"Đã dịch lại {len(result)} câu", 4000)

        self.window().statusBar().showMessage(f"Đang dịch {len(targets)} câu…")
        run_background(work, done, lambda msg: error(self, msg, "Dịch thất bại"))

    def _tts_selected(self, targets: list[Segment]) -> None:
        self.save_now()
        doc = clone(self.doc)
        copies = clone(self.segments)
        ids = {s.id for s in targets}
        ctx = self._ctx()

        def work(progress, stop):
            ctx.progress = progress
            ctx.stop_event = stop
            chosen = [c for c in copies if c.id in ids]
            _done, failed = run_tts(ctx, doc, copies, chosen, force=True)
            return {c.id: (c.tts_file, c.tts_duration, c.tts_key) for c in chosen}, failed

        def done(result) -> None:
            data, failed = result
            for seg in self.segments:
                if seg.id in data:
                    seg.tts_file, seg.tts_duration, seg.tts_key = data[seg.id]
            self._after_bulk_edit("mix")
            message = f"Đã tạo lồng tiếng {len(data) - failed} câu" + (f", {failed} lỗi" if failed else "")
            self.window().statusBar().showMessage(message + " — bấm “Trộn âm” để nghe trong bản trộn", 6000)
            if len(targets) == 1:
                self.segment_action("listen", [targets[0].id])

        self.window().statusBar().showMessage(f"Đang tạo lồng tiếng {len(targets)} câu…")
        run_background(work, done, lambda msg: error(self, msg, "Lồng tiếng thất bại"))

    def _refresh_tts_status(self) -> None:
        if not (self.doc and self.state.store and self.state.project):
            return
        ctx = self._ctx()
        try:
            resolved = tts_resolved(ctx, self.doc)
            profile = resolved.profile
            provider = create_tts(profile)
        except Exception:  # noqa: BLE001
            self.table.model_.set_tts_status({})
            return
        context = self.state.load_context()
        clips = {c.segment.id: c for c in plan_clips(self.doc, self.segments)}
        status = {}
        for seg in self.segments:
            if not seg.tts_file or tts_path(ctx, self.doc, seg) is None:
                status[seg.id] = "missing"
                continue
            key = segment_key(seg, profile, resolve_voice(seg, context, provider, resolved.value), self.doc.audio.speed)
            if key != seg.tts_key:
                status[seg.id] = "stale"
            elif seg.id in clips and clips[seg.id].tempo > 1.35:
                status[seg.id] = "fast"
            else:
                status[seg.id] = "ok"
        self.table.model_.set_tts_status(status)

    def _refresh_choices(self) -> None:
        if not self.state.project:
            return
        context = self.state.load_context()
        speakers = sorted({c.target or c.source for c in context.characters if c.target or c.source})
        self.segment_panel.set_choices(speakers, self._voices)
        self.status_timer.start()
        try:
            profile = tts_resolved(self._ctx(), self.doc).profile
        except Exception:  # noqa: BLE001
            return
        language = self.state.project.target_language

        def work(_progress, _stop):
            provider = create_tts(profile)
            voices = provider.list_voices(language)  # có thể gọi mạng → chạy nền
            return voices or [(provider.voice, provider.voice)]

        def done(voices) -> None:
            self._voices = voices
            self.segment_panel.set_choices(speakers, voices)

        run_background(work, done)

    def _on_provider_changed(self, kind: str) -> None:
        self.mark_dirty("tts" if kind == "tts" else None)
        self._update_provider_badge()
        if kind == "tts":
            self._refresh_choices()

    def _update_provider_badge(self) -> None:
        settings, project = self.state.settings, self.state.project
        if not project:
            self.provider_badge.setText("")
            return
        tr = resolve(settings, "translate", project, self.doc)
        tts = resolve(settings, "tts", project, self.doc)
        def short(r, kind: str) -> str:
            if r.profile is None:
                return "chưa có"
            extra = r.value or (r.profile.model if kind == "translate" else r.profile.option("voice", ""))
            return r.profile.name + (f" · {extra.split('/')[-1]}" if extra else "")

        self.provider_badge.setText(f"Dịch: {short(tr, 'translate')}   |   TTS: {short(tts, 'tts')}")
        self.provider_badge.setToolTip(
            f"Dịch: {tr.describe('translate')}\nTTS: {tts.describe('tts')}\nBấm để đổi riêng cho video này (tab Provider)"
        )

    # ================================================================ layer / style / audio

    def _select_layer(self, layer_id: str) -> None:
        self.canvas.select_layer(layer_id)
        self.timeline.selected_layer = layer_id
        self.timeline.update()
        if self.layer_panel.current_id() != layer_id:
            self.layer_panel.select(layer_id)
        if layer_id:
            self.inspector.setCurrentIndex(2)

    def _on_layer_geometry(self, layer_id: str) -> None:
        self.layer_panel.reload_geometry(layer_id)
        self._on_layer_edited(layer_id)

    def _on_layer_edited(self, layer_id: str) -> None:
        self.mark_dirty("render")
        self.layer_panel.sync_item(layer_id)
        self.timeline.update()
        self.canvas.update()

    def _on_layers_structure(self) -> None:
        self.mark_dirty("render")
        self.timeline.rebuild_rows()
        self.canvas.invalidate_images()
        self.canvas.update()

    def _on_style(self, style: SubtitleStyle) -> None:
        if not self.doc:
            return
        self.doc.style = style
        self.mark_dirty("render")
        self.canvas.update()

    def _on_audio(self, regenerate: bool) -> None:
        self.mark_dirty("tts" if regenerate else "mix")
        self.timeline.refresh_clips()
        self.timeline.update()
        self.status_timer.start()
        self.schedule_remix()

    def _on_tracks_changed(self) -> None:
        self.mark_dirty("mix")
        self._sync_track_toggles()
        self.canvas.update()
        self.timeline.update()
        self.schedule_remix()

    def _set_track(self, name: str, on: bool) -> None:
        if not self.doc or getattr(self.doc.tracks, name) == on:
            return
        setattr(self.doc.tracks, name, on)
        self._on_tracks_changed()

    def _sync_track_toggles(self) -> None:
        if not self.doc:
            return
        for button, value in ((self.sub_toggle, self.doc.tracks.subtitles), (self.dub_toggle, self.doc.tracks.dub)):
            button.blockSignals(True)
            button.setChecked(value)
            button.blockSignals(False)

    def _on_video_settings(self, field: str) -> None:
        if not self.doc:
            return
        if field == "export_srt":
            self._export_srt()
            return
        if field == "flip":
            self.mark_dirty("render")
            self.canvas.update()
        elif field in ("mode", "srt", "language", "whisper"):
            self.mark_dirty("asr")
        elif field == "source":
            self.mark_dirty("asr")
            self.backend.load(self.doc.source_path)
        else:
            self.mark_dirty()
        if field == "name":
            self.save_now()
            self._refresh_video_combo()

    def _export_srt(self) -> None:
        if not self.doc:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Xuất phụ đề", f"{self.doc.name}.srt", "SRT (*.srt)")
        if not path:
            return
        choice = QtWidgets.QMessageBox.question(
            self, "Xuất SRT", "Xuất bản dịch? (Chọn No để xuất câu gốc)",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        use_text = choice == QtWidgets.QMessageBox.StandardButton.Yes
        items = [(s.start, s.end, s.display_text() if use_text else s.source) for s in sorted(self.segments, key=lambda s: s.start)]
        Path(path).write_text(render_srt(i for i in items if i[2]), encoding="utf-8")
        self.window().statusBar().showMessage(f"Đã lưu {path}", 4000)

    def _refresh_presets(self) -> None:
        presets = self.state.settings.subtitle_presets
        self.preset_combo.set_items([(str(i), p.name) for i, p in enumerate(presets)], keep=False)

    def _apply_preset(self) -> None:
        presets = self.state.settings.subtitle_presets
        try:
            preset = presets[int(self.preset_combo.value())]
        except (ValueError, IndexError, TypeError):
            return
        self.style_editor.set_style(preset)
        self._on_style(clone(preset))

    def _save_preset(self) -> None:
        if not self.doc:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Lưu preset", "Tên preset:", text=self.doc.style.name)
        if not ok or not name.strip():
            return
        style = clone(self.doc.style)
        style.name = name.strip()
        presets = self.state.settings.subtitle_presets
        for index, preset in enumerate(presets):
            if preset.name == style.name:
                presets[index] = style
                break
        else:
            presets.append(style)
        self.state.save_settings()

    # ================================================================ chạy job

    def run_steps(self, steps: list[str], only_missing: bool = True, force_context: bool = False) -> None:
        if not self.doc:
            return
        if "asr" in steps and self.segments and self.doc.stage("asr") == STATE_DONE:
            if steps == ["asr"]:
                if not confirm(self, "Nhận dạng lại", "Video đã có câu thoại. Chạy lại sẽ ghi đè toàn bộ câu và bản dịch. Tiếp tục?"):
                    return
            else:
                steps = [s for s in steps if s != "asr"]
        self.save_now()
        self.state.enqueue([self.doc.id], steps, only_missing=only_missing, force_context=force_context)
        self.window().statusBar().showMessage("Đã thêm vào hàng đợi", 3000)

    def export_preview(self) -> None:
        if not self.doc:
            return
        self.save_now()
        doc = clone(self.doc)
        segments = clone(self.segments)
        ctx = self._ctx()
        start = max(0.0, self.backend.position())
        mix = self.state.store.cache_dir(doc.id) / "preview" / "export_mix.wav"

        def work(progress, stop):
            ctx.progress = lambda pct, msg: progress(pct * 0.3, msg)
            ctx.stop_event = stop
            run_mix(ctx, doc, segments, target=mix)  # luôn trộn theo thiết lập hiện tại
            ctx.progress = lambda pct, msg: progress(30 + pct * 0.7, msg)
            return run_render(ctx, doc, segments, mix, start=start, length=15.0)

        def done(path) -> None:
            self.window().statusBar().showMessage(f"Đã xuất thử: {path}", 6000)
            open_path(str(path))

        self.window().statusBar().showMessage("Đang xuất thử 15 giây…")
        run_background(
            work, done, lambda msg: error(self, msg, "Xuất thử thất bại"),
            lambda pct, msg: self.window().statusBar().showMessage(f"Xuất thử: {pct:.0f}%"),
        )

    def save_as_template(self) -> None:
        project = self.state.project
        if not (self.doc and project):
            return
        project.template.style = clone(self.doc.style)
        project.template.layers = clone(self.doc.layers)
        project.template.audio = clone(self.doc.audio)
        project.template.tracks = clone(self.doc.tracks)
        project.template.flip_horizontal = self.doc.flip_horizontal
        self.state.save_project()
        self.window().statusBar().showMessage("Đã lưu làm mẫu cho video mới của project", 4000)

    def apply_to_all_videos(self) -> None:
        project, store = self.state.project, self.state.store
        if not (self.doc and project and store):
            return
        others = [v for v in project.videos if v.id != self.doc.id]
        if not others or not confirm(self, "Áp cho mọi video", f"Ghi đè style, layer, âm thanh của {len(others)} video khác?"):
            return
        self.save_now()
        skipped = 0
        for ref in others:
            if self.state.is_video_locked(ref.id):
                skipped += 1
                continue
            doc = store.load_video(ref.id)
            doc.style = clone(self.doc.style)
            doc.layers = clone(self.doc.layers)
            doc.audio = clone(self.doc.audio)
            doc.tracks = clone(self.doc.tracks)
            doc.flip_horizontal = self.doc.flip_horizontal
            doc.mark_stale_from("mix")
            store.save_video(doc)
        message = f"Đã áp cho {len(others) - skipped} video"
        if skipped:
            message += f" (bỏ qua {skipped} video đang xử lý)"
        self.window().statusBar().showMessage(message, 5000)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._layout_restored = True
        QtCore.QTimer.singleShot(0, self._apply_layout)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._layout_restored and not self._user_layout:
            QtCore.QTimer.singleShot(0, self._apply_layout)

    def _on_user_split(self, *_args) -> None:
        if not self._applying_layout:
            self._user_layout = True

    _applying_layout = False

    def _apply_layout(self) -> None:
        """Chia khung theo tỉ lệ (đã lưu hoặc mặc định) dựa trên kích thước thật của cửa sổ."""
        if self._user_layout:
            return
        self._applying_layout = True
        saved = self.state.settings.window_state
        width = self.upper_splitter.width()
        upper = saved.get("editor_upper")
        if not (isinstance(upper, list) and len(upper) == 3 and sum(upper) > 0):
            upper = [34, 43, 23]
        if width > 300:
            self.upper_splitter.setSizes([int(width * part / sum(upper)) for part in upper])
        body = saved.get("editor_body")
        if not (isinstance(body, list) and len(body) == 2 and sum(body) > 0):
            body = [68, 32]
        height = self.body.height()
        if height > 300:
            self.body.setSizes([int(height * part / sum(body)) for part in body])
        self._applying_layout = False

    def shutdown(self) -> None:
        if self._layout_restored:
            self.state.settings.window_state["editor_upper"] = self.upper_splitter.sizes()
            self.state.settings.window_state["editor_body"] = self.body.sizes()
        self.save_now()
        self.backend.close()
