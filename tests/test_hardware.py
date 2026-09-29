from __future__ import annotations

import pytest

from reviewtrans.core import hardware
from reviewtrans.core.config import SettingsStore, upgrade_settings, whisper_thread_count
from reviewtrans.core.hardware import EncoderConfig, Gpu, HardwareInfo, quality_args, resolve_encoder, whisper_plan
from reviewtrans.core.paths import find_tool
from reviewtrans.core.pipeline import RunContext
from reviewtrans.core.pipeline import asr as asr_mod
from reviewtrans.core.pipeline import render as render_mod
from reviewtrans.core.proc import MediaInfo, probe, run_checked
from reviewtrans.core.store import ProjectStore

needs_ffmpeg = pytest.mark.skipif(not find_tool("ffmpeg"), reason="cần ffmpeg trong bin/")


@pytest.fixture(autouse=True)
def _fresh_hardware():
    hardware.forget()
    yield
    hardware.forget()


def test_quality_args_per_vendor():
    assert quality_args("libx264", 20, "fast") == ["-preset", "fast", "-crf", "20"]
    assert "-cq" in quality_args("h264_nvenc", 20) and "20" in quality_args("h264_nvenc", 20)
    assert quality_args("hevc_amf", 22)[-4:] == ["-qp_i", "22", "-qp_p", "22"]
    assert quality_args("h264_qsv", 24)[-2:] == ["-global_quality", "24"]
    mf = quality_args("h264_mf", 20)
    assert mf[:2] == ["-hw_encoding", "1"] and mf[-1] == "70"


def test_resolve_encoder_prefers_gpu_and_falls_back():
    amf = EncoderConfig("h264_amf", tail="format=nv12,hwupload", device="RX")
    info = HardwareInfo(gpus=[Gpu("RX", "amd", 1)], encoders={"h264_amf": amf, "h264_mf": EncoderConfig("h264_mf")},
                        failed={"h264_nvenc": "Cannot load nvcuda.dll"})
    assert resolve_encoder("auto", info)[0].codec == "h264_amf"
    assert resolve_encoder("auto_hevc", info)[0].codec == "libx265"  # không có hevc GPU → CPU
    config, note = resolve_encoder("h264_nvenc", info)
    assert config.codec == "libx264" and "nvcuda" in note
    assert resolve_encoder("libx265", info)[0].codec == "libx265"
    assert resolve_encoder("auto", info, {"h264_amf"})[0].codec == "h264_mf"  # vừa lỗi trong lần xuất này
    assert resolve_encoder("auto", info)[0].codec == "h264_amf"  # lần sau vẫn thử lại
    hardware.mark_failed("h264_amf")  # lỗi khi xuất thật → phiên này bỏ qua
    assert resolve_encoder("auto", info)[0].codec == "h264_mf"


def test_amf_tries_discrete_gpu_first():
    gpus = [Gpu("AMD Radeon(TM) Graphics", "amd", 0, integrated=True), Gpu("Radeon RX 5300M", "amd", 1)]
    configs = hardware._candidates("h264_amf", gpus)
    if not configs:
        pytest.skip("AMF chỉ có trên Windows")
    assert [c.device for c in configs] == ["Radeon RX 5300M", "AMD Radeon(TM) Graphics"]
    assert "d3d11va=rtdx:1" in configs[0].pre_args
    assert hardware._candidates("h264_nvenc", gpus) == []  # không có card NVIDIA thì khỏi thử


def test_cuda_package_choice():
    assert hardware.cuda_package(HardwareInfo(compute_cap="8.6", cuda_version="12.8")) == "11.8.0"  # RTX 30
    assert hardware.cuda_package(HardwareInfo(compute_cap="8.9", cuda_version="12.6")) == "12.4.0"  # RTX 40
    assert hardware.cuda_package(HardwareInfo(compute_cap="12.0", cuda_version="12.2")) == "11.8.0"
    assert hardware.cuda_package(None) == "11.8.0"


def test_settings_upgrade_to_auto(home):
    store = SettingsStore()
    s = store.settings
    s.render.video_codec, s.whisper_threads = "libx264", 4
    upgrade_settings(s, 1)
    assert s.render.video_codec == "auto" and s.whisper_threads == 0 and s.settings_version == 2
    s.render.video_codec = "libx264"  # người dùng tự chọn lại CPU → giữ nguyên
    upgrade_settings(s, 2)
    assert s.render.video_codec == "libx264"
    assert whisper_thread_count(0) >= 1 and whisper_thread_count(6) == 6


def _fake_whisper(folder, name="whisper-cli.exe"):
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / name
    exe.write_bytes(b"")
    return exe


