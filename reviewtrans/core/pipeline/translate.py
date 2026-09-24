from __future__ import annotations

from ..config import ProviderProfile
from ..context import UPDATE_SYSTEM, build_update_prompt, context_block, extract_json, glossary_pairs, merge_update
from ..models import Segment, VideoDoc
from ..providers import ProviderError
from ..providers.translate import TranslateJob, Translator, create_translator
from ..resolve import Resolved, resolve
from . import RunContext


def translate_resolved(ctx: RunContext, doc: VideoDoc | None = None) -> Resolved:
    resolved = resolve(ctx.settings, "translate", ctx.project, doc)
    if resolved.profile is None:
        raise ProviderError("Chưa cấu hình provider dịch (trang Providers).")
    return resolved


def translate_profile(ctx: RunContext, doc: VideoDoc | None = None) -> ProviderProfile:
    return translate_resolved(ctx, doc).profile


def make_translator(ctx: RunContext, doc: VideoDoc | None = None) -> Translator:
    resolved = translate_resolved(ctx, doc)
    return create_translator(resolved.profile, resolved.value)


def translate_segments(
    ctx: RunContext,
    doc: VideoDoc,
    segments: list[Segment],
    targets: list[Segment],
    save=None,
) -> int:
    """Dịch danh sách `targets` (thuộc `segments`). Trả về số câu đã dịch."""
    if not targets:
        return 0
    translator = make_translator(ctx, doc)
    context = ctx.store.load_context()
    job = TranslateJob(
        source_lang=doc.source_language or ctx.project.source_language,
        target_lang=ctx.project.target_language,
        context_text=context_block(context, ctx.project.instructions) if translator.uses_context else "",
        glossary=glossary_pairs(context),
        log=ctx.log,
        stop_event=ctx.stop_event,
    )
    batch_size = max(1, min(ctx.settings.translate_batch_size, translator.max_batch))
    index_of = {id(seg): i for i, seg in enumerate(segments)}
    done = 0
    ctx.log(f"Dịch {len(targets)} câu bằng {translator.profile.name} ({translator.model or translator.profile.kind})")
    for start in range(0, len(targets), batch_size):
        ctx.check_stop()
        batch = targets[start:start + batch_size]
        first = index_of.get(id(batch[0]), 0)
        job.previous = [
            (s.source, s.text) for s in segments[max(0, first - 6):first] if s.text and s.source
        ]
        results = translator.translate([s.source for s in batch], job)
        for seg, text in zip(batch, results):
            seg.text = text.strip()
        done += len(batch)
        ctx.progress(done * 100.0 / len(targets), f"Đã dịch {done}/{len(targets)} câu")
        if save:
            save()
    return done


def run_translate(ctx: RunContext, doc: VideoDoc, segments: list[Segment], only_missing: bool = True, save=None) -> None:
    targets = [
        s for s in segments
        if s.source.strip() and not s.locked and (not only_missing or not s.text.strip())
    ]
    if not targets:
        ctx.log("Không có câu nào cần dịch.")
    translate_segments(ctx, doc, segments, targets, save)


def context_llm(ctx: RunContext, doc: VideoDoc | None = None) -> Translator | None:
    if ctx.project.context_profile:
        profile = ctx.settings.find_translate(ctx.project.context_profile)
        if profile is not None and profile.is_llm:
            return create_translator(profile)
    resolved = translate_resolved(ctx, doc)
    if not resolved.profile.is_llm:
        return None
    return create_translator(resolved.profile, resolved.value)


def update_context(ctx: RunContext, doc: VideoDoc, segments: list[Segment]) -> list[str]:
    llm = context_llm(ctx, doc)
    if llm is None:
        ctx.log("Bỏ qua cập nhật ngữ cảnh: cần một provider LLM (OpenAI/Gemini/Anthropic).")
        return []
    ctx.progress(0, "Cập nhật ngữ cảnh project")
    context = ctx.store.load_context()
    prompt = build_update_prompt(
        context, segments, doc.source_language or ctx.project.source_language, ctx.project.target_language, doc.name
    )
    raw = llm.complete(UPDATE_SYSTEM, prompt)
    try:
        data = extract_json(raw)
    except ValueError as exc:
        raise ProviderError(f"Không đọc được JSON ngữ cảnh: {raw[:200]}") from exc
    if not isinstance(data, dict):
        raise ProviderError("LLM trả về ngữ cảnh không đúng định dạng.")
    # đọc lại ngay trước khi gộp để giữ các sửa tay mới nhất
    context = ctx.store.load_context()
    changes = merge_update(context, data, doc.name)
    if doc.id not in context.processed_videos:
        context.processed_videos.append(doc.id)
    ctx.store.save_context(context)
    ctx.context_changed()
    ctx.log("Ngữ cảnh: " + ("; ".join(changes) if changes else "không có thay đổi"))
    ctx.progress(100, "Đã cập nhật ngữ cảnh")
    return changes
