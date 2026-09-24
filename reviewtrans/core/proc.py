from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .paths import IS_WINDOWS, find_tool

LogFn = Callable[[str], None]


class StopRequested(Exception):
    """Người dùng bấm dừng."""


class ToolMissing(FileNotFoundError):
    pass


def subprocess_kwargs() -> dict:
    if IS_WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def require_tool(name: str) -> Path:
    path = find_tool(name)
    if not path:
        raise ToolMissing(f"Không tìm thấy {name}. Vào trang Tài nguyên để tải về.")
    return path


def _terminate_on_stop(process: subprocess.Popen, stop_event: threading.Event) -> None:
    while process.poll() is None:
        if stop_event.wait(0.2):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
            return


def run(
    command: list[str],
    log: LogFn | None = None,
    stop_event: threading.Event | None = None,
    cwd: str | Path | None = None,
    line_cb: Callable[[str], None] | None = None,
    quiet: bool = False,
) -> int:
    """Chạy lệnh, stream output ra log. Trả về return code, raise StopRequested khi bị dừng."""
    if log and not quiet:
        log("$ " + " ".join(_quote(part) for part in command))
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd) if cwd else None,
        **subprocess_kwargs(),
    )
    if stop_event is not None:
        threading.Thread(target=_terminate_on_stop, args=(process, stop_event), daemon=True).start()
    tail: list[str] = []
    assert process.stdout is not None
    for raw in process.stdout:
        line = raw.rstrip()
        if line_cb:
            line_cb(line)
        tail.append(line)
        if len(tail) > 30:
            tail.pop(0)
        if log and not quiet:
            log(line)
    code = process.wait()
    if stop_event is not None and stop_event.is_set():
        raise StopRequested()
    if code != 0 and log and quiet:
        for line in tail:
            log(line)
    return code


def run_checked(command: list[str], what: str, **kwargs) -> None:
    if run(command, **kwargs) != 0:
        raise RuntimeError(f"{what} thất bại (xem log).")


def _quote(part: str) -> str:
    return f'"{part}"' if " " in part else part


@dataclass
class MediaInfo:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    has_video: bool = False


def probe(path: str | Path) -> MediaInfo:
    ffprobe = require_tool("ffprobe")
    result = subprocess.run(
        [
            str(ffprobe), "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **subprocess_kwargs(),
    )
    info = MediaInfo()
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return info
    try:
        info.duration = float(data.get("format", {}).get("duration", 0) or 0)
    except ValueError:
        pass
    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and not info.has_video:
            if stream.get("disposition", {}).get("attached_pic"):
                continue
            info.has_video = True
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            info.fps = _parse_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1")
            rotation = _rotation(stream)
            if rotation in (90, 270):
                info.width, info.height = info.height, info.width
        elif kind == "audio":
            info.has_audio = True
    return info


def _parse_rate(rate: str) -> float:
    try:
        num, den = rate.split("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def _rotation(stream: dict) -> int:
    rotate = stream.get("tags", {}).get("rotate")
    if rotate:
        try:
            return abs(int(rotate)) % 360
        except ValueError:
            return 0
    for side in stream.get("side_data_list", []) or []:
        if "rotation" in side:
            try:
                return abs(int(side["rotation"])) % 360
            except (TypeError, ValueError):
                return 0
    return 0


def audio_duration(path: str | Path) -> float:
    return probe(path).duration


def parse_progress_time(value: str) -> float:
    """Đọc out_time=HH:MM:SS.micro từ ffmpeg -progress."""
    try:
        hours, minutes, seconds = value.strip().split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (ValueError, TypeError):
        return 0.0


def atempo_chain(factor: float) -> str:
    """ffmpeg atempo chỉ nhận 0.5..2.0 nên phải xâu chuỗi."""
    factor = max(0.25, min(8.0, factor))
    parts = []
    while factor > 2.0:
        parts.append(2.0)
        factor /= 2.0
    while factor < 0.5:
        parts.append(0.5)
        factor /= 0.5
    parts.append(factor)
    return ",".join(f"atempo={p:.5f}" for p in parts)
