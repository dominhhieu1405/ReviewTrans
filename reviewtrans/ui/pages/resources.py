from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from ...core.paths import fonts_dir, user_data_dir
from ...core.pipeline.resources import (
    KNOWN_WHISPER_MODELS,
    download_ffmpeg,
    download_libmpv,
    download_whisper_binaries,
    downloaded_whisper_models,
    ensure_whisper_model,
    fetch_whisper_models,
    tool_status,
    whisper_model_path,
)
from ...core.store import ProjectStore
from ..jobs import run_background
from ..state import AppState
from ..theme import DANGER, SUCCESS
from ..widgets.common import confirm, error, hint, human_size, open_path, push_button, title_label

TOOLS = [
    ("ffmpeg", "FFmpeg + FFprobe", "Bắt buộc: tách âm, trộn âm, xuất video.", download_ffmpeg),
    ("whisper", "whisper.cpp", "Nhận dạng giọng nói (ASR) offline.", download_whisper_binaries),
    ("libmpv", "libmpv (player)", "Player mượt, seek chính xác, xem trước blur thật. Không có sẽ dùng Qt Multimedia.", download_libmpv),
]


class ResourcesPage(QtWidgets.QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.threads = []
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(title_label("Tài nguyên", "Tải và quản lý công cụ, model nhận dạng giọng nói, dung lượng cache."))

        tools_box = QtWidgets.QGroupBox("Công cụ")
        grid = QtWidgets.QGridLayout(tools_box)
        self.tool_labels: dict[str, QtWidgets.QLabel] = {}
        self.tool_progress: dict[str, QtWidgets.QProgressBar] = {}
        for row, (key, name, desc, fn) in enumerate(TOOLS):
            title = QtWidgets.QLabel(f"<b>{name}</b><br><span style='color:#9aa0a8'>{desc}</span>")
            status = QtWidgets.QLabel()
            status.setWordWrap(True)
            status.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            progress = QtWidgets.QProgressBar()
            progress.setRange(0, 100)
            progress.setVisible(False)
            button = push_button("Tải / cập nhật", lambda _=False, k=key, f=fn: self.download_tool(k, f), "download")
            grid.addWidget(title, row, 0)
            grid.addWidget(status, row, 1)
            grid.addWidget(progress, row, 2)
            grid.addWidget(button, row, 3)
            self.tool_labels[key] = status
            self.tool_progress[key] = progress
        grid.setColumnStretch(1, 1)
        layout.addWidget(tools_box)

        models_box = QtWidgets.QGroupBox("Model Whisper")
        models_layout = QtWidgets.QVBoxLayout(models_box)
        self.models = QtWidgets.QTableWidget(0, 3)
        self.models.setHorizontalHeaderLabels(["Model", "Trạng thái", "Dung lượng"])
        self.models.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.models.verticalHeader().setVisible(False)
        self.models.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.models.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        models_layout.addWidget(self.models)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(push_button("Lấy danh sách đầy đủ", self.fetch_models, "refresh"))
        row.addWidget(push_button("Tải model đã chọn", self.download_model, "download", "Primary"))
        row.addWidget(push_button("Xoá model đã chọn", self.delete_model, "delete"))
        self.model_progress = QtWidgets.QProgressBar()
        self.model_progress.setVisible(False)
        row.addWidget(self.model_progress, 1)
        models_layout.addLayout(row)
        models_layout.addWidget(hint("Gợi ý: small/medium cho máy thường, large-v3-turbo cho độ chính xác cao. Bản -q5 nhẹ hơn."))
        layout.addWidget(models_box, 1)

        storage = QtWidgets.QGroupBox("Lưu trữ")
        storage_layout = QtWidgets.QHBoxLayout(storage)
        self.cache_label = QtWidgets.QLabel()
        storage_layout.addWidget(self.cache_label, 1)
        storage_layout.addWidget(push_button("Tính dung lượng cache", self.compute_cache, "refresh"))
        storage_layout.addWidget(push_button("Dọn cache project (giữ lồng tiếng)", self.clear_cache, "delete"))
        storage_layout.addWidget(push_button("Thư mục dữ liệu", lambda: open_path(str(user_data_dir())), "folder"))
        storage_layout.addWidget(push_button("Thư mục font", lambda: open_path(str(fonts_dir())), "folder"))
        layout.addWidget(storage)

        self._all_models = list(KNOWN_WHISPER_MODELS)
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        status = tool_status()
        for key, _name, _desc, _fn in TOOLS:
            if key == "ffmpeg":
                ok = bool(status["ffmpeg"] and status["ffprobe"])
                text = status["ffmpeg"] if ok else "Chưa có"
            else:
                ok = bool(status[key])
                text = status[key] or "Chưa có"
            label = self.tool_labels[key]
            label.setText(("✓ " if ok else "✕ ") + text)
            label.setStyleSheet(f"color: {SUCCESS if ok else DANGER};")
        downloaded = set(downloaded_whisper_models())
        models = sorted(set(self._all_models) | downloaded)
        self.models.setRowCount(len(models))
        for row, model in enumerate(models):
            path = whisper_model_path(model)
            have = model in downloaded
            for col, text in enumerate([model, "Đã tải" if have else "", human_size(path.stat().st_size) if have else ""]):
                item = QtWidgets.QTableWidgetItem(text)
                self.models.setItem(row, col, item)

    def download_tool(self, key: str, fn) -> None:
        bar = self.tool_progress[key]
        bar.setVisible(True)
        bar.setValue(0)

        def done(_result) -> None:
            bar.setVisible(False)
            self.refresh()
            note = " Khởi động lại ứng dụng để dùng player libmpv." if key == "libmpv" else ""
            self.window().statusBar().showMessage(f"Đã tải xong {key}.{note}", 8000)

        def fail(message: str) -> None:
            bar.setVisible(False)
            error(self, message, f"Tải {key} thất bại")

        run_background(lambda progress, stop: fn(progress, stop), done, fail, lambda pct, _m: bar.setValue(int(pct)))

    def fetch_models(self) -> None:
        def done(models) -> None:
            self._all_models = models
            self.refresh()

        run_background(lambda _p, _s: fetch_whisper_models(), done, lambda msg: error(self, msg))

    def _selected_model(self) -> str:
        row = self.models.currentRow()
        item = self.models.item(row, 0) if row >= 0 else None
        return item.text() if item else ""

    def download_model(self) -> None:
        model = self._selected_model()
        if not model:
            return
        self.model_progress.setVisible(True)
        self.model_progress.setValue(0)

        def done(_path) -> None:
            self.model_progress.setVisible(False)
            self.refresh()

        def fail(message: str) -> None:
            self.model_progress.setVisible(False)
            error(self, message)

        run_background(
            lambda progress, stop: ensure_whisper_model(model, progress, stop), done, fail,
            lambda pct, _m: self.model_progress.setValue(int(pct)),
        )

    def delete_model(self) -> None:
        model = self._selected_model()
        path = whisper_model_path(model)
        if model and path.exists() and confirm(self, "Xoá model", f"Xoá {path.name}?"):
            path.unlink()
            self.refresh()

    def compute_cache(self) -> None:
        store = self.state.store
        if not store:
            self.cache_label.setText("Mở một project để xem dung lượng cache.")
            return
        run_background(
            lambda _p, _s: store.cache_size(),
            lambda size: self.cache_label.setText(f"Cache project “{store.root.name}”: {human_size(size)}"),
        )

    def clear_cache(self) -> None:
        store: ProjectStore | None = self.state.store
        if not store:
            return
        if not confirm(self, "Dọn cache", "Xoá file tạm (âm thanh tách, bản trộn, file render) của project hiện tại? File lồng tiếng từng câu được giữ lại."):
            return
        store.clear_cache(keep_tts=True)
        self.compute_cache()

