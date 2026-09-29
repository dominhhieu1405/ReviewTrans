from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..ass import build_ass
from ..config import RenderSettings
from ..geometry import layer_rect
from ..hardware import EncoderConfig, detect, load_cached, mark_failed, probe_encoder, quality_args, resolve_encoder
from ..paths import fonts_dir
from ..models import LAYER_BLUR, LAYER_IMAGE, LAYER_TEXT, Layer, Segment, VideoDoc, clone
from ..proc import parse_progress_time, require_tool, run
from ..store import slugify
from . import RunContext
from .mix import filter_script_args


@dataclass
class RenderPlan:
    command: list[str]
    cwd: Path
    duration: float
    output: Path
    graph: str = ""
    inputs: list[str] = field(default_factory=list)


def _enable(layer: Layer, duration: float, offset: float) -> str:
    start, end = layer.time_range(duration)
    start -= offset
    end -= offset
    return f"between(t,{max(0.0, start):.3f},{max(0.0, end):.3f})"


def _hex(color: str) -> str:
    value = (color or "#000000").lstrip("#")
    return "0x" + (value[-6:] if len(value) >= 6 else "000000")


def _stage_fonts(work_dir: Path) -> bool:
    """Chép font người dùng vào thư mục làm việc để libass dùng được (đường dẫn tương đối, khỏi escape)."""
    source = fonts_dir()
    files = [p for p in source.iterdir() if p.suffix.lower() in (".ttf", ".otf", ".ttc")] if source.exists() else []
    if not files:
        return False
    target = work_dir / "fonts"
    target.mkdir(parents=True, exist_ok=True)
    for font in files:
        dest = target / font.name
        if not dest.exists() or dest.stat().st_size != font.stat().st_size:
            shutil.copy2(font, dest)
    return True


