from __future__ import annotations

from .base import PlayerBackend


def create_player(prefer: str = "auto"):
    """Trả về (backend, canvas_widget, lý do nếu phải fallback)."""
    reason = ""
    if prefer in ("auto", "mpv"):
        try:
            from .mpv_backend import MpvCanvas, MpvPlayerBackend

            backend = MpvPlayerBackend()
            return backend, MpvCanvas(backend), ""
        except Exception as exc:  # noqa: BLE001 - thiếu DLL, lỗi OpenGL...
            reason = f"Không dùng được libmpv: {exc}"
    from .qt_backend import QtCanvas, QtPlayerBackend

    backend = QtPlayerBackend()
    return backend, QtCanvas(backend), reason


__all__ = ["PlayerBackend", "create_player"]
