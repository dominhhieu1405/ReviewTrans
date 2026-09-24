"""Phần vẽ overlay + kéo thả layer dùng chung cho canvas libmpv và canvas Qt."""
from __future__ import annotations

import bisect

from PyQt6 import QtCore, QtGui

from ...core.geometry import layer_rect
from ...core.models import LAYER_BLUR, LAYER_IMAGE, LAYER_TEXT, Layer, Segment, VideoDoc
from ...core.raster import image_size, paint_subtitle, paint_text_layer, qcolor
from ..theme import ACCENT

HANDLE = 6
# thứ tự: TL, T, TR, R, BR, B, BL, L
_HANDLE_CURSORS = [
    QtCore.Qt.CursorShape.SizeFDiagCursor, QtCore.Qt.CursorShape.SizeVerCursor,
    QtCore.Qt.CursorShape.SizeBDiagCursor, QtCore.Qt.CursorShape.SizeHorCursor,
    QtCore.Qt.CursorShape.SizeFDiagCursor, QtCore.Qt.CursorShape.SizeVerCursor,
    QtCore.Qt.CursorShape.SizeBDiagCursor, QtCore.Qt.CursorShape.SizeHorCursor,
]


class CanvasEvents(QtCore.QObject):
    layerSelected = QtCore.pyqtSignal(str)
    layerEdited = QtCore.pyqtSignal(str)  # kéo/đổi kích thước xong
    layerMoving = QtCore.pyqtSignal(str)  # đang kéo
    clicked = QtCore.pyqtSignal()