def build_render_plan(
    doc: VideoDoc,
    segments: list[Segment],
    render: RenderSettings,
    work_dir: Path,
    output: Path,
    mix_audio: Path | None,
    start: float = 0.0,
    length: float | None = None,
    image_size_fn=None,
    text_raster_fn=None,
    encoder: EncoderConfig | None = None,
    hwaccel_decode: bool | None = None,
) -> RenderPlan:
    """Dịch mô hình timeline thành lệnh ffmpeg. `start/length` dùng cho xuất thử một đoạn."""
    ffmpeg = require_tool("ffmpeg")
    work_dir.mkdir(parents=True, exist_ok=True)
    width, height = doc.width or 1920, doc.height or 1080
    duration = doc.duration if length is None else min(length, max(0.1, doc.duration - start))
    offset = start

    if encoder is None:
        encoder = resolve_encoder(render.video_codec, load_cached())[0]
    if hwaccel_decode is None:
        hwaccel_decode = render.hwaccel_decode

    command = [str(ffmpeg), "-y", "-hide_banner", "-nostats", "-progress", "pipe:1"]
    if hwaccel_decode:
        command += ["-hwaccel", "auto"]
    command += encoder.pre_args
    if start > 0:
        command += ["-ss", f"{start:.3f}"]
    if length is not None:
        command += ["-t", f"{duration:.3f}"]
    command += ["-i", doc.source_path]
    inputs = [doc.source_path]

    parts: list[str] = []
    current = "[0:v]"
    counter = 0

    def next_label() -> str:
        nonlocal counter
        counter += 1
        return f"[v{counter}]"

    if doc.flip_horizontal:
        label = next_label()
        parts.append(f"{current}hflip{label}")
        current = label

    layers = [layer for layer in doc.layers if layer.enabled] if doc.tracks.layers else []
    for layer in layers:
        lstart, lend = layer.time_range(doc.duration)
        if lend <= offset or lstart >= offset + duration:
            continue
        enable = _enable(layer, doc.duration, offset)
        if layer.type == LAYER_BLUR:
            x, y, w, h = layer_rect(layer, width, height)
            if w < 4 or h < 4:
                continue
            if layer.blur_mode == "fill":
                label = next_label()
                parts.append(
                    f"{current}drawbox=x={x}:y={y}:w={w}:h={h}:color={_hex(layer.fill_color)}@{layer.opacity:.2f}"
                    f":t=fill:enable='{enable}'{label}"
                )
                current = label
                continue
            strength = max(1, int(layer.blur_strength))
            if layer.blur_mode == "pixelate":
                block = max(2, strength)
                effect = (
                    f"scale={max(1, w // block)}:{max(1, h // block)}:flags=area,"
                    f"scale={w}:{h}:flags=neighbor"
                )
            else:
                effect = f"gblur=sigma={strength * height / 1080.0:.2f}:steps=3"
            base, piece, done = f"[b{counter}]", f"[p{counter}]", f"[e{counter}]"
            label = next_label()
            parts.append(f"{current}split=2{base}{piece}")
            parts.append(f"{piece}crop={w}:{h}:{x}:{y},{effect}{done}")
            parts.append(f"{base}{done}overlay={x}:{y}:enable='{enable}'{label}")
            current = label
        elif layer.type in (LAYER_IMAGE, LAYER_TEXT):
            if layer.type == LAYER_IMAGE:
                if not layer.image_path or not Path(layer.image_path).exists():
                    continue
                size = image_size_fn(layer.image_path) if image_size_fn else None
                x, y, w, h = layer_rect(layer, width, height, size)
                source = layer.image_path
                opacity = layer.opacity
            else:
                if not layer.text.strip() or text_raster_fn is None:
                    continue
                png = work_dir / f"text_{layer.id}.png"
                x, y, w, h = text_raster_fn(layer, width, height, png)
                source = str(png)
                opacity = 1.0  # đã nướng độ trong suốt vào PNG
            command += ["-i", source]
            inputs.append(source)
            idx = len(inputs) - 1
            prepared = f"[i{idx}]"
            chain = f"[{idx}:v]format=rgba,scale={w}:{h}"
            if opacity < 0.999:
                chain += f",colorchannelmixer=aa={max(0.0, opacity):.3f}"
            parts.append(chain + prepared)
            label = next_label()
            parts.append(f"{current}{prepared}overlay={x}:{y}:enable='{enable}'{label}")
            current = label

    if doc.tracks.subtitles:
        shifted = []
        for seg in segments:
            if seg.end <= offset or seg.start >= offset + duration:
                continue
            copy = clone(seg)
            copy.start -= offset
            copy.end -= offset
            shifted.append(copy)
        if shifted:
            (work_dir / "subs.ass").write_text(build_ass(shifted, doc.style, width, height), encoding="utf-8")
            label = next_label()
            fonts = "fontsdir=fonts:" if _stage_fonts(work_dir) else ""
            parts.append(f"{current}subtitles={fonts}filename=subs.ass{label}")
            current = label

    label = "[vout]"
    parts.append(f"{current}{encoder.tail}{label}")
    graph = ";".join(parts)

    audio_index = None
    if mix_audio is not None and mix_audio.exists():
        if start > 0:
            command += ["-ss", f"{start:.3f}"]
        if length is not None:
            command += ["-t", f"{duration:.3f}"]
        command += ["-i", str(mix_audio)]
        inputs.append(str(mix_audio))
        audio_index = len(inputs) - 1

    command += filter_script_args(work_dir, "render_graph.txt", graph)
    command += ["-map", "[vout]"]
    if audio_index is not None:
        command += ["-map", f"{audio_index}:a", "-c:a", "aac", "-b:a", render.audio_bitrate]
    elif doc.has_audio and doc.tracks.original_audio:
        command += ["-map", "0:a?", "-c:a", "aac", "-b:a", render.audio_bitrate]
    else:
        command += ["-an"]

    command += ["-c:v", encoder.codec, *quality_args(encoder.codec, render.crf, render.preset), *encoder.args]
    command += ["-shortest", "-movflags", "+faststart", str(output)]
    return RenderPlan(command=command, cwd=work_dir, duration=duration, output=output, graph=graph, inputs=inputs)


def output_path(ctx: RunContext, doc: VideoDoc, suffix: str = "") -> Path:
    folder = ctx.store.output_dir(ctx.project)
    return folder / f"{slugify(doc.name)}{suffix}.mp4"


