from __future__ import annotations

import tempfile
import time
from pathlib import Path

from PyQt6 import QtCore, QtWidgets

from ...core.proc import parse_progress_time, probe, require_tool, run
from ..jobs import FunctionThread, run_background
from ..state import AppState
from ..widgets.common import VIDEO_FILTER, LogView, error, hint, open_path, push_button, title_label, tool_button


def _concat_line(path: Path) -> str:
    normalized = str(path).replace("\\", "/").replace("'", "'\\''")
    return f"file '{normalized}'"


def merge_videos(paths: list[str], output: str, reencode: bool, log, progress, stop) -> str:
    ffmpeg = require_tool("ffmpeg")
    total = sum(probe(p).duration for p in paths)
    work = Path(tempfile.mkdtemp(prefix="merge_"))
    concat = work / "list.txt"
    concat.write_text("\n".join(_concat_line(Path(p)) for p in paths) + "\n", encoding="utf-8")
    command = [str(ffmpeg), "-y", "-hide_banner", "-nostats", "-progress", "pipe:1", "-f", "concat", "-safe", "0", "-i", str(concat)]
    if reencode:
        command += ["-c:v", "libx264", "-crf", "20", "-preset", "medium", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    else:
        command += ["-c", "copy"]
    command.append(output)

    def on_line(line: str) -> None:
        if line.startswith("out_time=") and total > 0:
            progress(min(99.0, parse_progress_time(line.split("=", 1)[1]) * 100 / total), "")
        elif "=" not in line:
            log(line)

    log("$ " + " ".join(command))
    if run(command, log=None, stop_event=stop, line_cb=on_line) != 0:
        raise RuntimeError("Ghép video thất bại (thử bật “Mã hoá lại” nếu các video khác định dạng).")
    return output


class DropList(QtWidgets.QListWidget):
    filesDropped = QtCore.pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            self.filesDropped.emit([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class ToolsPage(QtWidgets.QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.thread: FunctionThread | None = None
        self.started = 0.0
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(title_label("Công cụ", "Ghép nhiều video thành một (ví dụ ghép các tập đã xuất)."))
        box = QtWidgets.QGroupBox("Ghép video")
        box_layout = QtWidgets.QVBoxLayout(box)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(push_button("Thêm video…", self.add, "add"))
        row.addWidget(push_button("Thêm video đã xuất của project", self.add_project_outputs, "folder"))
        row.addWidget(tool_button("up", "Lên", lambda: self.move(-1)))
        row.addWidget(tool_button("down", "Xuống", lambda: self.move(1)))
        row.addWidget(tool_button("delete", "Xoá", self.remove))
        row.addWidget(push_button("Xoá hết", lambda: self.list.clear()))
        row.addStretch()
        box_layout.addLayout(row)
        self.list = DropList()
        self.list.filesDropped.connect(self.add_paths)
        box_layout.addWidget(self.list, 1)
        box_layout.addWidget(hint("Kéo thả để thêm hoặc sắp xếp. Video cùng codec/độ phân giải có thể ghép nhanh không mã hoá lại."))
        out_row = QtWidgets.QHBoxLayout()
        self.output = QtWidgets.QLineEdit()
        self.output.setPlaceholderText("File đầu ra .mp4")
        out_row.addWidget(self.output, 1)
        out_row.addWidget(push_button("Chọn…", self.pick_output, "open"))
        self.reencode = QtWidgets.QCheckBox("Mã hoá lại (chậm, nhưng ghép được video khác định dạng)")
        out_row.addWidget(self.reencode)
        box_layout.addLayout(out_row)
        run_row = QtWidgets.QHBoxLayout()
        self.run_button = push_button("Ghép", self.toggle, "merge", "Primary")
        run_row.addWidget(self.run_button)
        self.progress = QtWidgets.QProgressBar()
        run_row.addWidget(self.progress, 1)
        self.elapsed = QtWidgets.QLabel()
        run_row.addWidget(self.elapsed)
        box_layout.addLayout(run_row)
        self.log = LogView()
        self.log.setMaximumHeight(160)
        box_layout.addWidget(self.log)
        layout.addWidget(box, 1)
        self.timer = QtCore.QTimer(self, interval=1000, timeout=self._tick)

    def paths(self) -> list[str]:
        return [self.list.item(i).data(QtCore.Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def add(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Chọn video", "", VIDEO_FILTER)
        self.add_paths(paths)

    def add_paths(self, paths: list[str]) -> None:
        for path in paths:
            item = QtWidgets.QListWidgetItem(Path(path).name)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.list.addItem(item)
        if paths and not self.output.text():
            first = Path(paths[0])
            self.output.setText(str(first.with_name(first.stem + "_ghep.mp4")))

    def add_project_outputs(self) -> None:
        store, project = self.state.store, self.state.project
        if not (store and project):
            error(self, "Chưa mở project.")
            return
        paths = []
        for ref in project.videos:
            doc = store.load_video(ref.id)
            if doc.last_output and Path(doc.last_output).exists():
                paths.append(doc.last_output)
        if not paths:
            error(self, "Project chưa có video nào đã xuất.")
            return
        self.add_paths(paths)

    def move(self, direction: int) -> None:
        row = self.list.currentRow()
        target = row + direction
        if row < 0 or not 0 <= target < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)

    def remove(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))

    def pick_output(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Lưu video ghép", self.output.text(), "MP4 (*.mp4)")
        if path:
            self.output.setText(path)

    def toggle(self) -> None:
        if self.thread is not None:
            self.thread.stop_event.set()
            return
        paths = self.paths()
        output = self.output.text().strip()
        if len(paths) < 2 or not output:
            error(self, "Cần ít nhất 2 video và đường dẫn đầu ra.")
            return
        self.log.clear()
        self.progress.setValue(0)
        self.run_button.setText("Dừng")
        self.started = time.time()
        self.timer.start()
        reencode = self.reencode.isChecked()

        def work(progress, stop):
            return merge_videos(paths, output, reencode, lambda line: progress(-1, line), progress, stop)

        def on_progress(pct: float, message: str) -> None:
            if pct < 0:
                self.log.append_line(message)
            else:
                self.progress.setValue(int(pct))

        def finish(result) -> None:
            self._done()
            self.progress.setValue(100)
            self.log.append_line(f"Xong: {result}")
            open_path(str(Path(result).parent))

        def fail(message: str) -> None:
            self._done()
            self.log.append_line(message)
            if message != "Đã dừng":
                error(self, message)

        self.thread = run_background(work, finish, fail, on_progress)

    def _done(self) -> None:
        self.thread = None
        self.timer.stop()
        self.run_button.setText("Ghép")

    def _tick(self) -> None:
        self.elapsed.setText(time.strftime("%M:%S", time.gmtime(time.time() - self.started)))
