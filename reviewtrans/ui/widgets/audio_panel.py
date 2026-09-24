from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from ...core.models import AudioSettings, VideoDoc
from .common import AUDIO_FILTER, ComboBox, DoubleSpinBox, PathEdit, SpinBox, form_layout, hint

ORIGINAL_MODES = [("duck", "Giảm khi có lồng tiếng"), ("keep", "Giữ nguyên"), ("mute", "Tắt hẳn")]
FIT_MODES = [("fit", "Tăng tốc cho vừa khung câu"), ("natural", "Giữ tốc độ tự nhiên")]


class AudioPanel(QtWidgets.QWidget):
    audioChanged = QtCore.pyqtSignal(bool)  # True nếu cần tạo lại TTS (đổi tốc độ đọc)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc: VideoDoc | None = None
        self._loading = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        original = QtWidgets.QGroupBox("Âm thanh gốc")
        form = form_layout()
        original.setLayout(form)
        self.original_mode = ComboBox(ORIGINAL_MODES)
        self.original_volume = SpinBox(0, 200, 100, " %")
        self.duck_volume = SpinBox(0, 100, 20, " %")
        form.addRow("Chế độ", self.original_mode)
        form.addRow("Âm lượng", self.original_volume)
        form.addRow("Khi có lồng tiếng", self.duck_volume)

        dub = QtWidgets.QGroupBox("Lồng tiếng")
        form = form_layout()
        dub.setLayout(form)
        self.dub_volume = SpinBox(0, 300, 100, " %")
        self.speed = DoubleSpinBox(0.5, 2.0, 1.0, 0.05, 2, "x")
        self.pitch = DoubleSpinBox(-12, 12, 0, 0.5, 1, " cung")
        self.fit_mode = ComboBox(FIT_MODES)
        self.use_gap = QtWidgets.QCheckBox("Tận dụng khoảng lặng tới câu sau")
        self.max_speed = DoubleSpinBox(1.0, 3.0, 1.8, 0.1, 2, "x")
        form.addRow("Âm lượng", self.dub_volume)
        form.addRow("Tốc độ đọc", self.speed)
        form.addRow("Cao độ", self.pitch)
        form.addRow("Khớp thời gian", self.fit_mode)
        form.addRow("", self.use_gap)
        form.addRow("Tăng tốc tối đa", self.max_speed)

        bgm = QtWidgets.QGroupBox("Nhạc nền")
        form = form_layout()
        bgm.setLayout(form)
        self.bgm_path = PathEdit("file", AUDIO_FILTER, "Chọn file nhạc (lặp lại tới hết video)")
        self.bgm_volume = SpinBox(0, 200, 25, " %")
        form.addRow("File", self.bgm_path)
        form.addRow("Âm lượng", self.bgm_volume)

        for box in (original, dub, bgm):
            layout.addWidget(box)
        layout.addWidget(hint("Đổi âm lượng/khớp thời gian chỉ cần trộn lại (nhanh). Đổi tốc độ đọc sẽ tạo lại lồng tiếng."))
        layout.addStretch()

        for widget in (self.original_volume, self.duck_volume, self.dub_volume, self.bgm_volume):
            widget.valueChanged.connect(lambda _v: self._commit(False))
        for widget in (self.pitch, self.max_speed):
            widget.valueChanged.connect(lambda _v: self._commit(False))
        self.speed.valueChanged.connect(lambda _v: self._commit(True))
        self.original_mode.currentIndexChanged.connect(lambda _i: self._commit(False))
        self.fit_mode.currentIndexChanged.connect(lambda _i: self._commit(False))
        self.use_gap.toggled.connect(lambda _c: self._commit(False))
        self.bgm_path.changed.connect(lambda _p: self._commit(False))

    def set_document(self, doc: VideoDoc | None) -> None:
        self.doc = doc
        if doc is None:
            return
        self._loading = True
        a: AudioSettings = doc.audio
        self.original_mode.set_value(a.original_mode)
        self.original_volume.setValue(a.original_volume)
        self.duck_volume.setValue(a.duck_volume)
        self.dub_volume.setValue(a.dub_volume)
        self.speed.setValue(a.speed)
        self.pitch.setValue(a.pitch)
        self.fit_mode.set_value(a.fit_mode)
        self.use_gap.setChecked(a.use_gap)
        self.max_speed.setValue(a.max_speed)
        self.bgm_path.set_value(a.bgm_path)
        self.bgm_volume.setValue(a.bgm_volume)
        self._loading = False

    def _commit(self, regenerate: bool) -> None:
        if self._loading or self.doc is None:
            return
        a = self.doc.audio
        a.original_mode = self.original_mode.value()
        a.original_volume = self.original_volume.value()
        a.duck_volume = self.duck_volume.value()
        a.dub_volume = self.dub_volume.value()
        a.speed = self.speed.value()
        a.pitch = self.pitch.value()
        a.fit_mode = self.fit_mode.value()
        a.use_gap = self.use_gap.isChecked()
        a.max_speed = self.max_speed.value()
        a.bgm_path = self.bgm_path.value()
        a.bgm_volume = self.bgm_volume.value()
        self.audioChanged.emit(regenerate)