def test_whisper_plan_order(tmp_path, home, monkeypatch):
    extra = tmp_path / "extra"
    vulkan = _fake_whisper(extra / "whisper-vulkan")
    cuda = _fake_whisper(extra / "whisper-cuda")
    cpu = _fake_whisper(extra)
    monkeypatch.setattr(hardware, "bin_dirs", lambda: [extra])
    monkeypatch.setattr(hardware, "find_tool", lambda name: cpu if name == "whisper" else None)
    amd = HardwareInfo(gpus=[Gpu("RX", "amd", 1)])
    nvidia = HardwareInfo(gpus=[Gpu("RTX 3060", "nvidia", 0)], nvidia_driver="560")
    assert [(n, p) for n, p, _ in whisper_plan("auto", amd)] == [("vulkan", vulkan), ("cpu", cpu)]
    assert [(n, p) for n, p, _ in whisper_plan("auto", nvidia)] == [("cuda", cuda), ("vulkan", vulkan), ("cpu", cpu)]
    assert [n for n, _, _ in whisper_plan("cpu", nvidia)] == ["cpu"]
    assert [n for n, _, _ in whisper_plan("auto", HardwareInfo())] == ["cpu"]  # không có GPU
    assert [n for n, _, _ in whisper_plan("cuda", amd)] == ["cuda", "cpu"]
    # chỉ có bản GPU: dự phòng bằng chính nó với -ng
    monkeypatch.setattr(hardware, "find_tool", lambda name: None)
    plan = whisper_plan("auto", amd)
    assert plan[-1] == ("cpu", vulkan, ["-ng"])


def _ctx(tmp_path):
    settings = SettingsStore().settings
    store, project = ProjectStore.create(tmp_path / "projects", "HW")
    logs: list[str] = []
    return RunContext(store=store, project=project, settings=settings, log=logs.append), logs


def test_asr_falls_back_to_cpu(tmp_path, home, monkeypatch):
    ctx, logs = _ctx(tmp_path)
    source = tmp_path / "a.mp4"
    source.write_bytes(b"x")
    info = MediaInfo(duration=5.0, width=640, height=360, fps=25.0, has_audio=True)
    doc = ctx.store.add_video(ctx.project, str(source), info)
    gpu, cpu = tmp_path / "gpu.exe", tmp_path / "cpu.exe"
    monkeypatch.setattr(asr_mod, "whisper_plan", lambda device, info: [("vulkan", gpu, []), ("cpu", cpu, [])])
    monkeypatch.setattr(asr_mod, "ensure_whisper_model", lambda *a, **k: tmp_path / "model.bin")
    monkeypatch.setattr(asr_mod, "extract_audio", lambda *a: tmp_path / "audio.wav")
    calls = []

    def fake_run(command, log=None, stop_event=None, line_cb=None, quiet=False, cwd=None):
        calls.append(command[0])
        if command[0] == str(gpu):
            line_cb("ggml_vulkan: 0 = Radeon RX 5300M (AMD proprietary driver)")
            return 0xC0000005  # crash của driver
        out = command[command.index("-of") + 1]
        with open(out + ".srt", "w", encoding="utf-8") as handle:
            handle.write("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n")
        return 0

    monkeypatch.setattr(asr_mod, "run", fake_run)
    segments = asr_mod.run_asr(ctx, doc)
    assert [s.source for s in segments] == ["xin chào"]
    assert calls == [str(gpu), str(cpu)]
    assert any("Radeon RX 5300M" in line for line in logs)
    assert any("thử bản CPU" in line for line in logs)


def _video(ctx, tmp_path):
    source = tmp_path / "src.mp4"
    run_checked(
        [str(find_tool("ffmpeg")), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
        "tạo video test",
    )
    return ctx.store.add_video(ctx.project, str(source), probe(source))


@needs_ffmpeg
def test_render_gpu_failure_retries_on_cpu(tmp_path, home, monkeypatch):
    ctx, logs = _ctx(tmp_path)
    doc = _video(ctx, tmp_path)
    # cấu hình chắc chắn lỗi trên mọi máy (GPU số 99)
    broken = EncoderConfig("h264_nvenc", args=["-gpu", "99"], device="giả")
    real_choose = render_mod.choose_encoder
    calls = []

    def fake_choose(c, exclude=None):
        calls.append(set(exclude or ()))
        if len(calls) == 1:
            return broken, False
        c.settings.render.video_codec = "libx264"
        return real_choose(c, exclude)

    monkeypatch.setattr(render_mod, "choose_encoder", fake_choose)
    out = render_mod.run_render(ctx, doc, [], None)
    info = probe(out)
    assert info.has_video and abs(info.duration - 3) < 0.5
    assert any("xuất lại bằng libx264" in line for line in logs), logs[-10:]
    assert "h264_nvenc" in calls[-1]


@needs_ffmpeg
def test_render_with_detected_gpu(tmp_path, home):
    info = hardware.detect(force=True)
    if not any(c.startswith("h264") for c in info.encoders):
        pytest.skip("máy này không có bộ mã hoá H.264 phần cứng")
    ctx, logs = _ctx(tmp_path)
    ctx.settings.render.video_codec = "auto"
    doc = _video(ctx, tmp_path)
    out = render_mod.run_render(ctx, doc, [], None)
    result = probe(out)
    assert result.has_video and abs(result.duration - 3) < 0.5
    assert any("Mã hoá bằng GPU" in line for line in logs), logs[-10:]
    assert not any("xuất lại" in line for line in logs)
