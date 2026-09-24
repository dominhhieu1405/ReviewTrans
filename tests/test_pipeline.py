from __future__ import annotations

import json
import re

import pytest

from reviewtrans.core.config import ProviderProfile, SettingsStore
from reviewtrans.core.models import Layer, Segment
from reviewtrans.core.paths import find_tool
from reviewtrans.core.pipeline import RunContext
from reviewtrans.core.pipeline import translate as translate_mod
from reviewtrans.core.pipeline.mix import run_mix
from reviewtrans.core.pipeline.render import run_render
from reviewtrans.core.pipeline.runner import VideoPipeline
from reviewtrans.core.proc import probe, run_checked
from reviewtrans.core.providers.translate.base import LLMTranslator
from reviewtrans.core.store import ProjectStore

needs_ffmpeg = pytest.mark.skipif(not find_tool("ffmpeg"), reason="cần ffmpeg trong bin/")


class FakeLLM(LLMTranslator):
    calls: list[tuple[str, str]] = []

    def _call(self, system, prompt, key):
        FakeLLM.calls.append((system, prompt))
        if "series bible" in system:
            return json.dumps({
                "summary": "Lâm Phàm lên núi.",
                "characters": [{"source": "林凡", "target": "Lâm Phàm", "gender": "nam"}],
                "glossary": [{"source": "宗门", "target": "tông môn"}],
            })
        items = json.loads(re.search(r"Items \(JSON\):\n(.*)\n\nReturn", prompt, re.S).group(1))
        return json.dumps([{"id": it["id"], "text": f"VI:{it['text']}"} for it in items], ensure_ascii=False)


def _ctx(tmp_path, home):
    settings = SettingsStore().settings
    llm = ProviderProfile(name="fake", kind="openai", api_keys=["k"])
    settings.translate_profiles.append(llm)
    store, project = ProjectStore.create(tmp_path / "projects", "Test")
    project.translate_profile = llm.id
    store.save_project(project)
    logs: list[str] = []
    return RunContext(store=store, project=project, settings=settings, log=logs.append), logs


def test_translate_and_context_update(tmp_path, home, monkeypatch):
    ctx, _logs = _ctx(tmp_path, home)
    monkeypatch.setattr(translate_mod, "create_translator", lambda profile, model="": FakeLLM(profile, model))
    doc = ctx.store.add_video(ctx.project, str(tmp_path / "ep.mp4"))
    doc.duration, doc.width, doc.height = 10, 640, 360
    ctx.store.save_video(doc)
    segs = [Segment(id=i, start=i, end=i + 0.8, source=f"句子{i}") for i in range(1, 6)]
    segs[2].locked = True
    ctx.store.save_segments(doc.id, segs)
    pipeline = VideoPipeline(ctx, doc.id)
    pipeline.run(["translate", "context"])
    saved = ctx.store.load_segments(doc.id)
    assert saved[0].text == "VI:句子1"
    assert saved[2].text == ""  # câu bị khoá không dịch
    context = ctx.store.load_context()
    assert context.characters[0].target == "Lâm Phàm"
    assert doc.id in context.processed_videos
    # lần dịch sau có ngữ cảnh trong system prompt
    FakeLLM.calls.clear()
    saved[0].text = ""
    ctx.store.save_segments(doc.id, saved)
    VideoPipeline(ctx, doc.id).run(["translate"])
    assert "tông môn" in FakeLLM.calls[0][0]
    assert ctx.store.load_video(doc.id).stage("translate") == "done"


@needs_ffmpeg
def test_mix_and_render_with_layers(tmp_path, home, qapp):
    from PyQt6 import QtGui

    ctx, logs = _ctx(tmp_path, home)
    ffmpeg = str(find_tool("ffmpeg"))
    source = tmp_path / "src.mp4"
    run_checked(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=6",
         "-f", "lavfi", "-i", "sine=frequency=300:duration=6",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
        "tạo video test",
    )
    info = probe(source)
    doc = ctx.store.add_video(ctx.project, str(source), info)
    assert doc.width == 640 and abs(doc.duration - 6) < 0.2

    tts_dir = ctx.store.tts_dir(doc.id)
    segs = []
    for i, (start, end, dur) in enumerate([(0.5, 1.5, 1.8), (2.0, 3.0, 0.6), (4.0, 5.5, 1.0)], start=1):
        name = f"seg_{i}.wav"
        run_checked(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", f"sine=frequency={500 + i * 100}:duration={dur}", "-ac", "1", "-ar", "44100", str(tts_dir / name)],
            "tạo tts giả",
        )
        segs.append(Segment(id=i, start=start, end=end, source="原文", text=f"Câu thứ {i} có dấu tiếng Việt",
                            tts_file=name, tts_duration=dur))

    logo = tmp_path / "logo.png"
    image = QtGui.QImage(200, 100, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtGui.QColor(255, 0, 0, 180))
    assert image.save(str(logo))
    doc.layers = [
        Layer(type="blur", x=0.0, y=0.75, w=1.0, h=0.2, blur_mode="blur", blur_strength=15),
        Layer(type="blur", x=0.1, y=0.1, w=0.2, h=0.2, blur_mode="pixelate", start=1, end=4),
        Layer(type="blur", x=0.6, y=0.1, w=0.2, h=0.1, blur_mode="fill", fill_color="#112233"),
        Layer(type="image", image_path=str(logo), x=0.02, y=0.02, w=0.15, opacity=0.8),
        Layer(type="text", text="Tập 1 — thử chữ", x=0.5, y=0.02, w=0.45, h=0.12, bg_enabled=True),
    ]
    doc.style.bg_enabled = True
    doc.flip_horizontal = True
    doc.audio.bgm_path = str(source)
    ctx.store.save_video(doc)

    mix = run_mix(ctx, doc, segs)
    assert mix.exists()
    mix_info = probe(mix)
    assert mix_info.has_audio and abs(mix_info.duration - doc.duration) < 0.3

    out = run_render(ctx, doc, segs, mix)
    out_info = probe(out)
    assert out_info.has_video and out_info.has_audio
    assert abs(out_info.duration - doc.duration) < 0.5, logs[-20:]
    assert out.with_suffix(".srt").exists()

    clip = run_render(ctx, doc, segs, mix, start=1.0, length=2.0)
    clip_info = probe(clip)
    assert abs(clip_info.duration - 2.0) < 0.3
