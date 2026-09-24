from __future__ import annotations

import re

from .models import LAYER_IMAGE, Layer, SubtitleStyle

REFERENCE_HEIGHT = 1080.0


def scale_factor(video_height: int) -> float:
    return (video_height or REFERENCE_HEIGHT) / REFERENCE_HEIGHT


def layer_rect(
    layer: Layer, width: int, height: int, image_size: tuple[int, int] | None = None
) -> tuple[int, int, int, int]:
    """Hình chữ nhật của layer theo pixel video (x, y, w, h), đã kẹp trong khung."""
    x = int(round(layer.x * width))
    y = int(round(layer.y * height))
    w = max(2, int(round(layer.w * width)))
    h = max(2, int(round(layer.h * height)))
    if layer.type == LAYER_IMAGE and layer.keep_aspect and image_size and image_size[0] > 0:
        h = max(2, int(round(w * image_size[1] / image_size[0])))
    x = max(0, min(width - 2, x))
    y = max(0, min(height - 2, y))
    w = min(w, width - x)
    h = min(h, height - y)
    # yuv420 cần kích thước chẵn khi crop
    return x, y, w - (w % 2), h - (h % 2)


_CJK = re.compile(r"[　-鿿가-힯＀-￯]")


def wrap_text(text: str, max_chars: int) -> str:
    """Xuống dòng thủ công theo số ký tự (hỗ trợ cả tiếng Trung không có dấu cách)."""
    text = " ".join(text.split())
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if " " not in text or _CJK.search(text):
        return "\n".join(text[i:i + max_chars] for i in range(0, len(text), max_chars))
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    # tránh dòng cuối quá ngắn: cân bằng 2 dòng
    if len(lines) == 2 and len(lines[1]) < max_chars // 3:
        words = text.split(" ")
        best = min(
            range(1, len(words)),
            key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))),
        )
        lines = [" ".join(words[:best]), " ".join(words[best:])]
    return "\n".join(lines)


def subtitle_text(text: str, style: SubtitleStyle) -> str:
    text = text.strip()
    if style.uppercase:
        text = text.upper()
    return wrap_text(text, style.max_chars_per_line)
