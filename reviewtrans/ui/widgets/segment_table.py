from __future__ import annotations

import re

from PyQt6 import QtCore, QtGui, QtWidgets

from ...core.models import Segment
from ..theme import ACCENT_DIM, DANGER, SUCCESS, TEXT_DIM, WARNING

COLUMNS = ["#", "Bắt đầu", "Kết thúc", "Câu gốc", "Bản dịch", "TTS"]
COL_ID, COL_START, COL_END, COL_SOURCE, COL_TEXT, COL_TTS = range(6)
TTS_LABELS = {"ok": "✓", "missing": "—", "stale": "cũ", "fast": "nhanh"}


def short_time(seconds: float) -> str:
    minutes, secs = divmod(max(0.0, seconds), 60)
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours}:{minutes:02d}:{secs:05.2f}" if hours else f"{minutes}:{secs:05.2f}"


def parse_time(text: str) -> float | None:
    text = text.strip().replace(",", ".")
    match = re.fullmatch(r"(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    parts = [p for p in match.groups() if p is not None]
    values = [float(p) for p in parts]
    seconds = 0.0
    for value in values:
        seconds = seconds * 60 + value
    return seconds


class SegmentModel(QtCore.QAbstractTableModel):
    edited = QtCore.pyqtSignal(object, str)  # segment, field

    def __init__(self, parent=None):
        super().__init__(parent)
        self.segments: list[Segment] = []
        self.tts_status: dict[int, str] = {}
        self.active_id = -1
        self.read_only = False

    def set_segments(self, segments: list[Segment]) -> None:
        self.beginResetModel()
        self.segments = segments
        self.endResetModel()

    def set_tts_status(self, status: dict[int, str]) -> None:
        self.tts_status = status
        if self.segments:
            self.dataChanged.emit(self.index(0, COL_TTS), self.index(len(self.segments) - 1, COL_TTS))

    def set_active(self, segment_id: int) -> None:
        if segment_id == self.active_id:
            return
        old = self.row_of(self.active_id)
        self.active_id = segment_id
        for row in (old, self.row_of(segment_id)):
            if row >= 0:
                self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))

    def row_of(self, segment_id: int) -> int:
        for row, seg in enumerate(self.segments):
            if seg.id == segment_id:
                return row
        return -1

    def refresh_row(self, segment: Segment) -> None:
        row = self.row_of(segment.id)
        if row >= 0:
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))

    # ------------------------------------------------------------ Qt API

    def rowCount(self, parent=QtCore.QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.segments)

    def columnCount(self, parent=QtCore.QModelIndex()) -> int:  # noqa: N802
        return len(COLUMNS)

    def headerData(self, section, orientation, role=QtCore.Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == QtCore.Qt.Orientation.Horizontal and role == QtCore.Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def flags(self, index):
        base = QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
        if not self.read_only and index.column() in (COL_START, COL_END, COL_SOURCE, COL_TEXT):
            base |= QtCore.Qt.ItemFlag.ItemIsEditable
        return base

    def data(self, index, role=QtCore.Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        seg = self.segments[index.row()]
        col = index.column()
        if role in (QtCore.Qt.ItemDataRole.DisplayRole, QtCore.Qt.ItemDataRole.EditRole):
            if col == COL_ID:
                return f"{seg.id}{' 🔒' if seg.locked else ''}"
            if col == COL_START:
                return short_time(seg.start)
            if col == COL_END:
                return short_time(seg.end)
            if col == COL_SOURCE:
                return seg.source
            if col == COL_TEXT:
                return seg.text
            if col == COL_TTS and role == QtCore.Qt.ItemDataRole.DisplayRole:
                return TTS_LABELS.get(self.tts_status.get(seg.id, "missing"), "")
        if role == QtCore.Qt.ItemDataRole.ForegroundRole:
            if col == COL_TTS:
                state = self.tts_status.get(seg.id, "missing")
                return QtGui.QColor({"ok": SUCCESS, "stale": WARNING, "fast": WARNING}.get(state, TEXT_DIM))
            if col == COL_TEXT and not seg.text:
                return QtGui.QColor(DANGER)
            if col in (COL_ID, COL_START, COL_END):
                return QtGui.QColor(TEXT_DIM)
        if role == QtCore.Qt.ItemDataRole.BackgroundRole and seg.id == self.active_id:
            return QtGui.QColor(ACCENT_DIM)
        if role == QtCore.Qt.ItemDataRole.ToolTipRole and col in (COL_SOURCE, COL_TEXT):
            return seg.source if col == COL_SOURCE else seg.text
        if role == QtCore.Qt.ItemDataRole.TextAlignmentRole and col in (COL_ID, COL_TTS):
            return QtCore.Qt.AlignmentFlag.AlignCenter
        return None

    def setData(self, index, value, role=QtCore.Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if role != QtCore.Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        seg = self.segments[index.row()]
        col = index.column()
        if col in (COL_START, COL_END):
            seconds = parse_time(str(value))
            if seconds is None:
                return False
            if col == COL_START:
                if seconds >= seg.end:
                    return False
                seg.start = seconds
                field = "start"
            else:
                if seconds <= seg.start:
                    return False
                seg.end = seconds
                field = "end"
        elif col == COL_SOURCE:
            if str(value) == seg.source:
                return False
            seg.source = str(value)
            field = "source"
        elif col == COL_TEXT:
            if str(value) == seg.text:
                return False
            seg.text = str(value)
            field = "text"
        else:
            return False
        self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), len(COLUMNS) - 1))
        self.edited.emit(seg, field)
        return True


