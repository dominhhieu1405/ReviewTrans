from __future__ import annotations

import json

from reviewtrans.core.ass import ass_color, ass_time, build_ass
from reviewtrans.core.config import SettingsStore
from reviewtrans.core.context import apply_glossary_to_source, context_block, extract_json, glossary_pairs, merge_update
from reviewtrans.core.geometry import layer_rect, wrap_text
from reviewtrans.core.models import (
    Character,
    GlossaryEntry,
    Layer,
    Project,
    ProjectContext,
    Segment,
    SubtitleStyle,
    VideoDoc,
    from_dict,
    to_dict,
)
from reviewtrans.core.pipeline.mix import plan_clips
from reviewtrans.core.proc import atempo_chain
from reviewtrans.core.providers.translate.base import parse_items
from reviewtrans.core.srt import parse_srt, render_srt
from reviewtrans.core.store import ProjectStore


def test_srt_roundtrip():
    text = "﻿1\r\n00:00:01,000 --> 00:00:02,500\r\n你好\r\n世界\r\n\r\n00:00:03,000 --> 00:00:04,000\r\n再见\r\n"
    items = parse_srt(text)
    assert items == [(1.0, 2.5, "你好 世界"), (3.0, 4.0, "再见")]
    assert parse_srt(render_srt(items)) == items


def test_model_roundtrip_ignores_unknown_keys():
    doc = VideoDoc(name="a", layers=[Layer(type="text", text="hi")])
    data = to_dict(doc)
    data["unknown"] = 1
    data["style"]["bogus"] = 2
    again = from_dict(VideoDoc, json.loads(json.dumps(data)))
    assert again.layers[0].text == "hi"
    assert again.style == doc.style


def test_mark_stale_from():
    doc = VideoDoc(stages={"asr": "done", "translate": "done", "tts": "done", "render": "done"})
    doc.mark_stale_from("tts")
    assert doc.stages == {"asr": "done", "translate": "done", "tts": "stale", "render": "stale"}


def test_ass_color_and_time():
    assert ass_color("#FF8000") == "&H000080FF"
    assert ass_color("#000000", 0.0) == "&HFF000000"
    assert ass_time(3661.239) == "1:01:01.24"


def test_build_ass_box_layers():
    style = SubtitleStyle(bg_enabled=True)
    ass = build_ass([Segment(start=0, end=1, text="Xin {chào}")], style, 1280, 720)
    assert "Style: Box" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:01.00,Box,,0,0,0,,Xin (chào)" in ass
    assert "PlayResY: 720" in ass


def test_wrap_text():
    assert wrap_text("một hai ba bốn năm sáu", 10).count("\n") >= 1
    assert wrap_text("一二三四五六七八", 4) == "一二三四\n五六七八"
    assert wrap_text("ngắn", 0) == "ngắn"


def test_layer_rect_even_and_clamped():
    layer = Layer(x=0.9, y=0.9, w=0.5, h=0.5)
    x, y, w, h = layer_rect(layer, 1920, 1080)
    assert x + w <= 1920 and y + h <= 1080 and w % 2 == 0 and h % 2 == 0
    image = Layer(type="image", x=0, y=0, w=0.1, keep_aspect=True)
    assert layer_rect(image, 1000, 1000, (200, 100))[3] == 50


def test_atempo_chain():
    assert atempo_chain(3.0).count("atempo") == 2
    assert atempo_chain(1.2) == "atempo=1.20000"


def test_parse_items_variants():
    assert parse_items('```json\n[{"id":1,"text":"a"},{"id":2,"text":"b"}]\n```', 2) == ["a", "b"]
    assert parse_items('{"items":[{"id":2,"t":"b"},{"id":1,"t":"a"}]}', 2) == ["a", "b"]
    assert parse_items("a\nb", 2) == ["a", "b"]
    assert parse_items('[{"id":1,"text":"a"}]', 2) is None


def test_context_merge_respects_locks():
    ctx = ProjectContext(
        characters=[Character(source="林凡", target="Lâm Phàm", locked=True)],
        glossary=[GlossaryEntry(source="宗门", target="tông môn")],
    )
    changes = merge_update(
        ctx,
        {
            "summary": "Tóm tắt mới",
            "characters": [
                {"source": "林凡", "target": "Lin Fan"},
                {"source": "苏雪", "target": "Tô Tuyết", "gender": "nữ", "addressing": "gọi Lâm Phàm là huynh"},
            ],
            "glossary": [{"source": "宗门", "target": "môn phái"}, {"source": "灵石", "target": "linh thạch"}],
        },
        "Tập 1",
    )
    assert ctx.characters[0].target == "Lâm Phàm"  # đã khoá
    assert any(c.source == "苏雪" and c.auto for c in ctx.characters)
    assert {g.source: g.target for g in ctx.glossary} == {"宗门": "môn phái", "灵石": "linh thạch"}
    assert ctx.summary == "Tóm tắt mới"
    assert changes and ctx.changelog[-1].video == "Tập 1"
    block = context_block(ctx, "Giữ giọng hài hước")
    assert "Lâm Phàm" in block and "linh thạch" in block and "hài hước" in block
    pairs = glossary_pairs(ctx)
    assert apply_glossary_to_source("林凡拿灵石", pairs) == "Lâm Phàm拿linh thạch"


def test_extract_json_with_prose():
    assert extract_json('Đây là kết quả:\n{"a": 1}\nHết.') == {"a": 1}


