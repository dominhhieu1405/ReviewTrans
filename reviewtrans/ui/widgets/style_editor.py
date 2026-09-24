from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from ...core.models import Segment, SubtitleStyle, clone
from ...core.raster import paint_subtitle
from .common import ColorButton, ComboBox, DoubleSpinBox, FontCombo, SpinBox, form_layout

POSITIONS = [("bottom", "Dưới"), ("middle", "Giữa"), ("top", "Trên")]


class StyleEditor(QtWidgets.QWidget):
    """Form chỉnh SubtitleStyle; phát styleChanged mỗi khi người dùng sửa."""

    styleChanged = QtCore.pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._style = SubtitleStyle()
        self._loading = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        text_box = QtWidgets.QGroupBox("Chữ")
        form = form_layout()
        text_box.setLayout(form)
        self.font = FontCombo()
        self.size = DoubleSpinBox(8, 300, 56, 1, 0, " px")
        self.bold = QtWidgets.QCheckBox("Đậm")
        self.italic = QtWidgets.QCheckBox("Nghiêng")
        self.uppercase = QtWidgets.QCheckBox("VIẾT HOA")
        flags = QtWidgets.QHBoxLayout()
        for w in (self.bold, self.italic, self.uppercase):
            flags.addWidget(w)
        flags.addStretch()
        self.color = ColorButton()
        form.addRow("Font", self.font)
        form.addRow("Cỡ (ở 1080p)", self.size)
        form.addRow("", flags)
        form.addRow("Màu chữ", self.color)

        border_box = QtWidgets.QGroupBox("Viền và bóng")
        form = form_layout()
        border_box.setLayout(form)
        self.outline_color = ColorButton("#000000")
        self.outline_width = DoubleSpinBox(0, 20, 3, 0.5, 1, " px")
        self.shadow_depth = DoubleSpinBox(0, 20, 1.5, 0.5, 1, " px")
        self.shadow_color = ColorButton("#000000")
        self.shadow_opacity = SpinBox(0, 100, 70, " %")
        form.addRow("Màu viền", self.outline_color)
        form.addRow("Độ dày viền", self.outline_width)
        form.addRow("Độ lệch bóng", self.shadow_depth)
        form.addRow("Màu bóng", self.shadow_color)
        form.addRow("Độ đậm bóng", self.shadow_opacity)

        bg_box = QtWidgets.QGroupBox("Hộp nền")
        form = form_layout()
        bg_box.setLayout(form)
        self.bg_enabled = QtWidgets.QCheckBox("Bật hộp nền sau chữ")
        self.bg_color = ColorButton("#000000")
        self.bg_opacity = SpinBox(0, 100, 60, " %")
        self.bg_padding = DoubleSpinBox(0, 60, 10, 1, 0, " px")
        form.addRow("", self.bg_enabled)
        form.addRow("Màu nền", self.bg_color)
        form.addRow("Độ đục", self.bg_opacity)
        form.addRow("Đệm", self.bg_padding)

        pos_box = QtWidgets.QGroupBox("Vị trí")
        form = form_layout()
        pos_box.setLayout(form)
        self.position = ComboBox(POSITIONS)
        self.margin_v = DoubleSpinBox(0, 45, 7, 0.5, 1, " %")
        self.margin_h = DoubleSpinBox(0, 45, 6, 0.5, 1, " %")
        self.max_chars = SpinBox(0, 200, 0)
        self.max_chars.setSpecialValueText("Tự động")
        form.addRow("Vị trí", self.position)
        form.addRow("Lề dọc", self.margin_v)
        form.addRow("Lề ngang", self.margin_h)
        form.addRow("Ký tự / dòng", self.max_chars)

        for box in (text_box, border_box, bg_box, pos_box):
            layout.addWidget(box)
        layout.addStretch()

        for spin in (self.size, self.outline_width, self.shadow_depth, self.bg_padding, self.margin_v, self.margin_h):
            spin.valueChanged.connect(self._emit)
        for spin in (self.shadow_opacity, self.bg_opacity, self.max_chars):
            spin.valueChanged.connect(self._emit)
        for check in (self.bold, self.italic, self.uppercase, self.bg_enabled):
            check.toggled.connect(self._emit)
        for button in (self.color, self.outline_color, self.shadow_color, self.bg_color):
            button.colorChanged.connect(self._emit)
        self.font.currentFontChanged.connect(self._emit)
        self.position.currentIndexChanged.connect(self._emit)

    def style(self) -> SubtitleStyle:  # type: ignore[override]
        return clone(self._style)

    def set_style(self, style: SubtitleStyle) -> None:
        self._style = clone(style)
        self._loading = True
        s = self._style
        self.font.set_value(s.font_family)
        self.size.setValue(s.font_size)
        self.bold.setChecked(s.bold)
        self.italic.setChecked(s.italic)
        self.uppercase.setChecked(s.uppercase)
        self.color.set_value(s.text_color)
        self.outline_color.set_value(s.outline_color)
        self.outline_width.setValue(s.outline_width)
        self.shadow_depth.setValue(s.shadow_depth)
        self.shadow_color.set_value(s.shadow_color)
        self.shadow_opacity.setValue(s.shadow_opacity)
        self.bg_enabled.setChecked(s.bg_enabled)
        self.bg_color.set_value(s.bg_color)
        self.bg_opacity.setValue(s.bg_opacity)
        self.bg_padding.setValue(s.bg_padding)
        self.position.set_value(s.position)
        self.margin_v.setValue(s.margin_v)
        self.margin_h.setValue(s.margin_h)
        self.max_chars.setValue(s.max_chars_per_line)
        self._loading = False

    def _emit(self, *_args) -> None:
        if self._loading:
            return
        s = self._style
        s.font_family = self.font.value()
        s.font_size = self.size.value()
        s.bold = self.bold.isChecked()
        s.italic = self.italic.isChecked()
        s.uppercase = self.uppercase.isChecked()
        s.text_color = self.color.value()
        s.outline_color = self.outline_color.value()
        s.outline_width = self.outline_width.value()
        s.shadow_depth = self.shadow_depth.value()
        s.shadow_color = self.shadow_color.value()
        s.shadow_opacity = self.shadow_opacity.value()
        s.bg_enabled = self.bg_enabled.isChecked()
        s.bg_color = self.bg_color.value()
        s.bg_opacity = self.bg_opacity.value()
        s.bg_padding = self.bg_padding.value()
        s.position = self.position.value()
        s.margin_v = self.margin_v.value()
        s.margin_h = self.margin_h.value()
        s.max_chars_per_line = self.max_chars.value()
        self.styleChanged.emit(clone(s))


class StylePreview(QtWidgets.QWidget):
    """Khung xem trước style phụ đề trên nền giả lập."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.style_ = SubtitleStyle()
        self.text = "Đây là câu phụ đề mẫu để xem trước kiểu chữ"
        self.setMinimumHeight(180)

    def set_style(self, style: SubtitleStyle) -> None:
        self.style_ = clone(style)
        self.update()

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802
        return QtCore.QSize(480, 270)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = int(w * 9 / 16)
        if h > self.height():
            h = self.height()
            w = int(h * 16 / 9)
        rect = QtCore.QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        gradient = QtGui.QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0, QtGui.QColor("#3b6ea8"))
        gradient.setColorAt(0.5, QtGui.QColor("#d7b98e"))
        gradient.setColorAt(1, QtGui.QColor("#355e3b"))
        painter.fillRect(rect, gradient)
        paint_subtitle(painter, rect, Segment(text=self.text).display_text(), self.style_)
        painter.end()