class _MultilineDelegate(QtWidgets.QStyledItemDelegate):
    def createEditor(self, parent, option, index):  # noqa: N802
        if index.column() in (COL_SOURCE, COL_TEXT):
            editor = QtWidgets.QLineEdit(parent)
            return editor
        return super().createEditor(parent, option, index)


class SegmentTable(QtWidgets.QTableView):
    segmentActivated = QtCore.pyqtSignal(int)
    contextAction = QtCore.pyqtSignal(str, list)  # action, [segment ids]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_ = SegmentModel(self)
        self.proxy = QtCore.QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model_)
        self.proxy.setFilterCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.setModel(self.proxy)
        self.setItemDelegate(_MultilineDelegate(self))
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(24)
        header = self.horizontalHeader()
        header.setSectionResizeMode(COL_SOURCE, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_TEXT, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for col, width in ((COL_ID, 42), (COL_START, 62), (COL_END, 62), (COL_TTS, 48)):
            self.setColumnWidth(col, width)
        self.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked
            | QtWidgets.QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.clicked.connect(self._on_clicked)

    def set_filter(self, text: str) -> None:
        self.proxy.setFilterFixedString(text)

    def segment_at(self, proxy_index) -> Segment | None:
        source = self.proxy.mapToSource(proxy_index)
        if not source.isValid():
            return None
        return self.model_.segments[source.row()]

    def selected_ids(self) -> list[int]:
        ids = []
        for index in self.selectionModel().selectedRows():
            seg = self.segment_at(index)
            if seg:
                ids.append(seg.id)
        return ids

    def select_segment(self, segment_id: int, scroll: bool = True) -> None:
        row = self.model_.row_of(segment_id)
        if row < 0:
            return
        index = self.proxy.mapFromSource(self.model_.index(row, 0))
        if index.isValid():
            self.selectionModel().select(
                index,
                QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect | QtCore.QItemSelectionModel.SelectionFlag.Rows,
            )
            self.setCurrentIndex(index)
            if scroll:
                self.scrollTo(index, QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter)

    def follow(self, segment_id: int) -> None:
        self.model_.set_active(segment_id)
        row = self.model_.row_of(segment_id)
        if row >= 0 and self.state() != QtWidgets.QAbstractItemView.State.EditingState:
            index = self.proxy.mapFromSource(self.model_.index(row, 0))
            if index.isValid():
                self.scrollTo(index, QtWidgets.QAbstractItemView.ScrollHint.EnsureVisible)

    def _on_clicked(self, index) -> None:
        seg = self.segment_at(index)
        if seg:
            self.segmentActivated.emit(seg.id)

    def _menu(self, pos) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        menu = QtWidgets.QMenu(self)
        actions = [
            ("play", "Phát từ câu này"),
            (None, None),
            ("translate", "Dịch lại câu đã chọn"),
            ("tts", "Tạo lại lồng tiếng câu đã chọn"),
            ("listen", "Nghe lồng tiếng"),
            (None, None),
            ("lock", "Khoá / mở khoá bản dịch"),
            ("copy_source", "Chép câu gốc sang bản dịch"),
            ("clear_text", "Xoá bản dịch"),
            (None, None),
            ("insert", "Thêm câu sau"),
            ("split", "Tách câu tại vị trí phát"),
            ("merge", "Gộp các câu đã chọn"),
            ("delete", "Xoá câu"),
        ]
        for key, label in actions:
            if key is None:
                menu.addSeparator()
                continue
            action = menu.addAction(label)
            action.triggered.connect(lambda _=False, k=key: self.contextAction.emit(k, ids))
        menu.exec(self.viewport().mapToGlobal(pos))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == QtCore.Qt.Key.Key_Delete and self.state() != QtWidgets.QAbstractItemView.State.EditingState:
            ids = self.selected_ids()
            if ids:
                self.contextAction.emit("delete", ids)
                return
        super().keyPressEvent(event)
