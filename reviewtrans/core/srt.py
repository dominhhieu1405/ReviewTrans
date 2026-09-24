from __future__ import annotations

import re
from typing import Iterable

_TIME_RE = re.compile(
    r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})"
)


def parse_srt(content: str) -> list[tuple[float, float, str]]:
    """Trả về danh sách (start, end, text). Chịu được file thiếu số thứ tự, CRLF, BOM."""
    content = content.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    items: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", content):
        lines = [line for line in block.split("\n") if line.strip() != ""]
        for idx, line in enumerate(lines):
            match = _TIME_RE.search(line)
            if not match:
                continue
            g = match.groups()
            start = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3].ljust(3, "0")) / 1000
            end = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7].ljust(3, "0")) / 1000
            text = " ".join(part.strip() for part in lines[idx + 1:]).strip()
            items.append((start, end, text))
            break
    return items


def format_timestamp(seconds: float, sep: str = ",") -> str:
    seconds = max(0.0, seconds)
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{millis:03d}"


def format_clock(seconds: float) -> str:
    """Hiển thị gọn cho UI: MM:SS.s hoặc H:MM:SS.s"""
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if hours:
        return f"{hours}:{minutes:02d}:{secs:04.1f}"
    return f"{minutes:02d}:{secs:04.1f}"


def render_srt(items: Iterable[tuple[float, float, str]]) -> str:
    blocks = []
    for index, (start, end, text) in enumerate(items, start=1):
        blocks.append(f"{index}\n{format_timestamp(start)} --> {format_timestamp(end)}\n{text}")
    return "\n\n".join(blocks) + "\n"
