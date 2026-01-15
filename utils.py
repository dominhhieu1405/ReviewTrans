from __future__ import annotations

import hashlib


def escape_ffmpeg_path(path: str) -> str:
    safe_path = path.replace("\\", "/")
    return safe_path.replace(":", "\\:")


def generate_cache_key(*args: object) -> str:
    joined = "|".join(str(arg) for arg in args)
    return hashlib.md5(joined.encode("utf-8")).hexdigest()
