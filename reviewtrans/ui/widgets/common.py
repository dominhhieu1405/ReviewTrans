from __future__ import annotations

from typing import Callable

from PyQt6 import QtCore, QtGui, QtWidgets

from ..icons import icon
from ..theme import BORDER, TEXT_DIM

VIDEO_FILTER = "Video (*.mp4 *.mkv *.mov *.avi *.webm *.flv *.ts *.m4v);;Tất cả (*.*)"
IMAGE_FILTER = "Ảnh (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;Tất cả (*.*)"
AUDIO_FILTER = "Âm thanh (*.mp3 *.wav *.m4a *.aac *.flac *.ogg);;Tất cả (*.*)"


class NoWheelMixin:
    """Không đổi giá trị khi lăn chuột lướt qua (trừ khi đang focus)."""

    def wheelEvent(self, event):  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class SpinBox(NoWheelMixin, QtWidgets.QSpinBox):
    def __init__(self, minimum=0, maximum=100, value=0, suffix="", parent=None):
        super().__init__(parent)
        self.setRange(minimum, maximum)
        self.setValue(value)
        if suffix:
            self.setSuffix(suffix)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)


class DoubleSpinBox(NoWheelMixin, QtWidgets.QDoubleSpinBox):
    def __init__(self, minimum=0.0, maximum=100.0, value=0.0, step=0.1, decimals=2, suffix="", parent=None):
        super().__init__(parent)
        self.setDecimals(decimals)
        self.setRange(minimum, maximum)
        self.setSingleStep(step)
        self.setValue(value)
        if suffix:
            self.setSuffix(suffix)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)


class ComboBox(NoWheelMixin, QtWidgets.QComboBox):
    def __init__(self, items: list[tuple[str, str]] | None = None, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        if items:
            self.set_items(items)

    def set_items(self, items: list[tuple[str, str]], keep: bool = True) -> None:
        current = self.value() if keep else None
        self.blockSignals(True)
        self.clear()
        for value, label in items:
            self.addItem(label, value)
        self.blockSignals(False)
        if current is not None:
            self.set_value(current)

    def value(self):
        data = self.currentData()
        return data if data is not None else self.currentText()

    def set_value(self, value) -> None:
        index = self.findData(value)
        if index < 0 and self.isEditable():
            self.setEditText(str(value))
            return
        if index >= 0:
            self.setCurrentIndex(index)


class FontCombo(NoWheelMixin, QtWidgets.QFontComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

    def value(self) -> str:
        return self.currentFont().family()

    def set_value(self, family: str) -> None:
        self.setCurrentFont(QtGui.QFont(family))


class ColorButton(QtWidgets.QPushButton):
    colorChanged = QtCore.pyqtSignal(str)

    def __init__(self, color: str = "#FFFFFF", parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedWidth(84)
        self.clicked.connect(self._pick)
        self._refresh()

    def value(self) -> str:
        return self._color

    def set_value(self, color: str) -> None:
        self._color = color or "#000000"
        self._refresh()

    def _refresh(self) -> None:
        qc = QtGui.QColor(self._color)
        text = "#000000" if qc.lightness() > 140 else "#FFFFFF"
        self.setText(self._color.upper())
        self.setStyleSheet(
            f"QPushButton {{ background: {self._color}; color: {text}; border: 1px solid {BORDER}; }}"
        )

    def _pick(self) -> None:
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._color), self, "Chọn màu")
        if color.isValid():
            self.set_value(color.name().upper())
            self.colorChanged.emit(self._color)


class PathEdit(QtWidgets.QWidget):
    changed = QtCore.pyqtSignal(str)

    def __init__(self, mode: str = "file", file_filter: str = "", placeholder: str = "", parent=None):
        super().__init__(parent)
        self.mode = mode
        self.file_filter = file_filter
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.edit = QtWidgets.QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.editingFinished.connect(lambda: self.changed.emit(self.edit.text()))
        button = QtWidgets.QToolButton()
        button.setIcon(icon("open"))
        button.clicked.connect(self._browse)
        clear = QtWidgets.QToolButton()
        clear.setIcon(icon("delete"))
        clear.setToolTip("Xoá")
        clear.clicked.connect(lambda: self.set_value("", emit=True))
        layout.addWidget(self.edit, 1)
        layout.addWidget(button)
        layout.addWidget(clear)

    def value(self) -> str:
        return self.edit.text().strip()

    def set_value(self, value: str, emit: bool = False) -> None:
        self.edit.setText(value or "")
        if emit:
            self.changed.emit(self.value())

    def _browse(self) -> None:
        if self.mode == "dir":
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "Chọn thư mục", self.value())
        else:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Chọn file", self.value(), self.file_filter)
        if path:
            self.set_value(path, emit=True)


class LogView(QtWidgets.QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(4000)
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(9)
        self.setFont(font)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)

    def append_line(self, text: str) -> None:
        bar = self.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        self.appendPlainText(text)
        if at_bottom:
            bar.setValue(bar.maximum())


def title_label(text: str, subtitle: str = "") -> QtWidgets.QWidget:
    box = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 4)
    layout.setSpacing(2)
    title = QtWidgets.QLabel(text)
    title.setObjectName("Title")
    layout.addWidget(title)
    if subtitle:
        sub = QtWidgets.QLabel(subtitle)
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        layout.addWidget(sub)
    return box


def hint(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
    return label


def tool_button(name: str, tooltip: str, callback: Callable | None = None, checkable: bool = False, text: str = "") -> QtWidgets.QToolButton:
    button = QtWidgets.QToolButton()
    button.setIcon(icon(name))
    button.setToolTip(tooltip)
    button.setCheckable(checkable)
    if text:
        button.setText(text)
        button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    if callback:
        button.clicked.connect(callback)
    return button


def push_button(text: str, callback: Callable | None = None, icon_name: str = "", kind: str = "") -> QtWidgets.QPushButton:
    button = QtWidgets.QPushButton(text)
    if icon_name:
        button.setIcon(icon(icon_name, "#ffffff" if kind == "Primary" else "#e3e5e8"))
    if kind:
        button.setObjectName(kind)
    if callback:
        button.clicked.connect(callback)
    return button


def confirm(parent: QtWidgets.QWidget, title: str, text: str) -> bool:
    answer = QtWidgets.QMessageBox.question(
        parent, title, text,
        QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        QtWidgets.QMessageBox.StandardButton.No,
    )
    return answer == QtWidgets.QMessageBox.StandardButton.Yes


def error(parent: QtWidgets.QWidget, text: str, title: str = "Lỗi") -> None:
    QtWidgets.QMessageBox.critical(parent, title, text)


def info(parent: QtWidgets.QWidget, text: str, title: str = "Thông báo") -> None:
    QtWidgets.QMessageBox.information(parent, title, text)


def form_layout() -> QtWidgets.QFormLayout:
    form = QtWidgets.QFormLayout()
    form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setHorizontalSpacing(10)
    form.setVerticalSpacing(6)
    return form


def scroll_wrap(widget: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
    area = QtWidgets.QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    return area


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def open_path(path: str) -> None:
    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
