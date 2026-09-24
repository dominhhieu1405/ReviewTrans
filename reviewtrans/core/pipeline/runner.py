from __future__ import annotations

import traceback

from ..models import STAGE_LABELS, STATE_DONE, STATE_ERROR, STATE_RUNNING, STATE_STALE, Segment, VideoDoc
from ..proc import StopRequested
from . import RunContext
from .asr import ensure_media_info, run_asr
from .mix import run_mix
from .render import run_render
from .translate import run_translate, update_context
from .tts import run_tts

PIPELINE_ORDER = ["asr", "translate", "context", "tts", "mix", "render"]
STEP_LABELS = {**STAGE_LABELS, "context": "Cập nhật ngữ cảnh"}


class VideoPipeline:
    """Chạy các bước cho một video, lưu kết quả sau mỗi bước."""

    def __init__(self, ctx: RunContext, video_id: str) -> None:
        self.ctx = ctx
        self.video_id = video_id
        self.doc: VideoDoc = ctx.store.load_video(video_id)
        self.segments: list[Segment] = ctx.store.load_segments(video_id)

    # ------------------------------------------------------------ persistence

    def save(self) -> None:
        self.ctx.store.save_video(self.doc)
        self.ctx.store.save_segments(self.video_id, self.segments)
        self.ctx.video_changed(self.video_id)

    def save_segments(self) -> None:
        self.ctx.store.save_segments(self.video_id, self.segments)
        self.ctx.video_changed(self.video_id)

    # ------------------------------------------------------------ run

    def run(self, steps: list[str], only_missing: bool = True, force_context: bool = False) -> None:
        ctx = self.ctx
        ensure_media_info(self.doc)
        steps = [s for s in PIPELINE_ORDER if s in steps]
        if "render" in steps and "mix" not in steps:
            steps.insert(steps.index("render"), "mix")
        for step in steps:
            ctx.check_stop()
            if step == "context" and not (ctx.project.context_auto_update or force_context):
                continue
            ctx.log(f"── {self.doc.name}: {STEP_LABELS.get(step, step)}")
            ctx.progress(0, STEP_LABELS.get(step, step))
            stage = step if step != "context" else None
            if stage:
                self.doc.set_stage(stage, STATE_RUNNING)
                self.save()
            try:
                self._run_step(step, only_missing)
            except StopRequested:
                if stage:
                    self.doc.set_stage(stage, STATE_STALE)
                    self.save()
                raise
            except Exception as exc:  # noqa: BLE001
                ctx.log(traceback.format_exc(limit=3))
                if stage:
                    self.doc.set_stage(stage, STATE_ERROR, str(exc))
                    self.save()
                raise
            if stage:
                self.doc.set_stage(stage, STATE_DONE)
            self.save()

    def _run_step(self, step: str, only_missing: bool) -> None:
        ctx = self.ctx
        if step == "asr":
            segments = run_asr(ctx, self.doc)
            self.segments = segments
            self.doc.mark_stale_from("translate")
        elif step == "translate":
            run_translate(ctx, self.doc, self.segments, only_missing=only_missing, save=self.save_segments)
            self.doc.mark_stale_from("tts")
        elif step == "context":
            update_context(ctx, self.doc, self.segments)
        elif step == "tts":
            _done, failed = run_tts(ctx, self.doc, self.segments, force=not only_missing, save=self.save_segments)
            self.doc.mark_stale_from("mix")
            if failed:
                raise RuntimeError(f"{failed} câu tạo lồng tiếng thất bại (các câu khác đã lưu).")
        elif step == "mix":
            run_mix(ctx, self.doc, self.segments)
            self.doc.mark_stale_from("render")
        elif step == "render":
            mix = ctx.store.cache_dir(self.video_id) / "mix.wav"
            output = run_render(ctx, self.doc, self.segments, mix if mix.exists() else None)
            self.doc.last_output = str(output)
