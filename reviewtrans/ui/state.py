from __future__ import annotations

from pathlib import Path

from PyQt6 import QtCore

from ..core.config import SettingsStore
from ..core.models import Project, ProjectContext
from ..core.store import ProjectStore
from .jobs import Job, JobQueue


class AppState(QtCore.QObject):
    """Trạng thái dùng chung giữa các trang."""

    projectOpened = QtCore.pyqtSignal()
    projectChanged = QtCore.pyqtSignal()  # thông tin project / danh sách video đổi
    contextChanged = QtCore.pyqtSignal()
    settingsChanged = QtCore.pyqtSignal()
    videoChanged = QtCore.pyqtSignal(str)  # video_id của project đang mở được job cập nhật
    openVideoRequested = QtCore.pyqtSignal(str)
    navigateRequested = QtCore.pyqtSignal(str)
    flushRequested = QtCore.pyqtSignal()  # yêu cầu editor lưu ngay trước khi chạy job

    def __init__(self, settings_store: SettingsStore) -> None:
        super().__init__()
        self.settings_store = settings_store
        self.store: ProjectStore | None = None
        self.project: Project | None = None
        self.jobs = JobQueue(settings_store, self)
        self.jobs.videoChanged.connect(self._on_job_video_changed)
        self.jobs.contextChanged.connect(self._on_job_context_changed)

    @property
    def settings(self):
        return self.settings_store.settings

    def save_settings(self) -> None:
        self.settings_store.save()
        self.settingsChanged.emit()

    # ------------------------------------------------------------ project

    def open_project(self, path: str | Path) -> None:
        store = ProjectStore.open(path)
        self.store = store
        self.project = store.load_project()
        self.settings.add_recent(str(store.root))
        self.settings_store.save()
        self.projectOpened.emit()

    def create_project(self, parent: Path, name: str) -> None:
        store, project = ProjectStore.create(parent, name)
        settings = self.settings
        project.source_language = settings.default_source_language
        project.target_language = settings.default_target_language
        # để trống = luôn theo provider mặc định trong trang Providers
        project.translate_profile = ""
        project.tts_profile = ""
        project.whisper_model = settings.default_whisper_model
        store.save_project(project)
        self.open_project(store.root)

    def close_project(self) -> None:
        self.store = None
        self.project = None
        self.projectOpened.emit()

    def save_project(self) -> None:
        if self.store and self.project:
            self.store.save_project(self.project)
            self.projectChanged.emit()

    def reload_project(self) -> None:
        if self.store:
            self.project = self.store.load_project()
            self.projectChanged.emit()

    def load_context(self) -> ProjectContext:
        return self.store.load_context() if self.store else ProjectContext()

    def save_context(self, context: ProjectContext) -> None:
        if self.store:
            self.store.save_context(context)
            self.contextChanged.emit()

    # ------------------------------------------------------------ jobs

    def enqueue(self, video_ids: list[str], steps: list[str], only_missing: bool = True, force_context: bool = False) -> list[Job]:
        if not (self.store and self.project):
            return []
        self.flushRequested.emit()
        self.save_project()
        names = {v.id: v.name for v in self.project.videos}
        order = [v.id for v in self.project.videos]
        added = []
        for video_id in sorted(video_ids, key=lambda vid: order.index(vid) if vid in order else 0):
            job = Job(
                project_root=str(self.store.root),
                project_name=self.project.name,
                video_id=video_id,
                video_name=names.get(video_id, video_id),
                steps=list(steps),
                only_missing=only_missing,
                force_context=force_context,
            )
            added.append(self.jobs.add(job))
        return added

    def is_video_locked(self, video_id: str) -> bool:
        return bool(self.store) and self.jobs.is_locked(str(self.store.root), video_id)

    def _on_job_video_changed(self, project_root: str, video_id: str) -> None:
        if self.store and Path(project_root) == self.store.root:
            self.videoChanged.emit(video_id)

    def _on_job_context_changed(self, project_root: str) -> None:
        if self.store and Path(project_root) == self.store.root:
            self.contextChanged.emit()
