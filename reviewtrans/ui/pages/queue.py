from __future__ import annotations

import time

from PyQt6 import QtCore, QtGui, QtWidgets

from ..jobs import CANCELLED, DONE, ERROR, JOB_STATE_LABELS, PENDING, RUNNING, STOPPED, Job
from ..state import AppState
from ..theme import ACCENT, DANGER, SUCCESS, TEXT_DIM, WARNING
from ..widgets.common import LogView, push_button, title_label

COLORS = {PENDING: TEXT_DIM, RUNNING: ACCENT, DONE: SUCCESS, ERROR: DANGER, STOPPED: WARNING, CANCELLED: TEXT_DIM}


class QueuePage(QtWidgets.QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.queue = state.jobs
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        header = QtWidgets.QHBoxLayout()
        header.addWidget(title_label("Hàng đợi xử lý", "Các video chạy lần lượt theo thứ tự thêm vào; ngữ cảnh project được cập nhật sau mỗi video."), 1)
        header.addWidget(push_button("Dừng việc đang chạy", self.queue.stop_current, "stop", "Danger"))
        header.addWidget(push_button("Huỷ tất cả", self.queue.cancel_all, "delete"))
        header.addWidget(push_button("Xoá việc đã xong", self._clear, "refresh"))
        layout.addLayout(header)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["#", "Project", "Video", "Các bước", "Trạng thái", "Tiến độ", "Thời gian"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(5, 220)
        self.table.itemSelectionChanged.connect(self._show_log)
        self.table.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        splitter.addWidget(self.table)
        log_box = QtWidgets.QWidget()
        log_layout = QtWidgets.QVBoxLayout(log_box)
        log_layout.setContentsMargins(0, 0, 0, 0)
        self.log_title = QtWidgets.QLabel("Log")
        self.log_title.setStyleSheet(f"color: {TEXT_DIM};")
        log_layout.addWidget(self.log_title)
        self.log = LogView()
        log_layout.addWidget(self.log)
        splitter.addWidget(log_box)
        splitter.setSizes([300, 400])
        layout.addWidget(splitter, 1)

        self.rows: dict[int, int] = {}
        self.bars: dict[int, QtWidgets.QProgressBar] = {}
        self.queue.jobAdded.connect(self._add_job)
        self.queue.jobChanged.connect(self._update_job)
        self.queue.jobLog.connect(self._on_log)
        self.timer = QtCore.QTimer(self, interval=1000, timeout=self._tick)
        self.timer.start()

    def _add_job(self, job: Job) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.rows[job.id] = row
        for col, text in enumerate([str(job.id), job.project_name, job.video_name, job.steps_label, "", "", ""]):
            item = QtWidgets.QTableWidgetItem(text)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, job.id)
            self.table.setItem(row, col, item)
        bar = QtWidgets.QProgressBar()
        bar.setRange(0, 1000)
        self.bars[job.id] = bar
        self.table.setCellWidget(row, 5, bar)
        self._update_job(job)
        if self.table.currentRow() < 0:
            self.table.selectRow(row)

    def _update_job(self, job: Job) -> None:
        row = self.rows.get(job.id)
        if row is None:
            return
        state_item = self.table.item(row, 4)
        state_item.setText(JOB_STATE_LABELS.get(job.state, job.state))
        state_item.setForeground(QtGui.QColor(COLORS.get(job.state, TEXT_DIM)))
        state_item.setToolTip(job.error)
        bar = self.bars[job.id]
        bar.setValue(int(job.progress * 10))
        bar.setFormat(f"{job.progress:.0f}% {job.message[:40]}")
        self.table.item(row, 6).setText(self._elapsed(job))

    def _tick(self) -> None:
        current = self.queue.current()
        if current:
            row = self.rows.get(current.id)
            if row is not None:
                self.table.item(row, 6).setText(self._elapsed(current))

    @staticmethod
    def _elapsed(job: Job) -> str:
        seconds = int(job.elapsed())
        return time.strftime("%H:%M:%S", time.gmtime(seconds)) if seconds else ""

    def _selected_job(self) -> Job | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        job_id = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else None
        return next((j for j in self.queue.jobs if j.id == job_id), None)

    def _show_log(self) -> None:
        job = self._selected_job()
        self.log.clear()
        if job:
            self.log_title.setText(f"Log — {job.video_name}")
            self.log.setPlainText("\n".join(job.logs))
            self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _on_log(self, job: Job, line: str) -> None:
        selected = self._selected_job()
        if selected is None or selected.id == job.id:
            if selected is None:
                row = self.rows.get(job.id)
                if row is not None:
                    self.table.selectRow(row)
                    return
            self.log.append_line(line)

    def _menu(self, pos) -> None:
        job = self._selected_job()
        if not job:
            return
        menu = QtWidgets.QMenu(self)
        if job.state in (PENDING, RUNNING):
            menu.addAction("Huỷ / dừng", lambda: self.queue.cancel(job))
        if job.state in (ERROR, STOPPED, CANCELLED, DONE):
            menu.addAction("Chạy lại", lambda: self.queue.retry(job))
        menu.addAction("Chép log", lambda: QtWidgets.QApplication.clipboard().setText("\n".join(job.logs)))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _clear(self) -> None:
        self.queue.clear_finished()
        keep = {j.id for j in self.queue.jobs}
        for row in reversed(range(self.table.rowCount())):
            job_id = self.table.item(row, 0).data(QtCore.Qt.ItemDataRole.UserRole)
            if job_id not in keep:
                self.table.removeRow(row)
                self.bars.pop(job_id, None)
        self.rows = {self.table.item(r, 0).data(QtCore.Qt.ItemDataRole.UserRole): r for r in range(self.table.rowCount())}
        self.log.clear()