class OverlayMixin:
    """Mixin cho QWidget/QOpenGLWidget: cần gọi init_overlay() trong __init__."""

    def init_overlay(self) -> None:
        self.events = CanvasEvents()
        self.doc: VideoDoc | None = None
        self.segments: list[Segment] = []
        self._starts: list[float] = []
        self.time = 0.0
        self.selected = ""
        self.show_source = False
        self._drag: dict | None = None
        self._pixmaps: dict[str, QtGui.QPixmap] = {}
        self._sizes: dict[str, tuple[int, int] | None] = {}
        self._overlay_cache: dict[tuple, QtGui.QImage] = {}
        self.setMouseTracking(True)
        self.setMinimumSize(320, 180)

    # ------------------------------------------------------------ dữ liệu

    def set_document(self, doc: VideoDoc | None, segments: list[Segment]) -> None:
        self.doc = doc
        self.set_segments(segments)

    def set_segments(self, segments: list[Segment]) -> None:
        self.segments = sorted(segments, key=lambda s: s.start)
        self._starts = [s.start for s in self.segments]
        self.update()

    def set_time(self, seconds: float) -> None:
        self.time = seconds
        self.update()

    def select_layer(self, layer_id: str) -> None:
        self.selected = layer_id
        self.update()

    def current_segment(self) -> Segment | None:
        index = bisect.bisect_right(self._starts, self.time) - 1
        while index >= 0:
            seg = self.segments[index]
            if seg.start <= self.time <= seg.end:
                return seg
            if self.time - seg.end > 30:
                break
            index -= 1
        return None

    def invalidate_images(self) -> None:
        self._pixmaps.clear()
        self._sizes.clear()
        self._overlay_cache.clear()

    # ------------------------------------------------------------ hình học

    def video_size(self) -> tuple[int, int]:
        if self.doc and self.doc.width and self.doc.height:
            return self.doc.width, self.doc.height
        return 1920, 1080

    def video_rect(self) -> QtCore.QRectF:
        vw, vh = self.video_size()
        w, h = self.width(), self.height()
        scale = min(w / vw, h / vh)
        rw, rh = vw * scale, vh * scale
        return QtCore.QRectF((w - rw) / 2, (h - rh) / 2, rw, rh)

    def _image_size(self, path: str) -> tuple[int, int] | None:
        if path not in self._sizes:
            self._sizes[path] = image_size(path)
        return self._sizes[path]

    def layer_widget_rect(self, layer: Layer) -> QtCore.QRectF:
        vr = self.video_rect()
        vw, vh = self.video_size()
        size = self._image_size(layer.image_path) if layer.type == LAYER_IMAGE and layer.image_path else None
        x, y, w, h = layer_rect(layer, vw, vh, size)
        sx, sy = vr.width() / vw, vr.height() / vh
        return QtCore.QRectF(vr.left() + x * sx, vr.top() + y * sy, w * sx, h * sy)

    def _active_layers(self) -> list[Layer]:
        if not self.doc or not self.doc.tracks.layers:
            return []
        duration = self.doc.duration or 1e9
        return [layer for layer in self.doc.layers if layer.active_at(self.time, duration)]

    def _find_layer(self, layer_id: str) -> Layer | None:
        if not self.doc:
            return None
        return next((layer for layer in self.doc.layers if layer.id == layer_id), None)

    # ------------------------------------------------------------ vẽ

    def paint_overlays(
        self,
        painter: QtGui.QPainter,
        frame: QtGui.QImage | None = None,
        frame_rect: QtCore.QRectF | None = None,
    ) -> None:
        """frame = ảnh khung hình đang hiển thị, frame_rect = vùng widget mà ảnh đó phủ (mặc định = khung video)."""
        if not self.doc:
            return
        vr = self.video_rect()
        frame_rect = frame_rect or vr
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        for layer in self._active_layers():
            rect = self.layer_widget_rect(layer)
            if layer.type == LAYER_BLUR:
                self._paint_blur(painter, rect, layer, frame, frame_rect, vr)
            elif layer.type == LAYER_IMAGE:
                pixmap = self._pixmap(layer.image_path)
                if pixmap is not None:
                    painter.save()
                    painter.setOpacity(layer.opacity)
                    painter.drawPixmap(rect, pixmap, QtCore.QRectF(pixmap.rect()))
                    painter.restore()
                else:
                    self._placeholder(painter, rect, "Ảnh")
            elif layer.type == LAYER_TEXT:
                key = (
                    "text", layer.text, layer.font_family, layer.font_size, layer.bold, layer.italic,
                    layer.text_color, layer.outline_color, layer.outline_width, layer.bg_enabled,
                    layer.bg_color, layer.bg_opacity, layer.align, layer.opacity,
                    round(rect.width()), round(rect.height()), round(vr.height()),
                )
                size = QtCore.QSizeF(rect.width(), rect.height())
                image = self._cached_overlay(
                    key, size,
                    lambda p, l=layer, sz=size: paint_text_layer(p, QtCore.QRectF(QtCore.QPointF(0, 0), sz), l, vr.height() / 1080.0),
                )
                painter.drawImage(rect.topLeft(), image)
        if self.doc.tracks.subtitles:
            seg = self.current_segment()
            if seg is not None:
                text = seg.source if self.show_source else seg.display_text()
                style = self.doc.style
                key = ("sub", text, repr(style), round(vr.width()), round(vr.height()))
                image = self._cached_overlay(
                    key, vr.size(),
                    lambda p: paint_subtitle(p, QtCore.QRectF(QtCore.QPointF(0, 0), vr.size()), text, style),
                )
                painter.drawImage(vr.topLeft(), image)
        self._paint_selection(painter)

    def _cached_overlay(self, key: tuple, size: QtCore.QSizeF, draw) -> QtGui.QImage:
        """Vẽ chữ (phụ đề/layer chữ) một lần vào ảnh trong suốt rồi dùng lại — vẽ path chữ khá nặng."""
        ratio = self.devicePixelRatioF()
        key = key + (ratio,)
        image = self._overlay_cache.get(key)
        if image is None:
            image = QtGui.QImage(
                max(1, int(size.width() * ratio)), max(1, int(size.height() * ratio)),
                QtGui.QImage.Format.Format_ARGB32_Premultiplied,
            )
            image.setDevicePixelRatio(ratio)
            image.fill(QtCore.Qt.GlobalColor.transparent)
            painter = QtGui.QPainter(image)
            draw(painter)
            painter.end()
            if len(self._overlay_cache) > 64:
                self._overlay_cache.clear()
            self._overlay_cache[key] = image
        return image

    def _pixmap(self, path: str) -> QtGui.QPixmap | None:
        if not path:
            return None
        if path not in self._pixmaps:
            pixmap = QtGui.QPixmap(path)
            self._pixmaps[path] = pixmap if not pixmap.isNull() else None
        return self._pixmaps[path]

    def _placeholder(self, painter: QtGui.QPainter, rect: QtCore.QRectF, label: str) -> None:
        painter.save()
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 160), 1, QtCore.Qt.PenStyle.DashLine))
        painter.setBrush(QtGui.QColor(255, 255, 255, 30))
        painter.drawRect(rect)
        painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()

    def _paint_blur(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRectF,
        layer: Layer,
        frame: QtGui.QImage | None,
        frame_rect: QtCore.QRectF,
        vr: QtCore.QRectF,
    ) -> None:
        if layer.blur_mode == "fill":
            painter.fillRect(rect, qcolor(layer.fill_color, layer.opacity))
            return
        if frame is not None and not frame.isNull() and frame_rect.width() > 0 and frame_rect.height() > 0:
            fx = frame.width() / frame_rect.width()
            fy = frame.height() / frame_rect.height()
            src = QtCore.QRect(
                int((rect.left() - frame_rect.left()) * fx), int((rect.top() - frame_rect.top()) * fy),
                max(1, int(rect.width() * fx)), max(1, int(rect.height() * fy)),
            ).intersected(frame.rect())
            if src.width() > 0 and src.height() > 0:
                piece = frame.copy(src)
                # quy đổi cường độ (tính theo pixel video như khi xuất) sang pixel của ảnh preview
                _vw, vh = self.video_size()
                image_px_per_video_px = vr.height() * fy / max(1, vh)
                strength = max(1, int(layer.blur_strength))
                if layer.blur_mode == "pixelate":
                    factor = max(1.0, strength * image_px_per_video_px)
                    mode = QtCore.Qt.TransformationMode.FastTransformation
                else:
                    sigma = strength * vh / 1080.0 * image_px_per_video_px
                    factor = max(1.5, sigma * 1.5)
                    mode = QtCore.Qt.TransformationMode.SmoothTransformation
                small = piece.scaled(
                    max(1, round(piece.width() / factor)), max(1, round(piece.height() / factor)),
                    QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                    QtCore.Qt.TransformationMode.SmoothTransformation,
                )
                big = small.scaled(piece.width(), piece.height(), QtCore.Qt.AspectRatioMode.IgnoreAspectRatio, mode)
                target = QtCore.QRectF(
                    frame_rect.left() + src.x() / fx, frame_rect.top() + src.y() / fy, src.width() / fx, src.height() / fy
                )
                painter.drawImage(target, big)
                return
        painter.save()
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(200, 210, 225, 150))
        painter.drawRect(rect)
        painter.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255, 60), QtCore.Qt.BrushStyle.BDiagPattern))
        painter.drawRect(rect)
        painter.restore()

    def _handle_rects(self, rect: QtCore.QRectF) -> list[QtCore.QRectF]:
        points = [
            rect.topLeft(), QtCore.QPointF(rect.center().x(), rect.top()), rect.topRight(),
            QtCore.QPointF(rect.right(), rect.center().y()), rect.bottomRight(),
            QtCore.QPointF(rect.center().x(), rect.bottom()), rect.bottomLeft(),
            QtCore.QPointF(rect.left(), rect.center().y()),
        ]
        return [QtCore.QRectF(p.x() - HANDLE, p.y() - HANDLE, HANDLE * 2, HANDLE * 2) for p in points]

    def _paint_selection(self, painter: QtGui.QPainter) -> None:
        layer = self._find_layer(self.selected)
        if layer is None or not layer.enabled:
            return
        rect = self.layer_widget_rect(layer)
        painter.save()
        pen = QtGui.QPen(QtGui.QColor(ACCENT), 1.5, QtCore.Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        painter.setPen(QtGui.QPen(QtGui.QColor(ACCENT), 1))
        painter.setBrush(QtGui.QColor("#ffffff"))
        for handle in self._handle_rects(rect):
            painter.drawRect(handle)
        painter.restore()

    # ------------------------------------------------------------ chuột

    def overlay_mouse_press(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton or not self.doc:
            return
        pos = event.position()
        layer = self._find_layer(self.selected)
        if layer is not None and layer.active_at(self.time, self.doc.duration or 1e9):
            rect = self.layer_widget_rect(layer)
            for index, handle in enumerate(self._handle_rects(rect)):
                if handle.contains(pos):
                    self._start_drag(layer, pos, index)
                    return
        for candidate in reversed(self._active_layers()):
            if self.layer_widget_rect(candidate).contains(pos):
                if candidate.id != self.selected:
                    self.selected = candidate.id
                    self.events.layerSelected.emit(candidate.id)
                self._start_drag(candidate, pos, -1)
                self.update()
                return
        if self.selected:
            self.selected = ""
            self.events.layerSelected.emit("")
            self.update()
        self.events.clicked.emit()

    def _start_drag(self, layer: Layer, pos: QtCore.QPointF, handle: int) -> None:
        self._drag = {
            "id": layer.id, "handle": handle, "start": QtCore.QPointF(pos),
            "orig": (layer.x, layer.y, layer.w, layer.h), "moved": False,
        }

    def overlay_mouse_move(self, event: QtGui.QMouseEvent) -> None:
        pos = event.position()
        if not self._drag:
            self._update_cursor(pos)
            return
        layer = self._find_layer(self._drag["id"])
        if layer is None:
            return
        vr = self.video_rect()
        dx = (pos.x() - self._drag["start"].x()) / max(1.0, vr.width())
        dy = (pos.y() - self._drag["start"].y()) / max(1.0, vr.height())
        x, y, w, h = self._drag["orig"]
        handle = self._drag["handle"]
        min_size = 0.01
        if handle == -1:
            layer.x = min(max(0.0, x + dx), max(0.0, 1.0 - w))
            layer.y = min(max(0.0, y + dy), max(0.0, 1.0 - h))
        else:
            left, top, right, bottom = x, y, x + w, y + h
            if handle in (0, 6, 7):
                left = min(max(0.0, left + dx), right - min_size)
            if handle in (2, 3, 4):
                right = max(min(1.0, right + dx), left + min_size)
            if handle in (0, 1, 2):
                top = min(max(0.0, top + dy), bottom - min_size)
            if handle in (4, 5, 6):
                bottom = max(min(1.0, bottom + dy), top + min_size)
            layer.x, layer.y, layer.w, layer.h = left, top, right - left, bottom - top
        self._drag["moved"] = True
        self.events.layerMoving.emit(layer.id)
        self.update()

    def overlay_mouse_release(self, _event: QtGui.QMouseEvent) -> None:
        if self._drag and self._drag["moved"]:
            self.events.layerEdited.emit(self._drag["id"])
        self._drag = None
        self.update()

    def _update_cursor(self, pos: QtCore.QPointF) -> None:
        layer = self._find_layer(self.selected)
        if layer is not None and self.doc and layer.active_at(self.time, self.doc.duration or 1e9):
            rect = self.layer_widget_rect(layer)
            for index, handle in enumerate(self._handle_rects(rect)):
                if handle.contains(pos):
                    self.setCursor(_HANDLE_CURSORS[index])
                    return
            if rect.contains(pos):
                self.setCursor(QtCore.Qt.CursorShape.SizeAllCursor)
                return
        self.unsetCursor()
