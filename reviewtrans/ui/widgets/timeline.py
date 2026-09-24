from __future__ import annotations

import bisect
from dataclasses import dataclass
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets

from ...core.models import LAYER_TYPES, Layer, Segment, VideoDoc
from ...core.pipeline.mix import Clip, plan_clips
from ..icons import icon
from ..theme import ACCENT, BORDER, PANEL, PANEL_2, TEXT, TEXT_DIM, TRACK_COLORS, WARNING

HEADER_W = 170
RULER_H = 24
EDGE = 5
STEPS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200]


@dataclass
class Row:
    kind: str  # layer | subtitle | dub | original | bgm | video
    label: str
    height: int
    layer: Layer | None = None


class Timeline(QtWidgets.QWidget):
    seekRequested = QtCore.pyqtSignal(float)
    segmentSelected = QtCore.pyqtSignal(int)
    segmentsEdited = QtCore.pyqtSignal(list)
    layerSelected = QtCore.pyqtSignal(str)
    layerEdited = QtCore.pyqtSignal(str)
    layerToggled = QtCore.pyqtSignal(str)
    tracksChanged = QtCore.pyqtSignal()
    viewChanged = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc: VideoDoc | None = None
        self.segments: list[Segment] = []
        self._starts: list[float] = []
        self.clips: list[Clip] = []
        self._clip_starts: list[float] = []
        self.rows: list[Row] = []
        self.duration = 60.0
        self.pps = 40.0
        self.offset = 0.0
        self.time = 0.0
        self.selected_segment = -1
        self.selected_layer = ""
        self.peaks: list[float] = []
        self.peaks_rate = 20
        self.read_only = False
        self._drag: dict | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)

    # ------------------------------------------------------------ dữ liệu

    def set_document(self, doc: VideoDoc | None, segments: list[Segment]) -> None:
        self.doc = doc
        self.duration = max(1.0, (doc.duration if doc else 0) or 60.0)
        self.set_segments(segments)
        self.rebuild_rows()

    def set_segments(self, segments: list[Segment]) -> None:
        self.segments = sorted(segments, key=lambda s: s.start)
        self._starts = [s.start for s in self.segments]
        self.refresh_clips()

    def refresh_clips(self) -> None:
        self.clips = plan_clips(self.doc, self.segments) if self.doc else []
        self._clip_starts = [c.start for c in self.clips]
        self.update()

    def set_peaks(self, peaks: list[float], rate: int) -> None:
        self.peaks = peaks
        self.peaks_rate = rate
        self.update()

    def rebuild_rows(self) -> None:
        rows: list[Row] = []
        if self.doc:
            for layer in reversed(self.doc.layers):
                rows.append(Row("layer", layer.label(), 22, layer))
        rows += [
            Row("subtitle", "Phụ đề", 30),
            Row("dub", "Lồng tiếng", 30),
            Row("original", "Âm gốc", 30),
            Row("bgm", "Nhạc nền", 22),
            Row("video", "Video", 22),
        ]
        self.rows = rows
        self.setMinimumHeight(RULER_H + sum(r.height for r in rows) + 2)
        self.updateGeometry()
        self.update()

    def set_time(self, seconds: float, follow: bool = False) -> None:
        self.time = seconds
        if follow:
            width = self.content_width()
            if seconds < self.offset or seconds > self.offset + width / self.pps * 0.92:
                self.set_offset(seconds - width / self.pps * 0.1)
        self.update()

    # ------------------------------------------------------------ view

    def content_width(self) -> float:
        return max(10.0, self.width() - HEADER_W)

    def visible_seconds(self) -> float:
        return self.content_width() / self.pps

    def max_offset(self) -> float:
        return max(0.0, self.duration - self.visible_seconds() * 0.9)

    def set_offset(self, offset: float) -> None:
        self.offset = max(0.0, min(self.max_offset(), offset))
        self.update()
        self.viewChanged.emit()

    def set_zoom(self, pps: float, anchor_time: float | None = None) -> None:
        anchor = self.time if anchor_time is None else anchor_time
        anchor_x = self.x_of(anchor)
        self.pps = max(1.0, min(600.0, pps))
        self.offset = anchor - (anchor_x - HEADER_W) / self.pps
        self.set_offset(self.offset)

    def zoom_fit(self) -> None:
        self.pps = max(1.0, self.content_width() / self.duration)
        self.set_offset(0)

    def x_of(self, t: float) -> float:
        return HEADER_W + (t - self.offset) * self.pps

    def t_of(self, x: float) -> float:
        return self.offset + (x - HEADER_W) / self.pps

    def _row_at(self, y: float) -> tuple[int, Row | None, float]:
        top = RULER_H
        for index, row in enumerate(self.rows):
            if top <= y < top + row.height:
                return index, row, top
            top += row.height
        return -1, None, 0

    def _row_top(self, index: int) -> int:
        return RULER_H + sum(r.height for r in self.rows[:index])

    # ------------------------------------------------------------ vẽ

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(PANEL))
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)
        self._paint_ruler(painter)
        top = RULER_H
        for row in self.rows:
            self._paint_row(painter, row, top)
            top += row.height
        # playhead
        x = self.x_of(self.time)
        if x >= HEADER_W:
            painter.setPen(QtGui.QPen(QtGui.QColor("#ff5a5a"), 1.5))
            painter.drawLine(QtCore.QPointF(x, 0), QtCore.QPointF(x, self.height()))
            painter.setBrush(QtGui.QColor("#ff5a5a"))
            painter.drawPolygon(QtGui.QPolygonF([
                QtCore.QPointF(x - 6, 0), QtCore.QPointF(x + 6, 0), QtCore.QPointF(x, 8),
            ]))
        painter.end()

    def _paint_ruler(self, painter: QtGui.QPainter) -> None:
        painter.fillRect(QtCore.QRectF(0, 0, self.width(), RULER_H), QtGui.QColor(PANEL_2))
        painter.setPen(QtGui.QColor(BORDER))
        painter.drawLine(0, RULER_H - 1, self.width(), RULER_H - 1)
        step = next((s for s in STEPS if s * self.pps >= 70), STEPS[-1])
        start = int(self.offset / step) * step
        font = painter.font()
        font.setPixelSize(10)
        painter.setFont(font)
        t = start
        end = self.offset + self.visible_seconds()
        while t <= end + step:
            x = self.x_of(t)
            if x >= HEADER_W:
                painter.setPen(QtGui.QColor(TEXT_DIM))
                painter.drawLine(QtCore.QPointF(x, RULER_H - 8), QtCore.QPointF(x, RULER_H - 1))
                minutes, seconds = divmod(t, 60)
                label = f"{int(minutes)}:{seconds:04.1f}" if step < 1 else f"{int(minutes)}:{int(seconds):02d}"
                painter.drawText(QtCore.QPointF(x + 3, 12), label)
                for sub in range(1, 5):
                    sx = self.x_of(t + step * sub / 5)
                    if sx > HEADER_W:
                        painter.drawLine(QtCore.QPointF(sx, RULER_H - 4), QtCore.QPointF(sx, RULER_H - 1))
            t += step
        painter.fillRect(QtCore.QRectF(0, 0, HEADER_W, RULER_H), QtGui.QColor(PANEL_2))
        painter.setPen(QtGui.QColor(TEXT))
        m, s = divmod(self.time, 60)
        painter.drawText(QtCore.QRectF(8, 0, HEADER_W - 8, RULER_H), QtCore.Qt.AlignmentFlag.AlignVCenter, f"{int(m):02d}:{s:05.2f}")

    def _row_enabled(self, row: Row) -> bool:
        if not self.doc:
            return True
        tracks = self.doc.tracks
        return {
            "layer": bool(row.layer and row.layer.enabled and tracks.layers),
            "subtitle": tracks.subtitles,
            "dub": tracks.dub,
            "original": tracks.original_audio and self.doc.audio.original_mode != "mute",
            "bgm": tracks.bgm,
            "video": True,
        }[row.kind]

    def _paint_row(self, painter: QtGui.QPainter, row: Row, top: int) -> None:
        rect = QtCore.QRectF(0, top, self.width(), row.height)
        painter.setPen(QtGui.QColor(BORDER))
        painter.drawLine(QtCore.QPointF(0, rect.bottom() - 0.5), QtCore.QPointF(self.width(), rect.bottom() - 0.5))
        enabled = self._row_enabled(row)
        painter.save()
        painter.setClipRect(QtCore.QRectF(HEADER_W, top, self.width() - HEADER_W, row.height - 1))
        painter.setOpacity(1.0 if enabled else 0.35)
        inner = QtCore.QRectF(0, top + 3, 0, row.height - 7)
        if row.kind == "layer" and row.layer is not None:
            self._paint_layer_block(painter, row.layer, inner)
        elif row.kind == "subtitle":
            self._paint_segments(painter, inner)
        elif row.kind == "dub":
            self._paint_clips(painter, inner)
        elif row.kind == "original":
            self._paint_wave(painter, inner, TRACK_COLORS["original"])
        elif row.kind == "bgm":
            if self.doc and self.doc.audio.bgm_path:
                self._block(painter, 0, self.duration, inner, TRACK_COLORS["bgm"], Path(self.doc.audio.bgm_path).name)
        elif row.kind == "video":
            name = Path(self.doc.source_path).name if self.doc else ""
            self._block(painter, 0, self.duration, inner, TRACK_COLORS["video"], name)
        painter.restore()
        # header
        header = QtCore.QRectF(0, top, HEADER_W, row.height - 1)
        selected = row.layer is not None and row.layer.id == self.selected_layer
        painter.fillRect(header, QtGui.QColor(ACCENT if selected else PANEL_2).darker(170 if selected else 100))
        painter.setPen(QtGui.QColor(TEXT if enabled else TEXT_DIM))
        font = painter.font()
        font.setPixelSize(11)
        painter.setFont(font)
        prefix = f"{LAYER_TYPES.get(row.layer.type, '')}: " if row.layer else ""
        text = painter.fontMetrics().elidedText(prefix + row.label, QtCore.Qt.TextElideMode.ElideRight, HEADER_W - 40)
        painter.drawText(header.adjusted(8, 0, -30, 0), QtCore.Qt.AlignmentFlag.AlignVCenter, text)
        toggle = self._toggle_rect(top, row.height)
        if row.kind != "video":
            audio = row.kind in ("dub", "original", "bgm")
            name = ("volume" if enabled else "mute") if audio else ("eye" if enabled else "eye_off")
            icon(name, TEXT if enabled else TEXT_DIM).paint(painter, toggle.toRect())
        painter.setPen(QtGui.QColor(BORDER))
        painter.drawLine(QtCore.QPointF(HEADER_W - 0.5, top), QtCore.QPointF(HEADER_W - 0.5, top + row.height))

    @staticmethod
    def _toggle_rect(top: int, height: int) -> QtCore.QRectF:
        return QtCore.QRectF(HEADER_W - 26, top + (height - 16) / 2, 16, 16)

    def _block(self, painter, start, end, inner: QtCore.QRectF, color: str, label: str = "",
               selected: bool = False, outline: str | None = None) -> QtCore.QRectF:
        x1, x2 = self.x_of(start), self.x_of(end)
        rect = QtCore.QRectF(x1, inner.top(), max(2.0, x2 - x1), inner.height())
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff" if selected else (outline or color)), 1.5 if selected else 1))
        fill = QtGui.QColor(color)
        fill.setAlpha(235 if selected else 190)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 3, 3)
        if label and rect.width() > 18:
            painter.setPen(QtGui.QColor("#ffffff"))
            font = painter.font()
            font.setPixelSize(10)
            painter.setFont(font)
            elided = painter.fontMetrics().elidedText(label, QtCore.Qt.TextElideMode.ElideRight, int(rect.width() - 6))
            painter.drawText(rect.adjusted(4, 0, -2, 0), QtCore.Qt.AlignmentFlag.AlignVCenter, elided)
        return rect

    def _paint_layer_block(self, painter, layer: Layer, inner) -> None:
        start, end = layer.time_range(self.duration)
        self._block(painter, start, end, inner, TRACK_COLORS.get(layer.type, ACCENT), layer.label(),
                    selected=layer.id == self.selected_layer)

    def _visible_range(self, starts: list[float]) -> tuple[int, int]:
        first = max(0, bisect.bisect_left(starts, self.offset - 60) - 1)
        last = bisect.bisect_right(starts, self.offset + self.visible_seconds() + 1)
        return first, last

    def _paint_segments(self, painter, inner) -> None:
        first, last = self._visible_range(self._starts)
        for seg in self.segments[first:last]:
            label = seg.display_text()
            color = TRACK_COLORS["subtitle"] if seg.text else "#7b6a45"
            self._block(painter, seg.start, seg.end, inner, color, label, selected=seg.id == self.selected_segment)

    def _paint_clips(self, painter, inner) -> None:
        first, last = self._visible_range(self._clip_starts)
        for clip in self.clips[first:last]:
            fast = clip.tempo > 1.05
            label = f"x{clip.tempo:.2f} " if fast else ""
            color = WARNING if clip.tempo > 1.35 else TRACK_COLORS["dub"]
            self._block(painter, clip.start, clip.start + clip.length, inner, color,
                        label + clip.segment.text, selected=clip.segment.id == self.selected_segment)

    def _paint_wave(self, painter, inner, color: str) -> None:
        if not self.peaks:
            self._block(painter, 0, self.duration, inner, color, "Âm thanh gốc (chạy nhận dạng để hiện sóng âm)")
            return
        mid = inner.center().y()
        half = inner.height() / 2
        start_index = max(0, int(self.offset * self.peaks_rate))
        end_index = min(len(self.peaks), int((self.offset + self.visible_seconds()) * self.peaks_rate) + 2)
        stride = max(1, int(self.peaks_rate / max(1.0, self.pps)))
        painter.setPen(QtGui.QColor(color).lighter(150))
        for index in range(start_index, end_index, stride):
            peak = max(self.peaks[index:index + stride]) if stride > 1 else self.peaks[index]
            x = self.x_of(index / self.peaks_rate)
            painter.drawLine(QtCore.QPointF(x, mid - peak * half), QtCore.QPointF(x, mid + peak * half))

    # ------------------------------------------------------------ chuột

    def _hit(self, pos: QtCore.QPointF):
        index, row, top = self._row_at(pos.y())
        if row is None or pos.x() < HEADER_W:
            return row, None, None
        t = self.t_of(pos.x())
        tolerance = EDGE / self.pps

        def part(start: float, end: float) -> str | None:
            if start - tolerance <= t <= end + tolerance:
                if abs(t - start) <= tolerance:
                    return "left"
                if abs(t - end) <= tolerance:
                    return "right"
                return "body"
            return None

        if row.kind == "layer" and row.layer is not None:
            start, end = row.layer.time_range(self.duration)
            p = part(start, end)
            return row, (row.layer if p else None), p
        if row.kind in ("subtitle", "dub"):
            first, last = self._visible_range(self._starts)
            for seg in self.segments[first:last]:
                end = seg.end
                if row.kind == "dub":
                    clip = next((c for c in self.clips if c.segment is seg), None)
                    if clip is None:
                        continue
                    end = clip.start + clip.length
                p = part(seg.start, end)
                if p:
                    return row, seg, (p if row.kind == "subtitle" else "body")
        return row, None, None

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        pos = event.position()
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        if pos.y() < RULER_H:
            self._drag = {"type": "scrub"}
            self.seekRequested.emit(max(0.0, min(self.duration, self.t_of(pos.x()))))
            return
        index, row, top = self._row_at(pos.y())
        if row is None:
            return
        if pos.x() < HEADER_W:
            if self._toggle_rect(top, row.height).adjusted(-4, -4, 4, 4).contains(pos):
                self._toggle(row)
            elif row.layer is not None:
                self.selected_layer = row.layer.id
                self.layerSelected.emit(row.layer.id)
                self.update()
            return
        row, obj, part = self._hit(pos)
        t = self.t_of(pos.x())
        if isinstance(obj, Layer):
            self.selected_layer = obj.id
            self.layerSelected.emit(obj.id)
            start, end = obj.time_range(self.duration)
            if not self.read_only:
                self._drag = {"type": "layer", "obj": obj, "part": part, "t0": t, "orig": (start, end), "moved": False}
        elif isinstance(obj, Segment):
            self.selected_segment = obj.id
            self.segmentSelected.emit(obj.id)
            if not self.read_only and row.kind == "subtitle":
                self._drag = {"type": "segment", "obj": obj, "part": part, "t0": t, "orig": (obj.start, obj.end), "moved": False}
            if part == "body":
                self.seekRequested.emit(obj.start)
        else:
            self._drag = {"type": "scrub"}
            self.seekRequested.emit(max(0.0, min(self.duration, t)))
        self.update()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        pos = event.position()
        if not self._drag:
            _row, obj, part = self._hit(pos)
            if obj is not None and part in ("left", "right") and not self.read_only:
                self.setCursor(QtCore.Qt.CursorShape.SizeHorCursor)
            elif obj is not None:
                self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            else:
                self.unsetCursor()
            return
        t = self.t_of(pos.x())
        if self._drag["type"] == "scrub":
            self.seekRequested.emit(max(0.0, min(self.duration, t)))
            return
        dt = t - self._drag["t0"]
        start, end = self._drag["orig"]
        part = self._drag["part"]
        min_len = 0.1
        if part == "body":
            length = end - start
            start = max(0.0, min(self.duration - length, start + dt))
            end = start + length
        elif part == "left":
            start = max(0.0, min(end - min_len, start + dt))
        elif part == "right":
            end = min(self.duration, max(start + min_len, end + dt))
        obj = self._drag["obj"]
        obj.start = round(start, 3)
        if isinstance(obj, Layer):
            obj.end = -1.0 if end >= self.duration - 1e-3 else round(end, 3)
        else:
            obj.end = round(end, 3)
        self._drag["moved"] = True
        if isinstance(obj, Segment):
            self.refresh_clips()
        self.update()

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        drag, self._drag = self._drag, None
        if not drag or not drag.get("moved"):
            return
        if drag["type"] == "segment":
            self.set_segments(self.segments)
            self.segmentsEdited.emit([drag["obj"].id])
        elif drag["type"] == "layer":
            self.layerEdited.emit(drag["obj"].id)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        _row, obj, _part = self._hit(event.position())
        if isinstance(obj, Segment):
            self.seekRequested.emit(obj.start)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # noqa: N802
        delta = event.angleDelta().y() or event.angleDelta().x()
        if event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
            factor = 1.25 if delta > 0 else 0.8
            self.set_zoom(self.pps * factor, self.t_of(event.position().x()))
        else:
            self.set_offset(self.offset - delta / 120.0 * self.visible_seconds() * 0.1)
        event.accept()

    def _toggle(self, row: Row) -> None:
        if not self.doc:
            return
        tracks = self.doc.tracks
        if row.kind == "layer" and row.layer is not None:
            row.layer.enabled = not row.layer.enabled
            self.layerToggled.emit(row.layer.id)
        elif row.kind == "subtitle":
            tracks.subtitles = not tracks.subtitles
        elif row.kind == "dub":
            tracks.dub = not tracks.dub
        elif row.kind == "original":
            tracks.original_audio = not tracks.original_audio
        elif row.kind == "bgm":
            tracks.bgm = not tracks.bgm
        if row.kind != "layer":
            self.tracksChanged.emit()
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.viewChanged.emit()


