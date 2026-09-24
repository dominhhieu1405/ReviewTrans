from __future__ import annotations

from pathlib import Path

from PyQt6 import QtCore, QtWidgets

from .. import APP_NAME, APP_VERSION
from ..core.paths import find_tool
from ..core.store import ProjectStore
from .icons import icon
from .jobs import RUNNING
from .pages.editor import EditorPage
from .pages.presets import PresetsPage
from .pages.projects import ProjectsPage
from .pages.providers import ProvidersPage
from .pages.queue import QueuePage
from .pages.resources import ResourcesPage
from .pages.settings import SettingsPage
from .pages.tools import ToolsPage
from .state import AppState
from .theme import TEXT_DIM
from .widgets.common import confirm

NAV = [
    ("projects", "Project", "projects"),
    ("editor", "Editor", "editor"),
    ("queue", "Hàng đợi", "queue"),
    ("providers", "Providers", "providers"),
    ("presets", "Preset", "presets"),
    ("resources", "Tài nguyên", "resources"),
    ("tools", "Công cụ", "tools"),
    ("settings", "Cài đặt", "settings"),
]


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(1560, 940)
        self.setMinimumSize(1100, 680)

        central = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        rail = QtWidgets.QFrame()
        rail.setObjectName("NavRail")
        rail.setFixedWidth(96)
        rail_layout = QtWidgets.QVBoxLayout(rail)
        rail_layout.setContentsMargins(6, 10, 6, 10)
        rail_layout.setSpacing(4)
        self.stack = QtWidgets.QStackedWidget()
        self.pages: dict[str, QtWidgets.QWidget] = {
            "projects": ProjectsPage(state),
            "editor": EditorPage(state),
            "queue": QueuePage(state),
            "providers": ProvidersPage(state),
            "presets": PresetsPage(state),
            "resources": ResourcesPage(state),
            "tools": ToolsPage(state),
            "settings": SettingsPage(state),
        }
        self.buttons: dict[str, QtWidgets.QToolButton] = {}
        group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        for key, label, icon_name in NAV:
            button = QtWidgets.QToolButton()
            button.setObjectName("NavButton")
            button.setIcon(icon(icon_name))
            button.setIconSize(QtCore.QSize(22, 22))
            button.setText(label)
            button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setCheckable(True)
            button.setFixedSize(84, 58)
            button.clicked.connect(lambda _=False, k=key: self.navigate(k))
            group.addButton(button)
            self.buttons[key] = button
            if key == "settings":
                rail_layout.addStretch()
            rail_layout.addWidget(button)
            self.stack.addWidget(self.pages[key])
        layout.addWidget(rail)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        status = self.statusBar()
        self.project_label = QtWidgets.QLabel()
        self.project_label.setStyleSheet(f"color: {TEXT_DIM}; padding: 0 8px;")
        status.addPermanentWidget(self.project_label)
        self.job_label = QtWidgets.QLabel()
        self.job_label.setStyleSheet("padding: 0 6px;")
        self.job_bar = QtWidgets.QProgressBar()
        self.job_bar.setFixedWidth(180)
        self.job_bar.setRange(0, 1000)
        self.job_bar.setTextVisible(False)
        self.job_bar.hide()
        status.addPermanentWidget(self.job_label)
        status.addPermanentWidget(self.job_bar)

        state.navigateRequested.connect(self.navigate)
        state.openVideoRequested.connect(lambda _vid: self.navigate("editor"))
        state.projectOpened.connect(self._update_project_label)
        state.projectChanged.connect(self._update_project_label)
        state.jobs.jobChanged.connect(self._on_job)
        state.jobs.busyChanged.connect(self._on_busy)

        self._restore_geometry()
        self.navigate("projects")
        QtCore.QTimer.singleShot(0, self._startup)

    def navigate(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        self.buttons[key].setChecked(True)

    def _update_project_label(self) -> None:
        project = self.state.project
        self.project_label.setText(f"Project: {project.name}" if project else "Chưa mở project")
        self.setWindowTitle(f"{project.name} — {APP_NAME}" if project else f"{APP_NAME} {APP_VERSION}")

    def _on_job(self, job) -> None:
        if job.state == RUNNING:
            self.job_label.setText(f"{job.video_name}: {job.message[:50]}")
            self.job_bar.setValue(int(job.progress * 10))

    def _on_busy(self, busy: bool) -> None:
        self.job_bar.setVisible(busy)
        pending = sum(1 for j in self.state.jobs.jobs if j.state == "pending")
        if not busy:
            self.job_label.setText("Hàng đợi trống")
        elif pending:
            self.job_label.setToolTip(f"Còn {pending} việc chờ")

    def _startup(self) -> None:
        settings = self.state.settings
        last = settings.last_project
        if last and ProjectStore.is_project_dir(Path(last)):
            try:
                self.state.open_project(last)
                if self.state.project and self.state.project.videos:
                    self.navigate("editor")
            except Exception:  # noqa: BLE001
                pass
        self._update_project_label()
        if not find_tool("ffmpeg") or not find_tool("ffprobe"):
            if confirm(self, "Thiếu FFmpeg", "Chưa có FFmpeg — cần để xử lý video. Mở trang Tài nguyên để tải ngay?"):
                self.navigate("resources")

    def _restore_geometry(self) -> None:
        state = self.state.settings.window_state
        geometry = state.get("geometry")
        if geometry:
            self.restoreGeometry(QtCore.QByteArray.fromBase64(geometry.encode("ascii")))

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.state.jobs.is_busy():
            if not confirm(self, "Đang xử lý", "Hàng đợi đang chạy. Dừng và thoát?"):
                event.ignore()
                return
        self.state.flushRequested.emit()
        editor: EditorPage = self.pages["editor"]  # type: ignore[assignment]
        editor.shutdown()
        self.state.jobs.shutdown()
        self.state.settings.window_state["geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.state.settings_store.save()
        super().closeEvent(event)