def choose_encoder(ctx: RunContext, exclude: set[str] | None = None) -> tuple[EncoderConfig, bool]:
    """Bộ mã hoá sẽ dùng + có giải mã bằng GPU không. Bộ mã hoá GPU được chạy thử trước (cũng để đánh thức GPU rời).
    Bộ nào chạy thử lỗi được thêm vào `exclude` (chỉ cho lần xuất này — lỗi driver thường chập chờn)."""
    exclude = set() if exclude is None else exclude
    render = ctx.settings.render
    if render.video_codec in ("libx264", "libx265"):
        return resolve_encoder(render.video_codec, None)[0], render.hwaccel_decode
    info = load_cached()
    if info is None:
        ctx.progress(0, "Dò bộ mã hoá GPU")
        info = detect(log=ctx.log)
    ffmpeg = require_tool("ffmpeg")
    while True:
        encoder, note = resolve_encoder(render.video_codec, info, exclude)
        if note:
            ctx.log(note)
        if not encoder.hardware:
            return encoder, render.hwaccel_decode
        ok, message = probe_encoder(ffmpeg, encoder)
        if ok:
            ctx.log(f"Mã hoá bằng GPU: {encoder.codec} ({encoder.device})")
            return encoder, render.hwaccel_decode
        ctx.log(f"{encoder.codec} chạy thử lỗi: {message}")
        exclude.add(encoder.codec)


def run_render(
    ctx: RunContext,
    doc: VideoDoc,
    segments: list[Segment],
    mix_audio: Path | None,
    start: float = 0.0,
    length: float | None = None,
    output: Path | None = None,
) -> Path:
    from ..raster import image_size, render_text_layer_png

    work_dir = ctx.store.cache_dir(doc.id) / "render"
    if output is None:
        output = output_path(ctx, doc, "_thu" if length is not None else "")
    output.parent.mkdir(parents=True, exist_ok=True)
    excluded: set[str] = set()
    encoder, hwaccel = choose_encoder(ctx, excluded)

    def plan_for(config: EncoderConfig, hw_decode: bool) -> RenderPlan:
        return build_render_plan(
            doc, segments, ctx.settings.render, work_dir, output, mix_audio, start, length,
            image_size_fn=image_size, text_raster_fn=render_text_layer_png, encoder=config, hwaccel_decode=hw_decode,
        )

    plan = plan_for(encoder, hwaccel)
    ctx.log("Filter: " + plan.graph[:2000])
    started = time.monotonic()

    def execute(plan: RenderPlan) -> tuple[int, list[str]]:
        tail: list[str] = []

        def collect(line: str) -> None:
            if line.startswith("out_time=") and plan.duration > 0:
                seconds = parse_progress_time(line.split("=", 1)[1])
                ctx.progress(min(99.0, seconds * 100.0 / plan.duration), f"Xuất video {seconds:.0f}/{plan.duration:.0f}s")
            if "=" not in line or " " in line:
                tail.append(line)
                del tail[:-20]

        return run(plan.command, log=None, stop_event=ctx.stop_event, cwd=plan.cwd, line_cb=collect), tail

    retries: dict[str, int] = {}
    while True:
        code, tail = execute(plan)
        if code == 0:
            break
        for line in tail:
            ctx.log(line)
        if encoder.hardware:
            # driver GPU đôi khi từ chối mở bộ mã hoá (AMF lỗi 30…) — lỗi xảy ra ngay, thử lại rất rẻ
            opening = any("Error while opening encoder" in line or "CreateComponent" in line for line in tail)
            if opening and retries.get(encoder.codec, 0) < 3:
                retries[encoder.codec] = retries.get(encoder.codec, 0) + 1
                ctx.log(f"{encoder.codec} chưa mở được, thử lại ({retries[encoder.codec]}/3)")
                time.sleep(0.2)
                continue
            if not opening:
                mark_failed(encoder.codec)  # lỗi giữa chừng: bỏ bộ này cho cả phiên làm việc
            excluded.add(encoder.codec)
            previous = encoder.codec
            encoder, hwaccel = choose_encoder(ctx, excluded)
            ctx.log(f"Xuất bằng {previous} thất bại → xuất lại bằng {encoder.codec}")
        elif hwaccel:
            hwaccel = False
            ctx.log("Giải mã bằng GPU lỗi → xuất lại, giải mã bằng CPU")
        else:
            raise RuntimeError("Xuất video thất bại (xem log).")
        plan = plan_for(encoder, hwaccel)
    ctx.log(f"Thời gian xuất: {time.monotonic() - started:.1f}s")
    ctx.progress(100, "Đã xuất video")
    ctx.log(f"Đã xuất: {output}")
    # Kèm file phụ đề SRT
    if length is None:
        from ..srt import render_srt

        srt = output.with_suffix(".srt")
        srt.write_text(render_srt((s.start, s.end, s.display_text()) for s in segments if s.display_text()), encoding="utf-8")
    return output

