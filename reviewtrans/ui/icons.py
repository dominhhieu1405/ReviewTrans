"""Icon vẽ từ font Segoe Fluent Icons / Segoe MDL2 Assets (có sẵn trên Windows 10/11)."""
from __future__ import annotations

from functools import lru_cache

from PyQt6 import QtCore, QtGui

from .theme import TEXT

GLYPHS = {
    "projects": "",
    "editor": "",
    "queue": "",
    "providers": "",
    "presets": "",
    "resources": "",
    "tools": "",
    "settings": "",
    "play": "",
    "pause": "",
    "stop": "",
    "prev": "",
    "next": "",
    "add": "",
    "delete": "",
    "refresh": "",
    "save": "",
    "folder": "",
    "open": "",
    "eye": "",
    "eye_off": "",
    "volume": "",
    "mute": "",
    "image": "",
    "text": "",
    "blur": "",
    "up": "",
    "down": "",
    "export": "",
    "mic": "",
    "translate": "",
    "speaker": "",
    "mix": "",
    "check": "",
    "warning": "",
    "error": "",
    "lock": "",
    "unlock": "",
    "copy": "",
    "context": "",
    "download": "",
    "zoom_in": "",
    "zoom_out": "",
    "split": "",
    "merge": "",
    "run": "",
    "cut": "",
}

FALLBACK = {
    "play": "▶", "pause": "⏸", "stop": "■", "add": "+", "delete": "✕", "up": "▲", "down": "▼",
    "eye": "◉", "eye_off": "○", "volume": "♪", "mute": "×", "prev": "◀", "next": "▶",
}


@lru_cache(maxsize=1)
def _icon_family() -> str | None:
    families = set(QtGui.QFontDatabase.families())
    for name in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        if name in families:
            return name
    return None


@lru_cache(maxsize=256)
def icon(name: str, color: str = TEXT, size: int = 64) -> QtGui.QIcon:
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
    painter.setPen(QtGui.QColor(color))
    family = _icon_family()
    if family and name in GLYPHS:
        font = QtGui.QFont(family)
        font.setPixelSize(int(size * 0.72))
        text = GLYPHS[name]
    else:
        font = QtGui.QFont()
        font.setPixelSize(int(size * 0.6))
        text = FALLBACK.get(name, name[:1].upper())
    painter.setFont(font)
    painter.drawText(QtCore.QRectF(0, 0, size, size), QtCore.Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return QtGui.QIcon(pixmap)
