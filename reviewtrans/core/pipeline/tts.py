from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..config import ProviderProfile
from ..models import ProjectContext, Segment, VideoDoc
from ..proc import StopRequested, probe, require_tool, run
from ..providers import ProviderError
from ..providers.tts import TTSProvider, create_tts
from ..resolve import Resolved, resolve
from . import RunContext


def tts_resolved(ctx: RunContext, doc: VideoDoc | None = None) -> Resolved:
    resolved = resolve(ctx.settings, "tts", ctx.project, doc)
    if resolved.profile is None:
        raise ProviderError("Chưa cấu hình provider TTS (trang Providers).")
    return resolved


def tts_profile(ctx: RunContext, doc: VideoDoc | None = None) -> ProviderProfile:
    return tts_resolved(ctx, doc).profile


def resolve_voice(seg: Segment, context: ProjectContext, provider: TTSProvider, default_voice: str = "") -> str:
    """Giọng của câu: giọng riêng của câu > giọng nhân vật > giọng video/project > giọng của profile."""
    if seg.voice:
        return seg.voice
    if seg.speaker:
        for char in context.characters:
            if char.voice and seg.speaker in (char.source, char.target):
                return char.voice
    return default_voice or provider.voice


def segment_key(seg: Segment, profile: ProviderProfile, voice: str, speed: float) -> str:
    parts = [
        seg.text.strip(), profile.id, profile.kind, profile.model, profile.base_url, voice, f"{speed:.2f}",
        str(sorted((k, str(v)) for k, v in profile.options.items() if k not in ("voices",))),
    ]
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def tts_path(ctx: RunContext, doc: VideoDoc, seg: Segment) -> Path | None:
    if not seg.tts_file:
        return None
    path = ctx.store.tts_dir(doc.id) / seg.tts_file
    return path if path.exists() else None


def needs_tts(ctx: RunContext, doc: VideoDoc, seg: Segment, key: str) -> bool:
    return bool(seg.text.strip()) and (seg.tts_key != key or tts_path(ctx, doc, seg) is None)


def _convert_to_wav(raw: Path, target: Path, ctx: RunContext) -> None:
    ffmpeg = require_tool("ffmpeg")
    code = run(
        [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw), "-ac", "1", "-ar", "44100", str(target)],
        log=ctx.log, stop_event=ctx.stop_event, quiet=True,
    )
    if code != 0 or not target.exists():
        raise ProviderError("Chuyển đổi âm thanh TTS thất bại")


def run_tts(
    ctx: RunContext,
    doc: VideoDoc,
    segments: list[Segment],
    targets: list[Segment] | None = None,
    force: bool = False,
    save=None,
) -> tuple[int, int]:
    """Tạo TTS cho các câu cần. Trả về (thành công, lỗi)."""
    resolved = tts_resolved(ctx, doc)
    profile = resolved.profile
    provider = create_tts(profile)
    context = ctx.store.load_context()
    speed = doc.audio.speed
    tts_dir = ctx.store.tts_dir(doc.id)
    pool = targets if targets is not None else segments
    jobs: list[tuple[Segment, str, str]] = []
    for seg in pool:
        voice = resolve_voice(seg, context, provider, resolved.value)
        key = segment_key(seg, profile, voice, speed)
        if force or needs_tts(ctx, doc, seg, key):
            if seg.text.strip():
                jobs.append((seg, voice, key))
    if not jobs:
        ctx.log("Tất cả câu đã có lồng tiếng mới nhất.")
        return 0, 0
    ctx.log(f"Tạo lồng tiếng cho {len(jobs)} câu bằng {profile.name}")
    lock = threading.Lock()
    done = failed = 0
    errors: list[str] = []

    def work(seg: Segment, voice: str, key: str) -> tuple[Segment, str, float, str]:
        base = tts_dir / f"raw_{seg.id}_{key[:10]}"
        last: Exception | None = None
        for attempt in range(3):
            ctx.check_stop()
            text = seg.text.strip() if attempt < 2 else seg.text.strip() + " ."
            try:
                raw = provider.synthesize(text, voice, speed, base, ctx.stop_event)
                target = tts_dir / f"seg_{seg.id}_{key[:10]}.wav"
                _convert_to_wav(raw, target, ctx)
                if raw != target:
                    raw.unlink(missing_ok=True)
                return seg, target.name, probe(target).duration, key
            except StopRequested:
                raise
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise ProviderError(f"câu #{seg.id}: {last}")

    concurrency = max(1, int(profile.option("concurrency", ctx.settings.tts_concurrency)))
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(work, *job) for job in jobs]
        try:
            for future in as_completed(futures):
                try:
                    seg, name, duration, key = future.result()
                except StopRequested:
                    raise
                except Exception as exc:  # noqa: BLE001
                    with lock:
                        failed += 1
                        errors.append(str(exc))
                    ctx.log(f"  lỗi TTS {exc}")
                    continue
                with lock:
                    old = seg.tts_file
                    seg.tts_file, seg.tts_duration, seg.tts_key = name, duration, key
                    if old and old != name:
                        (tts_dir / old).unlink(missing_ok=True)
                    done += 1
                    ctx.progress((done + failed) * 100.0 / len(jobs), f"Lồng tiếng {done + failed}/{len(jobs)}")
                    if save and done % 5 == 0:
                        save()
        except StopRequested:
            for future in futures:
                future.cancel()
            raise
    if save:
        save()
    if failed:
        ctx.log(f"{failed} câu lỗi TTS: " + "; ".join(errors[:5]))
    return done, failed
