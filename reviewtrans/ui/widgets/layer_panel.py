from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from ...core.models import LAYER_BLUR, LAYER_IMAGE, LAYER_TEXT, LAYER_TYPES, Layer, VideoDoc, clone, new_id
from ..icons import icon
from .common import (
    IMAGE_FILTER,
    ColorButton,
    ComboBox,
    DoubleSpinBox,
    FontCombo,
    PathEdit,
    SpinBox,
    form_layout,
    hint,
    tool_button,
)

BLUR_MODES = [("blur", "Làm mờ"), ("pixelate", "Pixel hoá"), ("fill", "Tô màu đặc")]
ALIGNS = [("left", "Trái"), ("center", "Giữa"), ("right", "Phải")]


class LayerPanel(QtWidgets.QWidget):
    layersChanged = QtCore.pyqtSignal()  # thêm/xoá/đổi thứ tự
    layerChanged = QtCore.pyqtSignal(str)  # sửa thuộc tính
    layerSelected = QtCore.pyqtSignal(str)
    currentTimeRequested = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc: VideoDoc | None = None
        self.current_time = 0.0
        self._loading = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        add_row = QtWidgets.QHBoxLayout()
        for kind, name in ((LAYER_IMAGE, "image"), (LAYER_TEXT, "text"), (LAYER_BLUR, "blur")):
            button = QtWidgets.QPushButton(icon(name), f"+ {LAYER_TYPES[kind]}")
            button.clicked.connect(lambda _=False, k=kind: self.add_layer(k))
            add_row.addWidget(button)
        layout.addLayout(add_row)
        quick = QtWidgets.QPushButton("Che phụ đề gốc (dải dưới)")
        quick.setToolTip("Thêm vùng làm mờ phủ 15% dưới cùng — dùng để che chữ cứng của video gốc")
        quick.clicked.connect(self.add_bottom_blur)
        layout.addWidget(quick)

        self.list = QtWidgets.QListWidget()
        self.list.setMaximumHeight(150)
        self.list.currentRowChanged.connect(self._on_row)
        self.list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.list)

        tools = QtWidgets.QHBoxLayout()
        tools.addWidget(tool_button("up", "Đưa lên trên", lambda: self.move(1)))
        tools.addWidget(tool_button("down", "Đưa xuống dưới", lambda: self.move(-1)))
        tools.addWidget(tool_button("copy", "Nhân bản", self.duplicate))
        tools.addWidget(tool_button("delete", "Xoá layer", self.delete))
        tools.addStretch()
        layout.addLayout(tools)

        self.props = QtWidgets.QWidget()
        props_layout = QtWidgets.QVBoxLayout(self.props)
        props_layout.setContentsMargins(0, 0, 0, 0)

        common = QtWidgets.QGroupBox("Chung")
        form = form_layout()
        common.setLayout(form)
        self.name = QtWidgets.QLineEdit()
        self.start = DoubleSpinBox(0, 36000, 0, 0.5, 2, " s")
        self.end = DoubleSpinBox(0, 36000, 0, 0.5, 2, " s")
        self.to_end = QtWidgets.QCheckBox("Tới hết video")
        time_row = QtWidgets.QHBoxLayout()
        set_start = QtWidgets.QToolButton()
        set_start.setText("⇤ tại đầu phát")
        set_start.clicked.connect(lambda: self.start.setValue(self.current_time))
        set_end = QtWidgets.QToolButton()
        set_end.setText("⇥ tại đầu phát")
        set_end.clicked.connect(lambda: (self.to_end.setChecked(False), self.end.setValue(self.current_time)))
        time_row.addWidget(set_start)
        time_row.addWidget(set_end)
        self.x = DoubleSpinBox(0, 100, 0, 0.5, 1, " %")
        self.y = DoubleSpinBox(0, 100, 0, 0.5, 1, " %")
        self.w = DoubleSpinBox(0.5, 100, 10, 0.5, 1, " %")
        self.h = DoubleSpinBox(0.5, 100, 10, 0.5, 1, " %")
        pos_row = QtWidgets.QHBoxLayout()
        for label, spin in (("X", self.x), ("Y", self.y)):
            pos_row.addWidget(QtWidgets.QLabel(label))
            pos_row.addWidget(spin)
        size_row = QtWidgets.QHBoxLayout()
        for label, spin in (("R", self.w), ("C", self.h)):
            size_row.addWidget(QtWidgets.QLabel(label))
            size_row.addWidget(spin)
        self.opacity = SpinBox(0, 100, 100, " %")
        form.addRow("Tên", self.name)
        form.addRow("Bắt đầu", self.start)
        form.addRow("Kết thúc", self.end)
        form.addRow("", self.to_end)
        form.addRow("", time_row)
        form.addRow("Vị trí", pos_row)
        form.addRow("Kích thước", size_row)
        form.addRow("Độ đục", self.opacity)
        props_layout.addWidget(common)

        self.image_box = QtWidgets.QGroupBox("Ảnh")
        form = form_layout()
        self.image_box.setLayout(form)
        self.image_path = PathEdit("file", IMAGE_FILTER, "Chọn ảnh PNG/JPG…")
        self.keep_aspect = QtWidgets.QCheckBox("Giữ tỉ lệ ảnh")
        form.addRow("File", self.image_path)
        form.addRow("", self.keep_aspect)
        props_layout.addWidget(self.image_box)

        self.text_box = QtWidgets.QGroupBox("Chữ")
        form = form_layout()
        self.text_box.setLayout(form)
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setMaximumHeight(70)
        self.font = FontCombo()
        self.font_size = DoubleSpinBox(6, 400, 48, 1, 0, " px")
        self.bold = QtWidgets.QCheckBox("Đậm")
        self.italic = QtWidgets.QCheckBox("Nghiêng")
        style_row = QtWidgets.QHBoxLayout()
        style_row.addWidget(self.bold)
        style_row.addWidget(self.italic)
        style_row.addStretch()
        self.text_color = ColorButton()
        self.outline_color = ColorButton("#000000")
        self.outline_width = DoubleSpinBox(0, 20, 2, 0.5, 1, " px")
        self.bg_enabled = QtWidgets.QCheckBox("Nền")
        self.bg_color = ColorButton("#000000")
        self.bg_opacity = SpinBox(0, 100, 50, " %")
        self.align = ComboBox(ALIGNS)
        form.addRow("Nội dung", self.text)
        form.addRow("Font", self.font)
        form.addRow("Cỡ (1080p)", self.font_size)
        form.addRow("", style_row)
        form.addRow("Màu chữ", self.text_color)
        form.addRow("Màu viền", self.outline_color)
        form.addRow("Độ dày viền", self.outline_width)
        form.addRow("", self.bg_enabled)
        form.addRow("Màu nền", self.bg_color)
        form.addRow("Độ đục nền", self.bg_opacity)
        form.addRow("Căn lề", self.align)
        props_layout.addWidget(self.text_box)

        self.blur_box = QtWidgets.QGroupBox("Vùng che")
        form = form_layout()
        self.blur_box.setLayout(form)
        self.blur_mode = ComboBox(BLUR_MODES)
        self.blur_strength = SpinBox(1, 100, 20)
        self.fill_color = ColorButton("#000000")
        form.addRow("Kiểu", self.blur_mode)
        form.addRow("Cường độ", self.blur_strength)
        form.addRow("Màu tô", self.fill_color)
        props_layout.addWidget(self.blur_box)
        props_layout.addWidget(hint("Kéo layer trực tiếp trên khung phát để di chuyển/đổi kích thước; kéo khối trên timeline để đổi thời gian."))
        props_layout.addStretch()
        layout.addWidget(self.props, 1)

        for spin in (self.start, self.end, self.x, self.y, self.w, self.h, self.font_size, self.outline_width):
            spin.valueChanged.connect(self._commit)
        for spin in (self.opacity, self.bg_opacity, self.blur_strength):
            spin.valueChanged.connect(self._commit)
        for check in (self.to_end, self.keep_aspect, self.bold, self.italic, self.bg_enabled):
            check.toggled.connect(self._commit)
        for button in (self.text_color, self.outline_color, self.bg_color, self.fill_color):
            button.colorChanged.connect(self._commit)
        self.name.editingFinished.connect(self._commit)
        self.text.textChanged.connect(self._commit)
        self.font.currentFontChanged.connect(self._commit)
        self.align.currentIndexChanged.connect(self._commit)
        self.blur_mode.currentIndexChanged.connect(self._commit)
        self.image_path.changed.connect(self._commit)
        self._show_props(None)

    # ------------------------------------------------------------ dữ liệu

    def set_document(self, doc: VideoDoc | None) -> None:
        self.doc = doc
        self.refresh_list()

    def set_read_only(self, read_only: bool) -> None:
        self.setEnabled(not read_only)

    def refresh_list(self, select_id: str | None = None) -> None:
        current = select_id if select_id is not None else self.current_id()
        self.list.blockSignals(True)
        self.list.clear()
        if self.doc:
            for layer in reversed(self.doc.layers):
                item = QtWidgets.QListWidgetItem(icon({LAYER_IMAGE: "image", LAYER_TEXT: "text"}.get(layer.type, "blur")), layer.label())
                item.setData(QtCore.Qt.ItemDataRole.UserRole, layer.id)
                item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(QtCore.Qt.CheckState.Checked if layer.enabled else QtCore.Qt.CheckState.Unchecked)
                self.list.addItem(item)
        self.list.blockSignals(False)
        self.select(current or "")

    def current_id(self) -> str:
        item = self.list.currentItem()
        return item.data(QtCore.Qt.ItemDataRole.UserRole) if item else ""

    def layer(self, layer_id: str) -> Layer | None:
        if not self.doc:
            return None
        return next((layer for layer in self.doc.layers if layer.id == layer_id), None)

    def select(self, layer_id: str) -> None:
        for row in range(self.list.count()):
            if self.list.item(row).data(QtCore.Qt.ItemDataRole.UserRole) == layer_id:
                self.list.blockSignals(True)
                self.list.setCurrentRow(row)
                self.list.blockSignals(False)
                self._show_props(self.layer(layer_id))
                return
        self.list.blockSignals(True)
        self.list.setCurrentRow(-1)
        self.list.blockSignals(False)
        self._show_props(None)

    def reload_geometry(self, layer_id: str) -> None:
        """Chỉ cập nhật ô thời gian/vị trí/kích thước khi kéo trên canvas/timeline (nhẹ, không nạp lại cả form)."""
        layer = self.layer(layer_id)
        if layer is None or layer_id != self.current_id():
            return
        self._loading = True
        self.start.setValue(layer.start)
        if layer.end >= 0:
            self.end.setValue(layer.end)
        self.x.setValue(layer.x * 100)
        self.y.setValue(layer.y * 100)
        self.w.setValue(layer.w * 100)
        self.h.setValue(layer.h * 100)
        self._loading = False

    def sync_item(self, layer_id: str) -> None:
        """Đồng bộ tên + ô tick của một layer trong danh sách mà không dựng lại danh sách."""
        layer = self.layer(layer_id)
        if layer is None:
            return
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(QtCore.Qt.ItemDataRole.UserRole) == layer_id:
                self.list.blockSignals(True)
                item.setText(layer.label())
                item.setCheckState(QtCore.Qt.CheckState.Checked if layer.enabled else QtCore.Qt.CheckState.Unchecked)
                self.list.blockSignals(False)
                return

    # ------------------------------------------------------------ thao tác

    def add_layer(self, kind: str) -> None:
        if not self.doc:
            return
        layer = Layer(type=kind, start=0.0, end=-1.0)
        if kind == LAYER_IMAGE:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Chọn ảnh", "", IMAGE_FILTER)
            if not path:
                return
            layer.image_path = path
            layer.name = QtCore.QFileInfo(path).baseName()
            layer.x, layer.y, layer.w, layer.h = 0.02, 0.03, 0.14, 0.1
        elif kind == LAYER_TEXT:
            layer.text = "Nội dung chữ"
            layer.name = "Chữ"
            layer.x, layer.y, layer.w, layer.h = 0.3, 0.05, 0.4, 0.1
        else:
            layer.name = "Vùng che"
            layer.x, layer.y, layer.w, layer.h = 0.3, 0.4, 0.4, 0.2
        self.doc.layers.append(layer)
        self.refresh_list(layer.id)
        self.layersChanged.emit()
        self.layerSelected.emit(layer.id)

    def add_bottom_blur(self) -> None:
        if not self.doc:
            return
        layer = Layer(type=LAYER_BLUR, name="Che sub gốc", x=0.0, y=0.8, w=1.0, h=0.14, blur_strength=25)
        self.doc.layers.insert(0, layer)
        self.refresh_list(layer.id)
        self.layersChanged.emit()
        self.layerSelected.emit(layer.id)

    def move(self, direction: int) -> None:
        layer = self.layer(self.current_id())
        if not layer or not self.doc:
            return
        index = self.doc.layers.index(layer)
        target = index + direction
        if 0 <= target < len(self.doc.layers):
            self.doc.layers[index], self.doc.layers[target] = self.doc.layers[target], self.doc.layers[index]
            self.refresh_list(layer.id)
            self.layersChanged.emit()

    def duplicate(self) -> None:
        layer = self.layer(self.current_id())
        if not layer or not self.doc:
            return
        copy = clone(layer)
        copy.id = new_id()
        copy.name = (layer.name or layer.label()) + " (bản sao)"
        copy.x = min(0.9, copy.x + 0.02)
        copy.y = min(0.9, copy.y + 0.02)
        self.doc.layers.insert(self.doc.layers.index(layer) + 1, copy)
        self.refresh_list(copy.id)
        self.layersChanged.emit()

    def delete(self) -> None:
        layer = self.layer(self.current_id())
        if not layer or not self.doc:
            return
        self.doc.layers.remove(layer)
        self.refresh_list("")
        self.layersChanged.emit()
        self.layerSelected.emit("")

    # ------------------------------------------------------------ form

    def _on_row(self, _row: int) -> None:
        layer_id = self.current_id()
        self._show_props(self.layer(layer_id))
        self.layerSelected.emit(layer_id)

    def _on_item_changed(self, item: QtWidgets.QListWidgetItem) -> None:
        layer = self.layer(item.data(QtCore.Qt.ItemDataRole.UserRole))
        if layer:
            layer.enabled = item.checkState() == QtCore.Qt.CheckState.Checked
            self.layerChanged.emit(layer.id)

    def _show_props(self, layer: Layer | None) -> None:
        self.props.setVisible(layer is not None)
        if layer is None:
            return
        self._loading = True
        self.name.setText(layer.name)
        self.start.setValue(layer.start)
        self.to_end.setChecked(layer.end < 0)
        self.end.setEnabled(layer.end >= 0)
        self.end.setValue(layer.end if layer.end >= 0 else (self.doc.duration if self.doc else 0))
        self.x.setValue(layer.x * 100)
        self.y.setValue(layer.y * 100)
        self.w.setValue(layer.w * 100)
        self.h.setValue(layer.h * 100)
        self.h.setEnabled(not (layer.type == LAYER_IMAGE and layer.keep_aspect))
        self.opacity.setValue(int(round(layer.opacity * 100)))
        self.image_box.setVisible(layer.type == LAYER_IMAGE)
        self.text_box.setVisible(layer.type == LAYER_TEXT)
        self.blur_box.setVisible(layer.type == LAYER_BLUR)
        self.image_path.set_value(layer.image_path)
        self.keep_aspect.setChecked(layer.keep_aspect)
        if self.text.toPlainText() != layer.text:
            self.text.setPlainText(layer.text)
        self.font.set_value(layer.font_family)
        self.font_size.setValue(layer.font_size)
        self.bold.setChecked(layer.bold)
        self.italic.setChecked(layer.italic)
        self.text_color.set_value(layer.text_color)
        self.outline_color.set_value(layer.outline_color)
        self.outline_width.setValue(layer.outline_width)
        self.bg_enabled.setChecked(layer.bg_enabled)
        self.bg_color.set_value(layer.bg_color)
        self.bg_opacity.setValue(layer.bg_opacity)
        self.align.set_value(layer.align)
        self.blur_mode.set_value(layer.blur_mode)
        self.blur_strength.setValue(layer.blur_strength)
        self.fill_color.set_value(layer.fill_color)
        self._loading = False

    def _commit(self, *_args) -> None:
        if self._loading:
            return
        layer = self.layer(self.current_id())
        if layer is None:
            return
        layer.name = self.name.text().strip()
        layer.start = self.start.value()
        self.end.setEnabled(not self.to_end.isChecked())
        layer.end = -1.0 if self.to_end.isChecked() else max(layer.start + 0.1, self.end.value())
        layer.x = self.x.value() / 100
        layer.y = self.y.value() / 100
        layer.w = self.w.value() / 100
        layer.h = self.h.value() / 100
        layer.opacity = self.opacity.value() / 100
        layer.image_path = self.image_path.value()
        layer.keep_aspect = self.keep_aspect.isChecked()
        self.h.setEnabled(not (layer.type == LAYER_IMAGE and layer.keep_aspect))
        layer.text = self.text.toPlainText()
        layer.font_family = self.font.value()
        layer.font_size = self.font_size.value()
        layer.bold = self.bold.isChecked()
        layer.italic = self.italic.isChecked()
        layer.text_color = self.text_color.value()
        layer.outline_color = self.outline_color.value()
        layer.outline_width = self.outline_width.value()
        layer.bg_enabled = self.bg_enabled.isChecked()
        layer.bg_color = self.bg_color.value()
        layer.bg_opacity = self.bg_opacity.value()
        layer.align = self.align.value()
        layer.blur_mode = self.blur_mode.value()
        layer.blur_strength = self.blur_strength.value()
        layer.fill_color = self.fill_color.value()
        item = self.list.currentItem()
        if item:
            self.list.blockSignals(True)
            item.setText(layer.label())
            self.list.blockSignals(False)
        self.layerChanged.emit(layer.id)
