"""Vẽ chữ bằng QtGui — dùng chung cho preview trong editor và PNG của layer chữ khi xuất.

Kích thước font tuân theo quy ước của libass (Fontsize = chiều cao dòng) để phụ đề
preview khớp với phụ đề ASS được ffmpeg render.
"""
from __future__ import annotations

import re
from pathlib import Path

from PyQt6 import QtCore, QtGui

from .geometry import layer_rect, subtitle_text
from .models import Layer, SubtitleStyle

_ratio_cache: dict[tuple[str, bool, bool], float] = {}
_CJK = re.compile(r"[　-鿿가-힯＀-￯]")


def qcolor(hex_rgb: str, opacity: float = 1.0) -> QtGui.QColor:
    color = QtGui.QColor(hex_rgb or "#000000")
    if not color.isValid():
        color = QtGui.QColor("#000000")
    color.setAlphaF(max(0.0, min(1.0, opacity)))
    return color


def ass_font(family: str, size: float, bold: bool, italic: bool) -> QtGui.QFont:
    key = (family, bold, italic)
    if key not in _ratio_cache:
        probe = QtGui.QFont(family)
        probe.setPixelSize(200)
        probe.setBold(bold)
        probe.setItalic(italic)
        metrics = QtGui.QFontMetricsF(probe)
        height = metrics.ascent() + metrics.descent()
        _ratio_cache[key] = 200.0 / height if height > 0 else 0.8
    font = QtGui.QFont(family)
    font.setPixelSize(max(1, int(round(size * _ratio_cache[key]))))
    font.setBold(bold)
    font.setItalic(italic)
    font.setStyleStrategy(QtGui.QFont.StyleStrategy.PreferAntialias)
    return font


def wrap_to_width(text: str, font: QtGui.QFont, max_width: float) -> list[str]:
    metrics = QtGui.QFontMetricsF(font)
    result: list[str] = []
    for paragraph in text.split("\n"):
        if metrics.horizontalAdvance(paragraph) <= max_width or max_width <= 0:
            result.append(paragraph)
            continue
        tokens = list(paragraph) if (_CJK.search(paragraph) and " " not in paragraph) else paragraph.split(" ")
        joiner = "" if tokens and len(tokens) == len(paragraph) else " "
        line = ""
        for token in tokens:
            candidate = (line + joiner + token) if line else token
            if metrics.horizontalAdvance(candidate) > max_width and line:
                result.append(line)
                line = token
            else:
                line = candidate
        if line:
            result.append(line)
    return result


def _draw_lines(
    painter: QtGui.QPainter,
    lines: list[str],
    font: QtGui.QFont,
    area: QtCore.QRectF,
    h_align: str,
    v_align: str,
    fill: QtGui.QColor,
    outline: QtGui.QColor,
    outline_width: float,
    shadow_depth: float = 0.0,
    shadow_color: QtGui.QColor | None = None,
    box_color: QtGui.QColor | None = None,
    box_padding: float = 0.0,
) -> None:
    metrics = QtGui.QFontMetricsF(font)
    line_height = metrics.ascent() + metrics.descent()
    block = line_height * len(lines)
    if v_align == "top":
        y = area.top()
    elif v_align == "bottom":
        y = area.bottom() - block
    else:
        y = area.center().y() - block / 2
    path = QtGui.QPainterPath()
    boxes: list[QtCore.QRectF] = []
    for line in lines:
        width = metrics.horizontalAdvance(line)
        if h_align == "left":
            x = area.left()
        elif h_align == "right":
            x = area.right() - width
        else:
            x = area.center().x() - width / 2
        path.addText(QtCore.QPointF(x, y + metrics.ascent()), font, line)
        boxes.append(QtCore.QRectF(x - box_padding, y - box_padding, width + 2 * box_padding, line_height + 2 * box_padding))
        y += line_height

    painter.save()
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    if box_color is not None:
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(box_color)
        for rect in boxes:
            painter.drawRect(rect)
    # Không dùng QPainterPath.united (rất chậm): vẽ viền trước, chữ tô đè lên che nửa trong của viền.
    stroke = None
    if outline_width > 0:
        stroker = QtGui.QPainterPathStroker()
        stroker.setWidth(outline_width * 2)
        stroker.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        stroker.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        stroke = stroker.createStroke(path)
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    if shadow_depth > 0 and shadow_color is not None and shadow_color.alpha() > 0:
        # gộp chữ + viền thành một path (addPath rẻ, khác united) và tô một lần với WindingFill
        # để chỗ chồng nhau không bị đậm gấp đôi
        shadow = QtGui.QPainterPath(path)
        if stroke is not None:
            shadow.addPath(stroke)
        shadow.setFillRule(QtCore.Qt.FillRule.WindingFill)
        painter.setBrush(shadow_color)
        painter.drawPath(shadow.translated(shadow_depth, shadow_depth))
    if stroke is not None:
        painter.setBrush(outline)
        painter.drawPath(stroke)
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawPath(path)
    painter.restore()


