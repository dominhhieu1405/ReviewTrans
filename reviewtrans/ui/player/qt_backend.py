"""Backend dự phòng dùng QtMultimedia khi chưa có libmpv."""
from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtMultimedia, QtWidgets

from .base import PlayerBackend
from .canvas import OverlayMixin


class QtPlayerBackend(PlayerBackend):
    name = "Qt Multimedia"

    def __init__(self) -> None:
        super().__init__()
        self.player = QtMultimedia.QMediaPlayer()
        self.audio = QtMultimedia.QAudioOutput()
        self.player.setAudioOutput(self.audio)
        self.sink = QtMultimedia.QVideoSink()
        self.player.setVideoSink(self.sink)
        self.frame: QtGui.QImage | None = None
        self.sink.videoFrameChanged.connect(self._on_frame)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(lambda ms: self.durationChanged.emit(ms / 1000.0))
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.errorOccurred.connect(lambda _e, msg: self.errorOccurred.emit(msg))
        self.ext_player = QtMultimedia.QMediaPlayer()
        self.ext_audio = QtMultimedia.QAudioOutput()
        self.ext_player.setAudioOutput(self.ext_audio)
        self._override: str | None = None
        self._volume = 1.0

    def _on_frame(self, frame: QtMultimedia.QVideoFrame) -> None:
        if frame.isValid():
            self.frame = frame.toImage()
            self.frameUpdated.emit()

    def _on_position(self, ms: int) -> None:
        seconds = ms / 1000.0
        if self._override and self.is_playing():
            drift = abs(self.ext_player.position() / 1000.0 - seconds)
            if drift > 0.25:
                self.ext_player.setPosition(ms)
        self.positionChanged.emit(seconds)

    def _on_state(self, state) -> None:
        self.playingChanged.emit(state == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState)

    def load(self, path: str) -> None:
        self.frame = None
        self.player.setSource(QtCore.QUrl.fromLocalFile(path))
        self.player.pause()  # hiện khung đầu

    def unload(self) -> None:
        self.player.stop()
        self.player.setSource(QtCore.QUrl())
        self.ext_player.stop()
        self.frame = None
        self.frameUpdated.emit()

    def play(self) -> None:
        self.player.play()
        if self._override:
            self.ext_player.setPosition(self.player.position())
            self.ext_player.play()

    def pause(self) -> None:
        self.player.pause()
        self.ext_player.pause()

    def seek(self, seconds: float) -> None:
        ms = int(max(0.0, seconds) * 1000)
        self.player.setPosition(ms)
        if self._override:
            self.ext_player.setPosition(ms)

    def position(self) -> float:
        return self.player.position() / 1000.0

    def duration(self) -> float:
        return self.player.duration() / 1000.0

    def is_playing(self) -> bool:
        return self.player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState

    def set_audio_override(self, path: str | None) -> None:
        self._override = path
        if path:
            self.ext_player.setSource(QtCore.QUrl())
            self.ext_player.setSource(QtCore.QUrl.fromLocalFile(path))
            self.ext_player.setPosition(self.player.position())
            self.audio.setMuted(True)
            if self.is_playing():
                self.ext_player.play()
        else:
            self.ext_player.stop()
            self.audio.setMuted(False)

    def set_volume(self, volume: int) -> None:
        self._volume = max(0.0, min(1.0, volume / 100.0))
        self.audio.setVolume(self._volume)
        self.ext_audio.setVolume(self._volume)

    def close(self) -> None:
        self.player.stop()
        self.ext_player.stop()


class QtCanvas(OverlayMixin, QtWidgets.QWidget):
    def __init__(self, backend: QtPlayerBackend, parent=None) -> None:
        super().__init__(parent)
        self.backend = backend
        self.init_overlay()
        backend.frameUpdated.connect(self.update)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#000000"))
        vr = self.video_rect()
        frame = self.backend.frame
        if frame is not None and not frame.isNull():
            if self.doc and self.doc.flip_horizontal:
                frame = frame.mirrored(True, False)
            painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(vr, frame)
        else:
            painter.fillRect(vr, QtGui.QColor("#101114"))
        self.paint_overlays(painter, frame)
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.overlay_mouse_press(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.overlay_mouse_move(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self.overlay_mouse_release(event)
