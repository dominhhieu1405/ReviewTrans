"""Ô chọn provider + model (dịch) / giọng (TTS) cho một cấp cấu hình: project hoặc video.

Thứ tự ưu tiên khi chạy: video > project > mặc định (trang Providers). Mỗi ô có lựa chọn
"kế thừa" và luôn hiện dòng "Đang dùng: …" để biết giá trị thực tế lấy từ đâu.
"""
from __future__ import annotations

from typing import Callable

from PyQt6 import QtCore, QtWidgets

from ...core.config import TRANSLATE_KINDS, TTS_KINDS
from ...core.models import clone
from ...core.providers.translate import SUGGESTED_MODELS, create_translator
from ...core.providers.tts import create_tts
from ...core.resolve import FIELDS, SOURCE_LABELS, resolve
from ..jobs import run_background
from ..state import AppState
from ..theme import ACCENT, TEXT_DIM
from .common import ComboBox, error, tool_button

# danh sách model/giọng lấy từ API, dùng chung: (kind, profile_id) -> [(id, nhãn)]
_remote: dict[tuple[str, str], list[tuple[str, str]]] = {}


class _RemoteHub(QtCore.QObject):
    updated = QtCore.pyqtSignal(str, str)  # kind, profile_id


_hub: _RemoteHub | None = None


def remote_hub() -> _RemoteHub:
    global _hub
    if _hub is None:
        _hub = _RemoteHub()
    return _hub