def paint_subtitle(painter: QtGui.QPainter, video_rect: QtCore.QRectF, text: str, style: SubtitleStyle) -> None:
    """Vẽ một câu phụ đề lên khung video đang hiển thị (video_rect theo pixel màn hình)."""
    text = subtitle_text(text, style)
    if not text:
        return
    s = video_rect.height() / 1080.0
    font = ass_font(style.font_family, style.font_size * s, style.bold, style.italic)
    margin_h = style.margin_h / 100.0 * video_rect.width()
    margin_v = style.margin_v / 100.0 * video_rect.height()
    area = QtCore.QRectF(
        video_rect.left() + margin_h,
        video_rect.top() + margin_v,
        max(10.0, video_rect.width() - 2 * margin_h),
        max(10.0, video_rect.height() - 2 * margin_v),
    )
    lines = wrap_to_width(text, font, area.width())
    v_align = {"top": "top", "middle": "center"}.get(style.position, "bottom")
    _draw_lines(
        painter, lines, font, area, "center", v_align,
        fill=qcolor(style.text_color),
        outline=qcolor(style.outline_color),
        outline_width=style.outline_width * s,
        shadow_depth=style.shadow_depth * s,
        shadow_color=qcolor(style.shadow_color, style.shadow_opacity / 100.0),
        box_color=qcolor(style.bg_color, style.bg_opacity / 100.0) if style.bg_enabled else None,
        box_padding=style.bg_padding * s,
    )


def paint_text_layer(painter: QtGui.QPainter, rect: QtCore.QRectF, layer: Layer, scale: float) -> None:
    """Vẽ layer chữ vào rect (đơn vị pixel đích), scale = chiều cao đích / 1080."""
    painter.save()
    painter.setOpacity(max(0.0, min(1.0, layer.opacity)))
    if layer.bg_enabled:
        painter.fillRect(rect, qcolor(layer.bg_color, layer.bg_opacity / 100.0))
    font = ass_font(layer.font_family, layer.font_size * scale, layer.bold, layer.italic)
    pad = 6 * scale
    area = rect.adjusted(pad, pad, -pad, -pad)
    lines = wrap_to_width(layer.text, font, area.width())
    _draw_lines(
        painter, lines, font, area, layer.align, "center",
        fill=qcolor(layer.text_color),
        outline=qcolor(layer.outline_color),
        outline_width=layer.outline_width * scale,
    )
    painter.restore()


def render_text_layer_png(layer: Layer, width: int, height: int, target: Path) -> tuple[int, int, int, int]:
    """Raster layer chữ ra PNG đúng kích thước pixel trong video. Trả về rect (x, y, w, h)."""
    x, y, w, h = layer_rect(layer, width, height)
    image = QtGui.QImage(w, h, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    paint_text_layer(painter, QtCore.QRectF(0, 0, w, h), layer, height / 1080.0)
    painter.end()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(target), "PNG"):
        raise RuntimeError(f"Không lưu được {target}")
    return x, y, w, h


def image_size(path: str) -> tuple[int, int] | None:
    reader = QtGui.QImageReader(path)
    size = reader.size()
    if size.isValid():
        return size.width(), size.height()
    return None
