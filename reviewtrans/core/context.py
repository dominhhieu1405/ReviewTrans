from __future__ import annotations

import json
import re

from .langs import english_name
from .models import Character, ContextLog, GlossaryEntry, ProjectContext, Segment

MAX_SUMMARY_CHARS = 4000


def context_block(context: ProjectContext, instructions: str = "") -> str:
    """Khối ngữ cảnh chèn vào prompt dịch."""
    parts: list[str] = []
    if context.style_notes.strip():
        parts.append("GENRE / TONE:\n" + context.style_notes.strip())
    if context.summary.strip():
        parts.append("STORY SO FAR:\n" + context.summary.strip())
    if context.characters:
        lines = []
        for c in context.characters:
            if not c.source and not c.target:
                continue
            detail = [x for x in (c.gender, c.role) if x]
            line = f"- {c.source} => {c.target or c.source}"
            if detail:
                line += f" ({', '.join(detail)})"
            if c.addressing:
                line += f"; addressing: {c.addressing}"
            if c.note:
                line += f"; note: {c.note}"
            lines.append(line)
        if lines:
            parts.append("CHARACTERS (use these names and forms of address consistently):\n" + "\n".join(lines))
    if context.glossary:
        lines = [
            f"- {g.source} => {g.target}" + (f" ({g.note})" if g.note else "")
            for g in context.glossary
            if g.source and g.target
        ]
        if lines:
            parts.append("GLOSSARY (mandatory translations):\n" + "\n".join(lines))
    if instructions.strip():
        parts.append("EXTRA INSTRUCTIONS FROM THE USER:\n" + instructions.strip())
    return "\n\n".join(parts)


def glossary_pairs(context: ProjectContext) -> list[tuple[str, str]]:
    pairs = [(g.source, g.target) for g in context.glossary if g.source and g.target]
    pairs += [(c.source, c.target) for c in context.characters if c.source and c.target]
    # thay cụm dài trước để tránh thay một phần
    return sorted(set(pairs), key=lambda p: len(p[0]), reverse=True)


def apply_glossary_to_source(text: str, pairs: list[tuple[str, str]]) -> str:
    """Với máy dịch (Google/Microsoft): thay trước thuật ngữ gốc bằng bản dịch đã chốt."""
    for source, target in pairs:
        if source in text:
            text = text.replace(source, target)
    return text


# ------------------------------------------------------------------ auto update

UPDATE_SYSTEM = (
    "You maintain a shared translation context (a 'series bible') for a multi-episode "
    "video translation project. You read the latest episode's source lines and their "
    "translations, then report ONLY new or corrected information as JSON."
)


def build_update_prompt(
    context: ProjectContext,
    segments: list[Segment],
    source_lang: str,
    target_lang: str,
    video_name: str,
    max_lines: int = 600,
) -> str:
    lines = []
    step = max(1, len(segments) // max_lines) if len(segments) > max_lines else 1
    for seg in segments[::step]:
        if seg.source or seg.text:
            lines.append(f"{seg.source} || {seg.text}")
    current = {
        "summary": context.summary,
        "style_notes": context.style_notes,
        "characters": [
            {"source": c.source, "target": c.target, "gender": c.gender, "role": c.role, "addressing": c.addressing}
            for c in context.characters
        ],
        "glossary": [{"source": g.source, "target": g.target, "note": g.note} for g in context.glossary],
    }
    return (
        f"Source language: {english_name(source_lang)}. Target language: {english_name(target_lang)}.\n"
        f"Episode: {video_name}\n\n"
        "CURRENT CONTEXT (JSON):\n"
        f"{json.dumps(current, ensure_ascii=False)}\n\n"
        "EPISODE LINES (source || translation):\n"
        + "\n".join(lines)
        + "\n\n"
        "Return a JSON object with these keys:\n"
        '- "summary": the updated cumulative story summary in the TARGET language, '
        f"at most {MAX_SUMMARY_CHARS // 2} characters, merging the old summary with this episode.\n"
        '- "style_notes": genre/tone notes in the target language (keep the old text if nothing changes).\n'
        '- "characters": list of NEW or CORRECTED characters, each '
        '{"source","target","gender","role","addressing"}. "addressing" describes, in the target language, '
        "how this character refers to themselves and to others (pronouns / honorifics).\n"
        '- "glossary": list of NEW recurring proper nouns or terms {"source","target","note"} '
        "(places, sects, techniques, items, titles). Skip common words.\n"
        "Keep names consistent with the existing context. Output JSON only."
    )


def extract_json(text: str):
    """Lấy JSON đầu tiên trong câu trả lời của LLM (bỏ ```json ...```)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            candidate = text[start:end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    return json.loads(text)


def merge_update(context: ProjectContext, data: dict, video_name: str) -> list[str]:
    """Gộp kết quả LLM vào ngữ cảnh, tôn trọng mục đã khoá. Trả về danh sách thay đổi."""
    changes: list[str] = []
    summary = str(data.get("summary") or "").strip()
    if summary and summary != context.summary:
        context.summary = summary[:MAX_SUMMARY_CHARS]
        changes.append("cập nhật tóm tắt")
    style = str(data.get("style_notes") or "").strip()
    if style and style != context.style_notes:
        context.style_notes = style
        changes.append("cập nhật thể loại/giọng văn")

    by_source = {c.source.strip(): c for c in context.characters if c.source.strip()}
    for raw in data.get("characters") or []:
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source") or "").strip()
        if not source:
            continue
        existing = by_source.get(source)
        if existing is None:
            char = Character(
                source=source,
                target=str(raw.get("target") or "").strip(),
                gender=str(raw.get("gender") or "").strip(),
                role=str(raw.get("role") or "").strip(),
                addressing=str(raw.get("addressing") or "").strip(),
                auto=True,
            )
            context.characters.append(char)
            by_source[source] = char
            changes.append(f"thêm nhân vật {source} → {char.target}")
        elif not existing.locked:
            updated = False
            for key in ("target", "gender", "role", "addressing"):
                value = str(raw.get(key) or "").strip()
                if value and value != getattr(existing, key):
                    setattr(existing, key, value)
                    updated = True
            if updated:
                changes.append(f"sửa nhân vật {source}")

    glossary = {g.source.strip(): g for g in context.glossary if g.source.strip()}
    for raw in data.get("glossary") or []:
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source") or "").strip()
        target = str(raw.get("target") or "").strip()
        if not source or not target or source in by_source:
            continue
        existing = glossary.get(source)
        if existing is None:
            entry = GlossaryEntry(source=source, target=target, note=str(raw.get("note") or "").strip(), auto=True)
            context.glossary.append(entry)
            glossary[source] = entry
            changes.append(f"thêm thuật ngữ {source} → {target}")
        elif not existing.locked and existing.target != target:
            existing.target = target
            changes.append(f"sửa thuật ngữ {source} → {target}")

    if changes:
        context.changelog.append(ContextLog(video=video_name, message="; ".join(changes)))
        context.changelog = context.changelog[-200:]
    return changes