class ProviderField(QtWidgets.QWidget):
    """kind: "translate" | "tts"; scope: "project" | "video"."""

    changed = QtCore.pyqtSignal(str)  # kind

    def __init__(self, state: AppState, kind: str, scope: str, parent=None):
        super().__init__(parent)
        self.state = state
        self.kind = kind
        self.scope = scope
        self.target = None  # Project hoặc VideoDoc
        self.on_save: Callable[[], None] | None = None
        self._loading = False
        self.profile_field, self.value_field = FIELDS[kind]

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        self.profile = ComboBox()
        self.profile.setMinimumWidth(180)
        self.value = ComboBox()
        self.value.setMinimumWidth(180)
        self.value.setEditable(kind == "translate")  # model gõ tay được; giọng chọn từ danh sách
        self.fetch = tool_button(
            "refresh", "Tải danh sách model từ API" if kind == "translate" else "Tải danh sách giọng", self.fetch_remote
        )
        if scope == "video":  # inspector hẹp → xếp dọc
            self.profile.setMinimumWidth(0)
            self.value.setMinimumWidth(0)
            layout.addWidget(self.profile)
            row.addWidget(self.value, 1)
            row.addWidget(self.fetch)
            layout.addLayout(row)
        else:
            row.addWidget(self.profile, 1)
            row.addWidget(self.value, 1)
            row.addWidget(self.fetch)
            layout.addLayout(row)
        self.status = QtWidgets.QLabel()
        self.status.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.commit_timer = QtCore.QTimer(self, singleShot=True, interval=700, timeout=self.commit)
        self.profile.currentIndexChanged.connect(self._on_profile)
        if kind == "translate":
            self.value.currentTextChanged.connect(self._on_value_text)
            self.value.activated.connect(lambda _i: self.commit())
        else:
            self.value.currentIndexChanged.connect(lambda _i: self.commit())
        state.settingsChanged.connect(self.reload)
        state.flushRequested.connect(self._flush)
        remote_hub().updated.connect(self._on_remote_updated)
        if scope == "project":
            state.projectOpened.connect(self._bind_project)
            state.projectChanged.connect(self._bind_project)
            self._bind_project()
        else:
            # cấp video hiển thị giá trị kế thừa từ project nên cũng phải cập nhật khi project đổi
            state.projectChanged.connect(self.reload)

    # ------------------------------------------------------------ gắn dữ liệu

    def _bind_project(self) -> None:
        self.set_target(self.state.project)

    def set_target(self, target, on_save: Callable[[], None] | None = None) -> None:
        self.target = target
        if on_save is not None:
            self.on_save = on_save
        self.reload()

    def _profiles(self):
        settings = self.state.settings
        return settings.translate_profiles if self.kind == "translate" else settings.tts_profiles

    def _inherited(self):
        if self.scope == "project":
            return resolve(self.state.settings, self.kind, None)
        return resolve(self.state.settings, self.kind, self.state.project)

    def _effective(self):
        if self.scope == "project":
            return resolve(self.state.settings, self.kind, self.target)
        return resolve(self.state.settings, self.kind, self.state.project, self.target)

    # ------------------------------------------------------------ hiển thị

    def reload(self) -> None:
        self.setEnabled(self.target is not None)
        if self.target is None or self.commit_timer.isActive():
            return
        kinds = TRANSLATE_KINDS if self.kind == "translate" else TTS_KINDS
        inherited = self._inherited()
        inherit_name = inherited.profile.name if inherited.profile else "chưa có"
        prefix = "Mặc định" if self.scope == "project" else "Theo project"
        items = [("", f"{prefix} ({inherit_name})")]
        items += [(p.id, f"{p.name} · {kinds.get(p.kind, p.kind)}") for p in self._profiles()]
        own_profile = getattr(self.target, self.profile_field, "")
        known = {p.id for p in self._profiles()}
        self._loading = True
        self.profile.set_items(items, keep=False)
        self.profile.set_value(own_profile if own_profile in known else "")
        self._fill_values()
        self._loading = False
        self._update_status()

    def _fill_values(self) -> None:
        effective = self._effective()
        inherited = self._inherited()
        profile = effective.profile
        own_value = str(getattr(self.target, self.value_field, "") or "")
        same_profile = inherited.profile is not None and profile is not None and inherited.profile.id == profile.id
        if self.kind == "translate":
            default_model = (inherited.value if same_profile and inherited.value else "") or (profile.model if profile else "")
            models = [m for m, _l in _remote.get(("translate", profile.id), [])] if profile else []
            models = models or (SUGGESTED_MODELS.get(profile.kind, []) if profile else [])
            self.value.blockSignals(True)
            self.value.clear()
            self.value.addItems(models)
            self.value.setEditText(own_value)
            self.value.blockSignals(False)
            llm = profile is not None and profile.is_llm
            self.value.setEnabled(llm)
            self.fetch.setEnabled(llm)
            self.value.lineEdit().setPlaceholderText(
                ("kế thừa: " + default_model) if llm and default_model else ("model mặc định" if llm else "không dùng model")
            )
        else:
            default_voice = (inherited.value if same_profile and inherited.value else "") or (
                profile.option("voice", "") if profile else ""
            )
            voices = _remote.get(("tts", profile.id), []) if profile else []
            items = [("", f"Kế thừa ({default_voice or 'giọng mặc định của provider'})")] + list(voices)
            if own_value and all(v != own_value for v, _l in voices):
                items.append((own_value, own_value))
            self.value.blockSignals(True)
            self.value.set_items(items, keep=False)
            self.value.set_value(own_value)
            self.value.blockSignals(False)
            self.fetch.setEnabled(profile is not None and profile.kind in ("edge", "openai_speech"))
            # tự tải danh sách giọng một lần cho mỗi provider (lỗi cũng ghi nhận để không lặp lại)
            if profile is not None and ("tts", profile.id) not in _remote and profile.kind in ("edge", "openai_speech"):
                _remote[("tts", profile.id)] = []
                QtCore.QTimer.singleShot(0, lambda: self.fetch_remote(silent=True))

    def _update_status(self) -> None:
        effective = self._effective()
        text = effective.describe(self.kind)
        if effective.value and effective.value_source and effective.value_source != effective.profile_source:
            label = "model" if self.kind == "translate" else "giọng"
            text += f", {label} từ {SOURCE_LABELS[effective.value_source]}"
        overridden = effective.profile_source == self.scope or effective.value_source == self.scope
        color = ACCENT if overridden else TEXT_DIM
        self.status.setStyleSheet(f"color: {color}; font-size: 12px;")
        self.status.setText("Đang dùng: " + text)

    # ------------------------------------------------------------ ghi

    def _on_remote_updated(self, kind: str, profile_id: str) -> None:
        profile = self._effective().profile if self.target is not None else None
        if kind == self.kind and profile is not None and profile.id == profile_id:
            self._loading = True
            self._fill_values()
            self._loading = False

    def _on_profile(self, _index: int) -> None:
        if self._loading or self.target is None:
            return
        setattr(self.target, self.profile_field, self.profile.value() or "")
        setattr(self.target, self.value_field, "")  # model/giọng cũ thuộc provider cũ
        self._loading = True
        self._fill_values()
        self._loading = False
        self._save()

    def _on_value_text(self, _text: str) -> None:
        if not self._loading:
            self.commit_timer.start()

    def _flush(self) -> None:
        if self.commit_timer.isActive():
            self.commit()

    def commit(self) -> None:
        self.commit_timer.stop()
        if self._loading or self.target is None:
            return
        if self.kind == "translate":
            value = self.value.currentText().strip()
        else:
            value = self.value.value() or ""
        if getattr(self.target, self.value_field, "") == value:
            return
        setattr(self.target, self.value_field, value)
        self._save()

    def _save(self) -> None:
        self._update_status()
        if self.scope == "project":
            self.state.save_project()
        elif self.on_save:
            self.on_save()
        self.changed.emit(self.kind)

    # ------------------------------------------------------------ tải danh sách từ API

    def fetch_remote(self, silent: bool = False) -> None:
        profile = self._effective().profile
        if profile is None:
            return
        snapshot = clone(profile)
        language = self.state.project.target_language if self.state.project else ""
        key = (self.kind, snapshot.id)
        self.fetch.setEnabled(False)

        def work(_progress, _stop):
            if self.kind == "translate":
                return [(m, m) for m in create_translator(snapshot).list_models()]
            return create_tts(snapshot).list_voices(language)

        def done(items) -> None:
            self.fetch.setEnabled(True)
            _remote[key] = items
            remote_hub().updated.emit(key[0], key[1])
            if not silent:
                self.value.showPopup()

        def fail(message: str) -> None:
            self.fetch.setEnabled(True)
            _remote.setdefault(key, [])
            if not silent:
                error(self, message, "Không lấy được danh sách")

        run_background(work, done, fail)
