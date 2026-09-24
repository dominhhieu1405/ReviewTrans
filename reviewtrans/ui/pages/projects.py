from __future__ import annotations

import time
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets

from ...core.config import LLM_KINDS
from ...core.langs import SOURCE_LANGUAGES, TARGET_LANGUAGES, display_name
from ...core.models import STAGE_LABELS, STAGES, Character, GlossaryEntry, ProjectContext
from ...core.pipeline.resources import KNOWN_WHISPER_MODELS, downloaded_whisper_models
from ...core.pipeline.runner import STEP_LABELS
from ...core.proc import probe
from ...core.srt import format_clock
from ...core.store import ProjectStore
from ..icons import icon
from ..jobs import run_background
from ..state import AppState
from ..theme import STATE_COLORS, TEXT_DIM
from ..widgets.provider_field import ProviderField
from ..widgets.common import (
    VIDEO_FILTER,
    ComboBox,
    PathEdit,
    confirm,
    error,
    form_layout,
    hint,
    open_path,
    push_button,
    scroll_wrap,
    title_label,
    tool_button,
)

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".m4v"}
STATE_SYMBOL = {"done": "●", "stale": "◐", "error": "✕", "running": "▶", "none": "○"}


class ProjectList(QtWidgets.QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(title_label("Project", "Mỗi project chứa nhiều video dùng chung ngữ cảnh."))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(push_button("Tạo mới", self.create, "add", "Primary"))
        row.addWidget(push_button("Mở…", self.open_dialog, "open"))
        layout.addLayout(row)
        self.list = QtWidgets.QListWidget()
        self.list.itemDoubleClicked.connect(self._open_item)
        self.list.itemClicked.connect(self._open_item)
        self.list.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        layout.addWidget(self.list, 1)
        refresh = push_button("Làm mới danh sách", self.refresh, "refresh")
        layout.addWidget(refresh)
        state.projectOpened.connect(self.refresh)
        state.projectChanged.connect(self._mark_current)
        self.refresh()

    def refresh(self) -> None:
        settings = self.state.settings
        paths: list[Path] = []
        for raw in settings.recent_projects:
            path = Path(raw)
            if ProjectStore.is_project_dir(path) and path not in paths:
                paths.append(path)
        root = settings.projects_root_path()
        if root.exists():
            for child in sorted(root.iterdir()):
                if child.is_dir() and ProjectStore.is_project_dir(child) and child not in paths:
                    paths.append(child)
        self.list.clear()
        for path in paths:
            try:
                project = ProjectStore(path).load_project()
            except Exception:  # noqa: BLE001
                continue
            item = QtWidgets.QListWidgetItem(icon("folder"), f"{project.name}\n{len(project.videos)} video · {time.strftime('%d/%m/%Y', time.localtime(project.updated_at))}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.list.addItem(item)
        self._mark_current()

    def _mark_current(self) -> None:
        current = str(self.state.store.root) if self.state.store else ""
        for row in range(self.list.count()):
            item = self.list.item(row)
            font = item.font()
            font.setBold(Path(item.data(QtCore.Qt.ItemDataRole.UserRole)) == Path(current) if current else False)
            item.setFont(font)
            if self.state.store and Path(item.data(QtCore.Qt.ItemDataRole.UserRole)) == self.state.store.root and self.state.project:
                item.setText(f"{self.state.project.name}\n{len(self.state.project.videos)} video")

    def create(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "Project mới", "Tên project (ví dụ tên phim):")
        if not ok or not name.strip():
            return
        try:
            self.state.create_project(self.state.settings.projects_root_path(), name.strip())
        except OSError as exc:
            error(self, str(exc))

    def open_dialog(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Chọn thư mục project", str(self.state.settings.projects_root_path()))
        if folder:
            self._open(folder)

    def _open_item(self, item: QtWidgets.QListWidgetItem) -> None:
        path = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if self.state.store and Path(path) == self.state.store.root:
            return
        self._open(path)

    def _open(self, path: str) -> None:
        try:
            self.state.open_project(path)
        except (OSError, FileNotFoundError) as exc:
            error(self, str(exc))

    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if not item:
            return
        path = item.data(QtCore.Qt.ItemDataRole.UserRole)
        menu = QtWidgets.QMenu(self)
        menu.addAction("Mở thư mục", lambda: open_path(path))
        menu.addAction("Bỏ khỏi danh sách gần đây", lambda: self._forget(path))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _forget(self, path: str) -> None:
        settings = self.state.settings
        settings.recent_projects = [p for p in settings.recent_projects if Path(p) != Path(path)]
        self.state.save_settings()
        self.refresh()


class VideosTab(QtWidgets.QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setAcceptDrops(True)
        layout = QtWidgets.QVBoxLayout(self)
        tools = QtWidgets.QHBoxLayout()
        tools.addWidget(push_button("Thêm video…", self.add_videos, "add", "Primary"))
        tools.addWidget(tool_button("up", "Lên (tập trước)", lambda: self.move(-1)))
        tools.addWidget(tool_button("down", "Xuống (tập sau)", lambda: self.move(1)))
        tools.addWidget(tool_button("delete", "Xoá khỏi project", self.remove))
        tools.addWidget(push_button("Mở trong Editor", self.open_editor, "editor"))
        tools.addStretch()
        layout.addLayout(tools)
        self.table = QtWidgets.QTableWidget(0, 4 + len(STAGES))
        self.table.setHorizontalHeaderLabels(["#", "Tên video", "Thời lượng", "Câu"] + [STAGE_LABELS[s] for s in STAGES])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 40)
        self.table.doubleClicked.connect(lambda _i: self.open_editor())
        layout.addWidget(self.table, 1)
        layout.addWidget(hint("Kéo thả file video vào đây để thêm. Thứ tự trong danh sách = thứ tự tập, ngữ cảnh được cập nhật lần lượt theo thứ tự này."))

        run_box = QtWidgets.QGroupBox("Chạy hàng loạt")
        run_layout = QtWidgets.QHBoxLayout(run_box)
        self.step_checks: dict[str, QtWidgets.QCheckBox] = {}
        for step in ["asr", "translate", "context", "tts", "mix", "render"]:
            check = QtWidgets.QCheckBox(STEP_LABELS[step])
            check.setChecked(True)
            self.step_checks[step] = check
            run_layout.addWidget(check)
        run_layout.addStretch()
        self.only_missing = QtWidgets.QCheckBox("Chỉ phần còn thiếu")
        self.only_missing.setChecked(True)
        self.only_missing.setToolTip("Bỏ chọn để dịch/lồng tiếng lại toàn bộ (trừ câu đã khoá)")
        run_layout.addWidget(self.only_missing)
        run_layout.addWidget(push_button("Chạy video đã chọn", lambda: self.run(selected=True), "run"))
        run_layout.addWidget(push_button("Chạy tất cả video", lambda: self.run(selected=False), "run", "Primary"))
        layout.addWidget(run_box)

        state.projectOpened.connect(self.refresh)
        state.projectChanged.connect(self.refresh)
        state.videoChanged.connect(lambda _vid: self._refresh_timer.start())
        state.jobs.videoLocksChanged.connect(lambda: self._refresh_timer.start())
        self._refresh_timer = QtCore.QTimer(self, singleShot=True, interval=700, timeout=self.refresh)

    def refresh(self) -> None:
        project, store = self.state.project, self.state.store
        selected = set(self.selected_ids())
        self.table.setRowCount(0)
        if not project or not store:
            return
        self.table.setRowCount(len(project.videos))
        for row, ref in enumerate(project.videos):
            try:
                doc = store.load_video(ref.id)
                count = len(store.load_segments(ref.id))
            except (OSError, FileNotFoundError):
                continue
            cells = [str(row + 1), doc.name, format_clock(doc.duration), str(count)]
            for col, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, ref.id)
                if col == 1:
                    item.setToolTip(doc.source_path)
                self.table.setItem(row, col, item)
            locked = self.state.is_video_locked(ref.id)
            for offset, stage in enumerate(STAGES):
                state = doc.stage(stage)
                if locked and state not in ("done",):
                    state = "running" if state == "running" else state
                item = QtWidgets.QTableWidgetItem(f"{STATE_SYMBOL.get(state, '○')} {self._state_text(state)}")
                item.setForeground(QtGui.QColor(STATE_COLORS.get(state, TEXT_DIM)))
                if doc.stage_errors.get(stage):
                    item.setToolTip(doc.stage_errors[stage])
                self.table.setItem(row, 4 + offset, item)
            if ref.id in selected:
                self.table.selectRow(row)

    @staticmethod
    def _state_text(state: str) -> str:
        return {"done": "xong", "stale": "cũ", "error": "lỗi", "running": "đang chạy", "none": "—"}.get(state, state)

    def selected_ids(self) -> list[str]:
        ids = []
        for index in self.table.selectionModel().selectedRows() if self.table.selectionModel() else []:
            item = self.table.item(index.row(), 0)
            if item:
                ids.append(item.data(QtCore.Qt.ItemDataRole.UserRole))
        return ids

    def add_videos(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Chọn video", self.state.settings.last_project, VIDEO_FILTER)
        if paths:
            self.add_paths(paths)

    def add_paths(self, paths: list[str]) -> None:
        project, store = self.state.project, self.state.store
        if not project or not store:
            error(self, "Hãy tạo hoặc mở một project trước.")
            return
        paths = [p for p in paths if Path(p).suffix.lower() in VIDEO_EXTS or Path(p).is_file()]
        if not paths:
            return
        self.window().statusBar().showMessage(f"Đang đọc thông tin {len(paths)} video…")

        def work(_progress, _stop):
            infos = []
            for path in sorted(paths, key=_natural_key):
                try:
                    infos.append((path, probe(path)))
                except Exception:  # noqa: BLE001
                    infos.append((path, None))
            return infos

        def done(infos) -> None:
            for path, info in infos:
                store.add_video(project, path, info)
            self.state.save_project()
            self.window().statusBar().showMessage(f"Đã thêm {len(infos)} video", 4000)
            if len(project.videos) == len(infos):
                self.state.openVideoRequested.emit(project.videos[0].id)

        run_background(work, done, lambda msg: error(self, msg))

    def move(self, direction: int) -> None:
        project = self.state.project
        ids = self.selected_ids()
        if not project or len(ids) != 1:
            return
        index = next(i for i, v in enumerate(project.videos) if v.id == ids[0])
        target = index + direction
        if 0 <= target < len(project.videos):
            project.videos[index], project.videos[target] = project.videos[target], project.videos[index]
            self.state.save_project()
            self.table.selectRow(target)

    def remove(self) -> None:
        project, store = self.state.project, self.state.store
        ids = self.selected_ids()
        if not project or not store or not ids:
            return
        if any(self.state.is_video_locked(v) for v in ids):
            error(self, "Có video đang chạy trong hàng đợi.")
            return
        if not confirm(self, "Xoá video", f"Xoá {len(ids)} video khỏi project? (File video gốc không bị xoá, dữ liệu dịch/lồng tiếng sẽ mất.)"):
            return
        for video_id in ids:
            store.remove_video(project, video_id)
        self.state.save_project()

    def open_editor(self) -> None:
        ids = self.selected_ids()
        if ids:
            self.state.openVideoRequested.emit(ids[0])
            self.state.navigateRequested.emit("editor")

    def run(self, selected: bool) -> None:
        project = self.state.project
        if not project:
            return
        ids = self.selected_ids() if selected else [v.id for v in project.videos]
        steps = [s for s, check in self.step_checks.items() if check.isChecked()]
        if not ids or not steps:
            error(self, "Chọn ít nhất một video và một bước.")
            return
        self.state.enqueue(ids, steps, only_missing=self.only_missing.isChecked())
        self.state.navigateRequested.emit("queue")

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:  # noqa: N802
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        self.add_paths(paths)


def _natural_key(path: str):
    import re

    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", Path(path).name)]


class ContextTab(QtWidgets.QWidget):
    GLOSSARY_COLS = ["Gốc", "Dịch", "Ghi chú", "Khoá"]
    CHAR_COLS = ["Tên gốc", "Tên dịch", "Giới tính", "Vai trò / quan hệ", "Cách xưng hô", "Giọng TTS", "Khoá"]

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self._loading = False
        self.dirty = False
        self.save_timer = QtCore.QTimer(self, singleShot=True, interval=700, timeout=self.save)
        inner = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(inner)
        layout.addWidget(hint(
            "Ngữ cảnh dùng chung cho mọi video trong project và được đưa vào prompt khi dịch bằng LLM. "
            "Sau mỗi video, LLM tự bổ sung nhân vật/thuật ngữ mới. Tích “Khoá” để giữ nguyên mục bạn đã chốt. "
            "Với Google/Microsoft, glossary được thay trực tiếp vào câu gốc trước khi dịch."
        ))
        grid = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("Thể loại / giọng văn"))
        self.style_notes = QtWidgets.QPlainTextEdit()
        self.style_notes.setPlaceholderText("Ví dụ: Tiên hiệp, giọng kể review hài hước, xưng “tôi”…")
        self.style_notes.setMaximumHeight(80)
        left.addWidget(self.style_notes)
        left.addWidget(QtWidgets.QLabel("Tóm tắt cốt truyện đến hiện tại"))
        self.summary = QtWidgets.QPlainTextEdit()
        left.addWidget(self.summary)
        grid.addLayout(left, 1)
        right = QtWidgets.QVBoxLayout()
        right.addWidget(QtWidgets.QLabel("Nhật ký cập nhật tự động"))
        self.changelog = QtWidgets.QListWidget()
        right.addWidget(self.changelog)
        grid.addLayout(right, 1)
        layout.addLayout(grid)

        layout.addWidget(QtWidgets.QLabel("Nhân vật"))
        self.characters = self._table(self.CHAR_COLS)
        layout.addWidget(self.characters)
        layout.addLayout(self._table_tools(self.characters, self._add_character))
        layout.addWidget(QtWidgets.QLabel("Thuật ngữ (glossary)"))
        self.glossary = self._table(self.GLOSSARY_COLS)
        layout.addWidget(self.glossary)
        layout.addLayout(self._table_tools(self.glossary, self._add_glossary))

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll_wrap(inner))

        self.style_notes.textChanged.connect(self._changed)
        self.summary.textChanged.connect(self._changed)
        self.characters.itemChanged.connect(self._changed)
        self.glossary.itemChanged.connect(self._changed)
        state.projectOpened.connect(self.load)
        state.contextChanged.connect(self._external_change)
        state.flushRequested.connect(self.save)

    def _table(self, columns: list[str]) -> QtWidgets.QTableWidget:
        table = QtWidgets.QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(len(columns) - 1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table.setMinimumHeight(180)
        table.setSortingEnabled(False)
        return table

    def _table_tools(self, table: QtWidgets.QTableWidget, add_fn) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        row.addWidget(push_button("Thêm dòng", add_fn, "add"))
        row.addWidget(push_button("Xoá dòng đã chọn", lambda: self._remove_rows(table), "delete"))
        row.addStretch()
        return row

    def _set_row(self, table: QtWidgets.QTableWidget, row: int, values: list[str], locked: bool, auto: bool) -> None:
        for col, value in enumerate(values):
            item = QtWidgets.QTableWidgetItem(value)
            if auto and col == 0:
                item.setToolTip("Do LLM tự thêm")
                item.setForeground(QtGui.QColor("#9ec3ff"))
            table.setItem(row, col, item)
        check = QtWidgets.QTableWidgetItem()
        check.setFlags(QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled)
        check.setCheckState(QtCore.Qt.CheckState.Checked if locked else QtCore.Qt.CheckState.Unchecked)
        check.setData(QtCore.Qt.ItemDataRole.UserRole, auto)
        table.setItem(row, len(values), check)

    def load(self) -> None:
        context = self.state.load_context()
        self._loading = True
        self.style_notes.setPlainText(context.style_notes)
        self.summary.setPlainText(context.summary)
        self.characters.setRowCount(len(context.characters))
        for row, c in enumerate(context.characters):
            self._set_row(self.characters, row, [c.source, c.target, c.gender, c.role, c.addressing, c.voice], c.locked, c.auto)
        self.glossary.setRowCount(len(context.glossary))
        for row, g in enumerate(context.glossary):
            self._set_row(self.glossary, row, [g.source, g.target, g.note], g.locked, g.auto)
        self.changelog.clear()
        for log in reversed(context.changelog):
            self.changelog.addItem(f"{time.strftime('%d/%m %H:%M', time.localtime(log.time))} · {log.video}: {log.message}")
        self._loading = False
        self.dirty = False

    def _external_change(self) -> None:
        if not self.dirty:
            self.load()

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        self.dirty = True
        self.save_timer.start()

    def _cell(self, table, row, col) -> str:
        item = table.item(row, col)
        return item.text().strip() if item else ""

    def _checked(self, table, row, col) -> tuple[bool, bool]:
        item = table.item(row, col)
        if not item:
            return False, False
        return item.checkState() == QtCore.Qt.CheckState.Checked, bool(item.data(QtCore.Qt.ItemDataRole.UserRole))

    def collect(self) -> ProjectContext:
        context = self.state.load_context()
        context.style_notes = self.style_notes.toPlainText().strip()
        context.summary = self.summary.toPlainText().strip()
        characters = []
        for row in range(self.characters.rowCount()):
            values = [self._cell(self.characters, row, c) for c in range(6)]
            if not any(values):
                continue
            locked, auto = self._checked(self.characters, row, 6)
            characters.append(Character(*values[:5], voice=values[5], locked=locked, auto=auto))
        context.characters = characters
        glossary = []
        for row in range(self.glossary.rowCount()):
            values = [self._cell(self.glossary, row, c) for c in range(3)]
            if not values[0]:
                continue
            locked, auto = self._checked(self.glossary, row, 3)
            glossary.append(GlossaryEntry(values[0], values[1], values[2], locked=locked, auto=auto))
        context.glossary = glossary
        return context

    def save(self) -> None:
        self.save_timer.stop()
        if not self.dirty or not self.state.store:
            return
        self.dirty = False
        self.state.store.save_context(self.collect())

    def _add_character(self) -> None:
        row = self.characters.rowCount()
        self.characters.insertRow(row)
        self._set_row(self.characters, row, [""] * 6, True, False)
        self.characters.editItem(self.characters.item(row, 0))

    def _add_glossary(self) -> None:
        row = self.glossary.rowCount()
        self.glossary.insertRow(row)
        self._set_row(self.glossary, row, [""] * 3, True, False)
        self.glossary.editItem(self.glossary.item(row, 0))

    def _remove_rows(self, table: QtWidgets.QTableWidget) -> None:
        rows = sorted({index.row() for index in table.selectedIndexes()}, reverse=True)
        for row in rows:
            table.removeRow(row)
        if rows:
            self._changed()


class ProjectSettingsTab(QtWidgets.QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self._loading = False
        inner = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(inner)

        general = QtWidgets.QGroupBox("Chung")
        form = form_layout()
        general.setLayout(form)
        self.name = QtWidgets.QLineEdit()
        self.source_lang = ComboBox([(c, display_name(c)) for c in SOURCE_LANGUAGES])
        self.target_lang = ComboBox([(c, display_name(c)) for c in TARGET_LANGUAGES])
        self.output_dir = PathEdit("dir", placeholder="Mặc định: <project>/output")
        form.addRow("Tên project", self.name)
        form.addRow("Ngôn ngữ gốc", self.source_lang)
        form.addRow("Dịch sang", self.target_lang)
        form.addRow("Thư mục xuất", self.output_dir)
        layout.addWidget(general)

        translate = QtWidgets.QGroupBox("Dịch")
        form = form_layout()
        translate.setLayout(form)
        self.translate_field = ProviderField(state, "translate", "project")
        self.context_profile = ComboBox()
        self.auto_context = QtWidgets.QCheckBox("Tự cập nhật ngữ cảnh sau mỗi video (cần LLM)")
        self.instructions = QtWidgets.QPlainTextEdit()
        self.instructions.setPlaceholderText("Chỉ dẫn thêm cho LLM, ví dụ: giữ nguyên tên chiêu thức Hán Việt; văn phong review hài hước…")
        self.instructions.setMaximumHeight(100)
        form.addRow("Provider · model", self.translate_field)
        form.addRow("LLM cập nhật ngữ cảnh", self.context_profile)
        form.addRow("", self.auto_context)
        form.addRow("Chỉ dẫn thêm", self.instructions)
        layout.addWidget(translate)

        tts = QtWidgets.QGroupBox("Lồng tiếng")
        form = form_layout()
        tts.setLayout(form)
        self.tts_field = ProviderField(state, "tts", "project")
        form.addRow("Provider · giọng", self.tts_field)
        layout.addWidget(tts)

        asr = QtWidgets.QGroupBox("Nhận dạng giọng nói")
        form = form_layout()
        asr.setLayout(form)
        self.whisper = ComboBox()
        self.whisper.setEditable(True)
        form.addRow("Model Whisper", self.whisper)
        layout.addWidget(asr)
        layout.addWidget(hint(
            "Thứ tự ưu tiên provider: video (tab Provider trong Editor) > project (ở đây) > mặc định (trang Providers). "
            "Chọn “Mặc định (…)” để project đi theo provider mặc định."
        ))
        layout.addWidget(hint("Mẫu style/layer/âm thanh cho video mới: lưu từ Editor → “Chạy tất cả ▾” → “Lưu … làm mẫu project”."))
        layout.addStretch()
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll_wrap(inner))

        self.name.editingFinished.connect(self.commit)
        for combo in (self.source_lang, self.target_lang, self.context_profile):
            combo.currentIndexChanged.connect(self.commit)
        self.commit_timer = QtCore.QTimer(self, singleShot=True, interval=600, timeout=self.commit)
        self.whisper.currentTextChanged.connect(self._commit_later)
        self.auto_context.toggled.connect(self.commit)
        self.output_dir.changed.connect(self.commit)
        self.instructions.textChanged.connect(self._commit_later)
        state.flushRequested.connect(self._flush)
        state.projectOpened.connect(self.load)
        state.settingsChanged.connect(self.load)

    def _commit_later(self, *_args) -> None:
        if not self._loading:
            self.commit_timer.start()

    def _flush(self) -> None:
        if self.commit_timer.isActive():
            self.commit_timer.stop()
            self.commit()

    def load(self) -> None:
        project = self.state.project
        if not project:
            return
        settings = self.state.settings
        self._loading = True
        self.name.setText(project.name)
        self.source_lang.set_value(project.source_language)
        self.target_lang.set_value(project.target_language)
        self.output_dir.set_value(project.output_dir)
        self.context_profile.set_items(
            [("", "Dùng provider dịch (nếu là LLM)")]
            + [(p.id, p.name) for p in settings.translate_profiles if p.kind in LLM_KINDS],
            keep=False,
        )
        self.context_profile.set_value(project.context_profile)
        self.auto_context.setChecked(project.context_auto_update)
        if self.instructions.toPlainText() != project.instructions:
            self.instructions.setPlainText(project.instructions)
        models = sorted(set(downloaded_whisper_models() + KNOWN_WHISPER_MODELS))
        self.whisper.set_items([(m, m) for m in models], keep=False)
        self.whisper.setEditText(project.whisper_model)
        self._loading = False

    def commit(self, *_args) -> None:
        project = self.state.project
        if self._loading or not project:
            return
        self.commit_timer.stop()
        project.name = self.name.text().strip() or project.name
        project.source_language = self.source_lang.value()
        project.target_language = self.target_lang.value()
        project.output_dir = self.output_dir.value()
        project.context_profile = self.context_profile.value() or ""
        project.context_auto_update = self.auto_context.isChecked()
        project.instructions = self.instructions.toPlainText().strip()
        project.whisper_model = self.whisper.currentText().strip() or "small"
        self.state.save_project()

class ProjectsPage(QtWidgets.QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        splitter = QtWidgets.QSplitter()
        self.list = ProjectList(state)
        splitter.addWidget(self.list)
        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        header = QtWidgets.QHBoxLayout()
        self.header = title_label("Chưa mở project")
        header.addWidget(self.header, 1)
        self.open_folder = push_button("Mở thư mục", lambda: state.store and open_path(str(state.store.root)), "folder")
        header.addWidget(self.open_folder)
        right_layout.addLayout(header)
        self.tabs = QtWidgets.QTabWidget()
        self.videos = VideosTab(state)
        self.context = ContextTab(state)
        self.settings = ProjectSettingsTab(state)
        self.tabs.addTab(self.videos, "Video")
        self.tabs.addTab(self.context, "Ngữ cảnh chung")
        self.tabs.addTab(self.settings, "Cấu hình project")
        right_layout.addWidget(self.tabs, 1)
        self.placeholder = QtWidgets.QLabel("Tạo project mới hoặc chọn một project bên trái.")
        self.placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet(f"color: {TEXT_DIM}; font-size: 15px;")
        right_layout.addWidget(self.placeholder, 1)
        splitter.addWidget(right)
        splitter.setSizes([280, 900])
        layout.addWidget(splitter)
        state.projectOpened.connect(self._update)
        state.projectChanged.connect(self._update)
        self._update()

    def _update(self) -> None:
        project = self.state.project
        has = project is not None
        self.tabs.setVisible(has)
        self.open_folder.setVisible(has)
        self.placeholder.setVisible(not has)
        title = self.header.findChild(QtWidgets.QLabel, "Title")
        if title:
            title.setText(project.name if project else "Chưa mở project")
