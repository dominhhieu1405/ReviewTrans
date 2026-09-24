from __future__ import annotations

import re
from pathlib import Path

from ..langs import whisper_code
from ..models import Segment, VideoDoc
from ..proc import probe, require_tool, run_checked
from ..srt import parse_srt
from . import RunContext
from .resources import ensure_whisper_model

_PROGRESS = re.compile(r"progress\s*=\s*(\d+)%")


def ensure_media_info(doc: VideoDoc) -> None:
    if doc.width and doc.height and doc.duration:
        return
    info = probe(doc.source_path)
    doc.duration = info.duration or doc.duration
    doc.width = info.width or doc.width
    doc.height = info.height or doc.height
    doc.fps = info.fps or doc.fps
    doc.has_audio = info.has_audio


def extract_audio(ctx: RunContext, doc: VideoDoc) -> Path:
    target = ctx.store.cache_dir(doc.id) / "audio16k.wav"
    source = Path(doc.source_path)
    if target.exists() and source.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    ffmpeg = require_tool("ffmpeg")
    ctx.progress(2, "Tách âm thanh")
    run_checked(
        [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", str(target)],
        "Tách âm thanh",
        log=ctx.log,
        stop_event=ctx.stop_event,
    )
    return target


def run_asr(ctx: RunContext, doc: VideoDoc) -> list[Segment]:
    if doc.source_mode == "srt":
        if not doc.srt_path or not Path(doc.srt_path).exists():
            raise FileNotFoundError("Chưa chọn file SRT cho video này.")
        items = parse_srt(Path(doc.srt_path).read_text(encoding="utf-8", errors="replace"))
        ctx.log(f"Đọc {len(items)} câu từ {doc.srt_path}")
        return [Segment(id=i + 1, start=s, end=e, source=t) for i, (s, e, t) in enumerate(items)]

    whisper = require_tool("whisper")
    model = doc.whisper_model or ctx.project.whisper_model or ctx.settings.default_whisper_model
    language = whisper_code(doc.source_language or ctx.project.source_language)
    model_path = ensure_whisper_model(
        model, lambda pct, msg: ctx.progress(pct * 0.1, msg), ctx.stop_event
    )
    audio = extract_audio(ctx, doc)
    out_prefix = ctx.store.cache_dir(doc.id) / "asr"
    ctx.progress(10, f"Whisper {model}")

    def on_line(line: str) -> None:
        match = _PROGRESS.search(line)
        if match:
            ctx.progress(10 + int(match.group(1)) * 0.9, f"Whisper {model}: {match.group(1)}%")

    command = [
        str(whisper), "-m", str(model_path), "-f", str(audio), "-l", language or "auto",
        "-t", str(max(1, ctx.settings.whisper_threads)), "-osrt", "-of", str(out_prefix), "-pp",
    ]
    run_checked(command, "Whisper ASR", log=ctx.log, stop_event=ctx.stop_event, line_cb=on_line)
    srt_file = out_prefix.with_suffix(".srt")
    items = parse_srt(srt_file.read_text(encoding="utf-8", errors="replace"))
    segments = [Segment(id=i + 1, start=s, end=e, source=t) for i, (s, e, t) in enumerate(items) if t.strip()]
    for index, seg in enumerate(segments, start=1):
        seg.id = index
    ctx.log(f"Nhận dạng được {len(segments)} câu.")
    return segments
