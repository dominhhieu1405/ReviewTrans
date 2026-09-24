from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from ...core.config import builtin_presets
from ...core.models import SubtitleStyle, clone
from ..state import AppState
from ..widgets.common import confirm, push_button, scroll_wrap, title_label
from ..widgets.style_editor import StyleEditor, StylePreview


class PresetsPage(QtWidgets.QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(title_label("Preset phụ đề", "Lưu các kiểu phụ đề hay dùng để áp nhanh trong Editor."))
        body = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        self.list = QtWidgets.QListWidget()
        self.list.currentRowChanged.connect(self._on_row)
        left.addWidget(self.list, 1)
        grid = QtWidgets.QGridLayout()
        grid.addWidget(push_button("Thêm", self.add, "add"), 0, 0)
        grid.addWidget(push_button("Nhân bản", self.duplicate, "copy"), 0, 1)
        grid.addWidget(push_button("Đổi tên", self.rename, "text"), 1, 0)
        grid.addWidget(push_button("Xoá", self.delete, "delete"), 1, 1)
        grid.addWidget(push_button("Khôi phục mặc định", self.restore, "refresh"), 2, 0, 1, 2)
        left.addLayout(grid)
        body.addLayout(left, 1)
        self.preview = StylePreview()
        self.editor = StyleEditor()
        right = QtWidgets.QVBoxLayout()
        right.addWidget(self.preview, 2)
        body.addLayout(right, 3)
        body.addWidget(scroll_wrap(self.editor), 2)
        layout.addLayout(body, 1)
        self.editor.styleChanged.connect(self._on_style)
        self.save_timer = QtCore.QTimer(self, singleShot=True, interval=500, timeout=state.save_settings)
        self.refresh()

    @property
    def presets(self) -> list[SubtitleStyle]:
        return self.state.settings.subtitle_presets

    def refresh(self, select: int | None = None) -> None:
        current = self.list.currentRow() if select is None else select
        self.list.blockSignals(True)
        self.list.clear()
        for preset in self.presets:
            self.list.addItem(preset.name)
        self.list.blockSignals(False)
        if self.presets:
            self.list.setCurrentRow(max(0, min(current, len(self.presets) - 1)))
            self._on_row(self.list.currentRow())

    def _on_row(self, row: int) -> None:
        if 0 <= row < len(self.presets):
            self.editor.set_style(self.presets[row])
            self.preview.set_style(self.presets[row])

    def _on_style(self, style: SubtitleStyle) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.presets):
            style.name = self.presets[row].name
            self.presets[row] = style
            self.preview.set_style(style)
            self.save_timer.start()

    def add(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "Preset mới", "Tên:")
        if ok and name.strip():
            self.presets.append(SubtitleStyle(name=name.strip()))
            self.state.save_settings()
            self.refresh(len(self.presets) - 1)

    def duplicate(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.presets):
            copy = clone(self.presets[row])
            copy.name += " (bản sao)"
            self.presets.append(copy)
            self.state.save_settings()
            self.refresh(len(self.presets) - 1)

    def rename(self) -> None:
        row = self.list.currentRow()
        if not 0 <= row < len(self.presets):
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Đổi tên", "Tên:", text=self.presets[row].name)
        if ok and name.strip():
            self.presets[row].name = name.strip()
            self.state.save_settings()
            self.refresh(row)

    def delete(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.presets) and confirm(self, "Xoá preset", f"Xoá “{self.presets[row].name}”?"):
            del self.presets[row]
            self.state.save_settings()
            self.refresh(row)

    def restore(self) -> None:
        names = {p.name for p in self.presets}
        for preset in builtin_presets():
            if preset.name not in names:
                self.presets.append(preset)
        self.state.save_settings()
        self.refresh()
