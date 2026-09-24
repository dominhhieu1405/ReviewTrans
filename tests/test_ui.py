from __future__ import annotations

import os
from pathlib import Path

import pytest

from reviewtrans.core.models import Layer, Segment
from reviewtrans.core.paths import find_tool
from reviewtrans.core.proc import probe, run_checked

needs_ffmpeg = pytest.mark.skipif(not find_tool("ffmpeg"), reason="cần ffmpeg trong bin/")
SHOTS = os.environ.get("REVIEWTRANS_SHOTS")


def _wait(app, ms: int) -> None:
    from PyQt6 import QtCore

    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


@needs_ffmpeg
def test_main_window_smoke(qapp, home, tmp_path):
    from reviewtrans.core.config import SettingsStore
    from reviewtrans.ui.main_window import MainWindow
    from reviewtrans.ui.state import AppState
    from reviewtrans.ui.theme import apply_theme

    apply_theme(qapp)
    source = tmp_path / "Tap 01.mp4"
    run_checked(
        [str(find_tool("ffmpeg")), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=8",
         "-f", "lavfi", "-i", "sine=frequency=300:duration=8",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
        "tạo video test",
    )
    state = AppState(SettingsStore())
    window = MainWindow(state)
    window.resize(1600, 950)
    if SHOTS:
        from PyQt6 import QtCore

        window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen)
    window.show()
    _wait(qapp, 200)

    state.create_project(tmp_path / "projects", "Phim thử")
    assert state.project is not None
    doc = state.store.add_video(state.project, str(source), probe(source))
    state.store.save_segments(doc.id, [
        Segment(id=1, start=0.5, end=2.5, source="你来了", text="Ngươi đến rồi à"),
        Segment(id=2, start=3.0, end=5.0, source="我等你很久了", text="Ta đợi ngươi lâu lắm rồi"),
        Segment(id=3, start=5.5, end=7.5, source="走吧", text=""),
    ])
    doc = state.store.load_video(doc.id)
    doc.layers = [
        Layer(type="blur", name="Che sub gốc", x=0, y=0.8, w=1, h=0.14),
        Layer(type="text", name="Tiêu đề", text="TẬP 1", x=0.35, y=0.04, w=0.3, h=0.1, bg_enabled=True),
    ]
    doc.style.bg_enabled = True
    state.store.save_video(doc)
    state.reload_project()
    state.openVideoRequested.emit(doc.id)
    _wait(qapp, 600)

    editor = window.pages["editor"]
    assert editor.doc is not None and editor.doc.id == doc.id
    # project mới kế thừa provider mặc định; video có thể ghi đè riêng (video > project > mặc định)
    assert state.project.translate_profile == "" and state.project.tts_profile == ""
    field = editor.translate_field
    assert field.profile.value() == ""
    other = state.settings.translate_profiles[1]
    field.profile.set_value(other.id)
    assert editor.doc.translate_profile == other.id
    editor.save_now()
    assert state.store.load_video(doc.id).translate_profile == other.id
    assert other.name in editor.provider_badge.text() and "từ video" in editor.provider_badge.toolTip()
    field.profile.set_value("")
    assert editor.doc.translate_profile == ""
    editor.seek(3.5)
    _wait(qapp, 300)
    assert editor.canvas.current_segment().id == 2

    # sửa bản dịch → đánh dấu cần lồng tiếng lại và tự lưu
    seg = editor.segments[0]
    model = editor.table.model_
    model.setData(model.index(0, 4), "Ngươi tới rồi")
    assert seg.text == "Ngươi tới rồi" and editor.dirty
    editor.save_now()
    assert state.store.load_segments(doc.id)[0].text == "Ngươi tới rồi"

    # thêm layer qua panel và kéo trên canvas
    editor.layer_panel.add_bottom_blur()
    assert len(editor.doc.layers) == 3
    editor.segment_action("split", [2])
    assert len(editor.segments) == 4

    if SHOTS:
        out = Path(SHOTS)
        out.mkdir(parents=True, exist_ok=True)
        for key in window.pages:
            window.navigate(key)
            _wait(qapp, 250)
            window.grab().save(str(out / f"page_{key}.png"))
        window.navigate("editor")
        editor.inspector.setCurrentIndex(1)
        _wait(qapp, 200)
        window.grab().save(str(out / "page_editor_style.png"))
        editor.inspector.setCurrentIndex(2)
        editor._select_layer(editor.doc.layers[1].id)
        _wait(qapp, 200)
        window.grab().save(str(out / "page_editor_layer.png"))
        editor.inspector.setCurrentWidget(editor.provider_tab)
        _wait(qapp, 200)
        window.grab().save(str(out / "page_editor_provider.png"))
        window.navigate("projects")
        window.pages["projects"].tabs.setCurrentIndex(2)
        _wait(qapp, 200)
        window.grab().save(str(out / "page_project_settings.png"))

    window.close()
    _wait(qapp, 100)


@needs_ffmpeg
def test_job_queue_runs_pipeline(qapp, home, tmp_path):
    import time

    from reviewtrans.core.config import SettingsStore
    from reviewtrans.ui.jobs import DONE
    from reviewtrans.ui.state import AppState

    ffmpeg = str(find_tool("ffmpeg"))
    source = tmp_path / "ep.mp4"
    run_checked(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=4",
         "-f", "lavfi", "-i", "sine=frequency=300:duration=4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
        "tạo video test",
    )
    state = AppState(SettingsStore())
    state.create_project(tmp_path / "projects", "Q")
    doc = state.store.add_video(state.project, str(source), probe(source))
    tts = state.store.tts_dir(doc.id) / "seg_1.wav"
    run_checked([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=700:duration=1",
                 "-ac", "1", "-ar", "44100", str(tts)], "tts giả")
    state.store.save_segments(doc.id, [Segment(id=1, start=0.5, end=1.5, source="a", text="b",
                                               tts_file="seg_1.wav", tts_duration=1.0)])
    changed: list[str] = []
    state.videoChanged.connect(changed.append)
    jobs = state.enqueue([doc.id], ["mix", "render"])
    assert state.is_video_locked(doc.id)
    deadline = time.time() + 120
    while jobs[0].state not in (DONE, "error", "stopped") and time.time() < deadline:
        _wait(qapp, 100)
    assert jobs[0].state == DONE, "\n".join(jobs[0].logs[-30:])
    assert not state.is_video_locked(doc.id)
    saved = state.store.load_video(doc.id)
    assert saved.stage("render") == "done" and Path(saved.last_output).exists()
    assert changed and changed[-1] == doc.id


def _mean_volume(path) -> float:
    import re
    import subprocess

    out = subprocess.run(
        [str(find_tool("ffmpeg")), "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    match = re.search(r"mean_volume: (-?[\d.]+)", out)
    return float(match.group(1)) if match else -200.0


@needs_ffmpeg
def test_preview_remix_follows_original_volume(qapp, home, tmp_path, monkeypatch):
    import time

    from reviewtrans.ui.pages import editor as editor_module

    monkeypatch.setattr(editor_module, "open_path", lambda _p: None)

    from reviewtrans.core.config import SettingsStore
    from reviewtrans.ui.main_window import MainWindow
    from reviewtrans.ui.state import AppState

    source = tmp_path / "ep.mp4"
    run_checked(
        [str(find_tool("ffmpeg")), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=4",
         "-f", "lavfi", "-i", "sine=frequency=300:duration=4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
        "tạo video test",
    )
    state = AppState(SettingsStore())
    window = MainWindow(state)
    state.create_project(tmp_path / "projects", "Vol")
    doc = state.store.add_video(state.project, str(source), probe(source))
    state.reload_project()
    state.openVideoRequested.emit(doc.id)
    editor = window.pages["editor"]

    def wait_preview(previous):
        deadline = time.time() + 60
        while time.time() < deadline:
            _wait(qapp, 100)
            path = editor._preview_path
            if path is not None and path != previous and not editor._remix_running and not editor.remix_timer.isActive():
                return path
        raise AssertionError("bản nghe thử không được trộn lại")

    loud = wait_preview(None)
    loud_level = _mean_volume(loud)
    assert loud_level > -40

    editor.audio_panel.original_mode.set_value("mute")  # người dùng tắt âm gốc
    quiet = wait_preview(loud)
    assert _mean_volume(quiet) < -80
    assert editor._override_signature == str(quiet)  # player đã chuyển sang bản mới

    editor.audio_panel.original_mode.set_value("keep")
    editor.audio_panel.original_volume.setValue(20)
    softer = wait_preview(quiet)
    assert -80 < _mean_volume(softer) < loud_level - 10

    # Xuất thử luôn trộn theo thiết lập hiện tại
    editor.audio_panel.original_mode.set_value("mute")
    editor.export_preview()
    deadline = time.time() + 90
    out = state.store.output_dir(state.project) / "ep_thu.mp4"
    while time.time() < deadline and not out.exists():
        _wait(qapp, 200)
    _wait(qapp, 1500)
    assert out.exists() and _mean_volume(out) < -80
    window.close()
    _wait(qapp, 100)
