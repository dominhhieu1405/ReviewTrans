from __future__ import annotations


def escape_ffmpeg_path(path: str) -> str:
    safe_path = path.replace("\\", "/")
    return safe_path.replace(":", "\\:")