def test_plan_clips_fit_and_gap():
    doc = VideoDoc(duration=20)
    segs = [
        Segment(id=1, start=0, end=1, tts_file="a.wav", tts_duration=3.0),
        Segment(id=2, start=2, end=3, tts_file="b.wav", tts_duration=0.5),
    ]
    clips = plan_clips(doc, segs)
    assert abs(clips[0].tempo - 1.5) < 1e-6  # khe = tới câu sau (2s)
    assert clips[1].tempo == 1.0
    doc.audio.use_gap = False
    doc.audio.max_speed = 2.0
    assert abs(plan_clips(doc, segs)[0].tempo - 2.0) < 1e-6


def test_store_and_settings(home, tmp_path):
    legacy = home / ".video_translation_studio"
    legacy.mkdir()
    (legacy / "config.json").write_text(json.dumps({"gemini_api_keys": ["k1", "k2"], "openai_api_key": "o"}))
    store = SettingsStore()
    kinds = [p.kind for p in store.settings.translate_profiles]
    assert "gemini" in kinds and "openai" in kinds
    assert store.settings.find_translate(store.settings.default_translate_profile).kind == "gemini"

    pstore, project = ProjectStore.create(tmp_path / "projects", "Phim A")
    doc = pstore.add_video(project, str(tmp_path / "ep1.mp4"))
    pstore.save_segments(doc.id, [Segment(id=1, start=0, end=1, source="x")])
    reopened = ProjectStore.open(pstore.root)
    assert reopened.load_project().videos[0].id == doc.id
    assert reopened.load_segments(doc.id)[0].source == "x"
    assert isinstance(reopened.load_project(), Project)


def test_extract_7z_with_bcj2(tmp_path):
    import subprocess

    import pytest

    from reviewtrans.core.pipeline.resources import _seven_zip_exe, extract_7z

    seven = _seven_zip_exe()
    if not seven:
        pytest.skip("cần 7-Zip để tạo file thử BCJ2")
    src = tmp_path / "src"
    src.mkdir()
    (src / "libmpv-2.dll").write_bytes(b"MZ" + bytes(range(256)) * 64)
    archive = tmp_path / "pkg.7z"
    subprocess.run(
        [str(seven), "a", "-y", str(archive), str(src / "libmpv-2.dll"),
         "-m0=BCJ2", "-m1=LZMA:d25", "-m2=LZMA:d19", "-m3=LZMA:d19", "-mb0:1", "-mb0s1:2", "-mb0s2:3"],
        check=True, capture_output=True,
    )
    out = tmp_path / "out"
    out.mkdir()
    extract_7z(archive, out)
    assert (out / "libmpv-2.dll").read_bytes() == (src / "libmpv-2.dll").read_bytes()


def test_openai_speech_uses_server_voices_and_models(tmp_path, monkeypatch):
    from reviewtrans.core.config import ProviderProfile
    from reviewtrans.core.providers import tts as tts_mod

    class Resp:
        def __init__(self, status, data=None, content=b"", ctype="application/json"):
            self.status_code, self._data, self.content = status, data, content
            self.headers = {"content-type": ctype}
            self.text = str(data)

        def json(self):
            return self._data

    calls = []

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/voices"):
            return Resp(200, {"object": "list", "data": [{"id": "Trúc Ly", "name": "Trúc Ly", "description": "Nữ"}]})
        if url.endswith("/models"):
            return Resp(200, {"data": [{"id": "vieneu-v3-turbo"}]})
        return Resp(404, {})

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        return Resp(200, content=b"RIFF....WAVE", ctype="audio/wav")

    monkeypatch.setattr(tts_mod.requests, "get", fake_get)
    monkeypatch.setattr(tts_mod.requests, "post", fake_post)
    tts_mod.OpenAISpeechTTS._remote_cache.clear()
    provider = tts_mod.create_tts(ProviderProfile(kind="openai_speech", base_url="https://tts.example/v1", options={"format": "pcm"}))
    assert provider.list_voices() == [("Trúc Ly", "Trúc Ly — Nữ")]
    out = provider.synthesize("xin chào", "", 1.0, tmp_path / "a")
    assert calls[0]["voice"] == "Trúc Ly" and calls[0]["model"] == "vieneu-v3-turbo"
    assert calls[0]["response_format"] == "wav" and out.suffix == ".wav"
    tts_mod.OpenAISpeechTTS._remote_cache.clear()


def test_resolve_priority_video_project_global():
    from reviewtrans.core.config import AppSettings, ProviderProfile
    from reviewtrans.core.resolve import resolve

    g = ProviderProfile(id="g", name="G", kind="google")
    o = ProviderProfile(id="o", name="O", kind="openai", model="m-profile")
    a = ProviderProfile(id="a", name="A", kind="anthropic")
    settings = AppSettings(translate_profiles=[g, o, a], default_translate_profile="o")
    project = Project()
    doc = VideoDoc()
    r = resolve(settings, "translate", project, doc)
    assert (r.profile.id, r.profile_source, r.value) == ("o", "global", "")
    project.translate_model = "m-project"  # model ở project áp cho provider mặc định
    r = resolve(settings, "translate", project, doc)
    assert (r.profile.id, r.value, r.value_source) == ("o", "m-project", "project")
    project.translate_profile = "g"
    assert resolve(settings, "translate", project, doc).profile.id == "g"
    doc.translate_profile = "a"  # video chọn provider khác → model của project không áp sang
    r = resolve(settings, "translate", project, doc)
    assert (r.profile.id, r.profile_source, r.value) == ("a", "video", "")
    doc.translate_model = "claude-x"
    assert resolve(settings, "translate", project, doc).value == "claude-x"
    doc.translate_profile = "missing"  # profile đã bị xoá → rơi về cấp trên
    assert resolve(settings, "translate", project, doc).profile.id == "g"
