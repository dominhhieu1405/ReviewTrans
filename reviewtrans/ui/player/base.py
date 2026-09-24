from __future__ import annotations

from PyQt6 import QtCore


class PlayerBackend(QtCore.QObject):
    positionChanged = QtCore.pyqtSignal(float)
    durationChanged = QtCore.pyqtSignal(float)
    playingChanged = QtCore.pyqtSignal(bool)
    frameUpdated = QtCore.pyqtSignal()
    errorOccurred = QtCore.pyqtSignal(str)

    name = "base"

    def load(self, path: str) -> None:
        raise NotImplementedError

    def unload(self) -> None:
        raise NotImplementedError

    def play(self) -> None:
        raise NotImplementedError

    def pause(self) -> None:
        raise NotImplementedError

    def toggle(self) -> None:
        if self.is_playing():
            self.pause()
        else:
            self.play()

    def seek(self, seconds: float) -> None:
        raise NotImplementedError

    def position(self) -> float:
        raise NotImplementedError

    def duration(self) -> float:
        raise NotImplementedError

    def is_playing(self) -> bool:
        raise NotImplementedError

    def set_audio_override(self, path: str | None) -> None:
        """Phát file âm thanh khác (mix lồng tiếng) thay cho âm gốc; None = âm gốc."""
        raise NotImplementedError

    def set_volume(self, volume: int) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass
