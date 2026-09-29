"""Xuất icon cho app/installer từ logo SVG.

    python assets/build_icons.py

Tạo: icon.ico (16–256 px; cỡ ≤48 dùng bản giản lược logo-small.svg), icon.png (256 px),
assets/logo-256.png và assets/logo-512.png (dùng khi đăng sản phẩm, README...).
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6 import QtCore, QtGui, QtSvg, QtWidgets  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SMALL_SIZES = (16, 24, 32, 48)
LARGE_SIZES = (64, 128, 256)


def render(svg: Path, size: int) -> QtGui.QImage:
    renderer = QtSvg.QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise SystemExit(f"SVG lỗi: {svg}")
    image = QtGui.QImage(size, size, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return image


def png_bytes(image: QtGui.QImage) -> bytes:
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def write_ico(target: Path, images: list[QtGui.QImage]) -> None:
    """ICO chứa ảnh PNG cho từng cỡ (Windows Vista trở lên đọc được)."""
    blobs = [png_bytes(img) for img in images]
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries = b""
    for img, blob in zip(images, blobs):
        size = img.width()
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    target.write_bytes(header + entries + b"".join(blobs))


def main() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    logo, small = ASSETS / "logo.svg", ASSETS / "logo-small.svg"
    frames = [render(small, s) for s in SMALL_SIZES] + [render(logo, s) for s in LARGE_SIZES]
    write_ico(ROOT / "icon.ico", frames)
    render(logo, 256).save(str(ROOT / "icon.png"), "PNG")
    for size in (256, 512):
        render(logo, size).save(str(ASSETS / f"logo-{size}.png"), "PNG")
    print("Đã tạo icon.ico, icon.png, assets/logo-256.png, assets/logo-512.png")
    del app


if __name__ == "__main__":
    main()
