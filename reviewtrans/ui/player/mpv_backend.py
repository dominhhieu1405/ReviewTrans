"""Player libmpv: render phần mềm (mpv render API "sw") vào QImage rồi vẽ overlay bằng QPainter.

Không dùng OpenGL: một số driver (ví dụ AMD đời cũ) làm crash mọi QOpenGLWidget.
"""
from __future__ import annotations

import ctypes
import locale

from PyQt6 import QtCore, QtGui, QtWidgets

from ...core.paths import register_dll_dirs
from .base import PlayerBackend
from .canvas import OverlayMixin

register_dll_dirs()
import mpv  # noqa: E402  (cần PATH có libmpv trước khi import)


class _SwSize(ctypes.Structure):
    _fields_ = [("w", ctypes.c_int), ("h", ctypes.c_int)]


class _SwStride(ctypes.Structure):
    _fields_ = [("stride", ctypes.c_size_t)]


# python-mpv chưa khai báo các tham số render phần mềm (libmpv/render.h)
mpv.MpvRenderParam.TYPES.setdefault("sw_size", (17, _SwSize))
mpv.MpvRenderParam.TYPES.setdefault("sw_format", (18, str))
mpv.MpvRenderParam.TYPES.setdefault("sw_stride", (19, _SwStride))
mpv.MpvRenderParam.TYPES.setdefault("sw_pointer", (20, ctypes.c_void_p))


class MpvPlayerBackend(PlayerBackend):
    name = "libmpv"

    def __init__(self) -> None:
        super().__init__()
        try:
            locale.setlocale(locale.LC_NUMERIC, "C")
        except locale.Error:
            pass
        self.mpv = mpv.MPV(
            vo="libmpv",
            keep_open="yes",
            idle="yes",
            hr_seek="yes",
            osc=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
            hwdec="auto-copy-safe",
            load_scripts=False,
            ytdl=False,
            loglevel="warn",
        )
        self.ctx: mpv.MpvRenderContext | None = None
        self._position = 0.0
        self._duration = 0.0
        self._paused = True
        self._loaded = False
        self._pending_override: str | None = None
        self._override: str | None = None

        @self.mpv.property_observer("time-pos")
        def _time(_name, value):
            if value is not None:
                self._position = float(value)
                self.positionChanged.emit(self._position)

        @self.mpv.property_observer("duration")
        def _duration(_name, value):
            if value is not None:
                self._duration = float(value)
                self.durationChanged.emit(self._duration)

        @self.mpv.property_observer("pause")
        def _pause(_name, value):
            self._paused = bool(value)
            self.playingChanged.emit(not self._paused)

        @self.mpv.event_callback("file-loaded")
        def _loaded(_event):
            self._loaded = True
            QtCore.QMetaObject.invokeMethod(self, "_after_load", QtCore.Qt.ConnectionType.QueuedConnection)

    @QtCore.pyqtSlot()
    def _after_load(self) -> None:
        if self._pending_override is not None or self._override:
            self._apply_override(self._pending_override if self._pending_override is not None else self._override)

    # ------------------------------------------------------------ render

    def create_render_context(self) -> None:
        self.ctx = mpv.MpvRenderContext(self.mpv, "sw")
        self.ctx.update_cb = lambda: self.frameUpdated.emit()

    def render_into(self, image: QtGui.QImage) -> None:
        """Vẽ khung hình hiện tại (đã letterbox) vào QImage định dạng RGBX8888."""
        if self.ctx is None:
            return
        self.ctx.render(
            sw_size={"w": image.width(), "h": image.height()},
            sw_format="rgb0",
            sw_stride={"stride": image.bytesPerLine()},
            sw_pointer=int(image.bits()),
            block_for_target_time=False,  # mặc định mpv chờ tới giờ hiển thị khung → chặn luồng giao diện
        )

    def frame_ready(self) -> bool:
        return self.ctx.update() if self.ctx is not None else False

    # ------------------------------------------------------------ điều khiển

    def load(self, path: str) -> None:
        self._loaded = False
        self.mpv.pause = True
        self.mpv.play(path)

    def unload(self) -> None:
        self._loaded = False
        self.mpv.command("stop")

    def play(self) -> None:
        self.mpv.pause = False

    def pause(self) -> None:
        self.mpv.pause = True

    def seek(self, seconds: float) -> None:
        if not self._loaded:
            return
        try:
            self.mpv.seek(max(0.0, seconds), reference="absolute", precision="exact")
        except SystemError:
            pass

    def position(self) -> float:
        return self._position

    def duration(self) -> float:
        return self._duration

    def is_playing(self) -> bool:
        return not self._paused

    def set_audio_override(self, path: str | None) -> None:
        if not self._loaded:
            self._pending_override = path
            self._override = path
            return
        self._apply_override(path)

    def _apply_override(self, path: str | None) -> None:
        self._pending_override = None
        self._override = path
        try:
            for track in list(self.mpv.track_list):
                if track.get("type") == "audio" and track.get("external"):
                    self.mpv.command("audio-remove", track["id"])
            if path:
                self.mpv.command("audio-add", path, "select", "Lồng tiếng")
            else:
                self.mpv.aid = "auto"
        except Exception as exc:  # noqa: BLE001
            self.errorOccurred.emit(f"Không nạp được âm thanh xem trước: {exc}")

    def set_volume(self, volume: int) -> None:
        self.mpv.volume = max(0, min(100, volume))

    def close(self) -> None:
        try:
            if self.ctx is not None:
                self.ctx.free()
                self.ctx = None
            self.mpv.terminate()
        except Exception:  # noqa: BLE001
            pass


class MpvCanvas(OverlayMixin, QtWidgets.QWidget):
    def __init__(self, backend: MpvPlayerBackend, parent=None) -> None:
        super().__init__(parent)
        self.backend = backend
        self.init_overlay()
        self._image: QtGui.QImage | None = None
        self._stale = True
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_OpaquePaintEvent)
        backend.create_render_context()
        backend.frameUpdated.connect(self._on_frame, QtCore.Qt.ConnectionType.QueuedConnection)

    def _on_frame(self) -> None:
        self._stale = True
        self.update()

    def _target_image(self) -> QtGui.QImage:
        ratio = self.devicePixelRatioF()
        width = max(16, int(self.width() * ratio))
        height = max(16, int(self.height() * ratio))
        if self._image is None or self._image.width() != width or self._image.height() != height:
            self._image = QtGui.QImage(width, height, QtGui.QImage.Format.Format_RGBX8888)
            self._image.fill(QtGui.QColor("#000000"))
            self._stale = True
        return self._image

    def paintEvent(self, _event) -> None:  # noqa: N802
        image = self._target_image()
        if self.backend.frame_ready() or self._stale:
            self._stale = False
            self.backend.render_into(image)
        if self.doc and self.doc.flip_horizontal:
            image = image.mirrored(True, False)  # khung letterbox đối xứng nên lật cả ảnh = lật video
        painter = QtGui.QPainter(self)
        painter.drawImage(QtCore.QRectF(self.rect()), image)
        self.paint_overlays(painter, image, QtCore.QRectF(self.rect()))
        painter.end()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._stale = True

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.overlay_mouse_press(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.overlay_mouse_move(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self.overlay_mouse_release(event)