class TimelinePanel(QtWidgets.QWidget):
    """Timeline + thanh cuộn ngang + nút zoom."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timeline = Timeline()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.timeline)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        layout.addWidget(scroll, 1)
        bottom = QtWidgets.QHBoxLayout()
        bottom.setContentsMargins(4, 2, 4, 2)
        self.scrollbar = QtWidgets.QScrollBar(QtCore.Qt.Orientation.Horizontal)
        bottom.addWidget(self.scrollbar, 1)
        for name, tip, callback in (
            ("zoom_out", "Thu nhỏ (Ctrl + lăn chuột)", lambda: self.timeline.set_zoom(self.timeline.pps * 0.7)),
            ("zoom_in", "Phóng to (Ctrl + lăn chuột)", lambda: self.timeline.set_zoom(self.timeline.pps * 1.4)),
        ):
            button = QtWidgets.QToolButton()
            button.setIcon(icon(name))
            button.setToolTip(tip)
            button.clicked.connect(callback)
            bottom.addWidget(button)
        fit = QtWidgets.QToolButton()
        fit.setText("Vừa khung")
        fit.clicked.connect(self.timeline.zoom_fit)
        bottom.addWidget(fit)
        layout.addLayout(bottom)
        self._syncing = False
        self.timeline.viewChanged.connect(self._sync_scrollbar)
        self.scrollbar.valueChanged.connect(self._on_scroll)

    def _sync_scrollbar(self) -> None:
        self._syncing = True
        t = self.timeline
        self.scrollbar.setRange(0, int(t.max_offset() * 100))
        self.scrollbar.setPageStep(int(t.visible_seconds() * 100))
        self.scrollbar.setValue(int(t.offset * 100))
        self._syncing = False

    def _on_scroll(self, value: int) -> None:
        if not self._syncing:
            self.timeline.set_offset(value / 100.0)
