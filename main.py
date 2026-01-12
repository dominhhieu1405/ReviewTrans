from __future__ import annotations

import asyncio
import importlib
import importlib.util
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests
from PyQt6 import QtCore, QtGui, QtWidgets

from config_manager import AppConfig, ConfigManager
from utils import escape_ffmpeg_path, generate_cache_key


APP_NAME = "Video Translation Studio"
WHISPER_REPO = "ggerganov/whisper.cpp"
SUPPORTED_VIDEO_EXTENSIONS = "*.mp4 *.mkv *.avi *.mov"

OPENAI_MODELS = ["gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"]
GEMINI_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
]


def optional_import(name: str):
    if importlib.util.find_spec(name) is None:
        return None
    return importlib.import_module(name)


openai = optional_import("openai")
edge_tts = optional_import("edge_tts")
try:
    from google import genai
except ImportError:
    genai = None


def get_resource_path(relative_path: str) -> Path:
    if getattr(sys, "frozen", False):
        base_path = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base_path = Path(__file__).resolve().parent
    return base_path / relative_path


def find_tool(tool_name: str) -> Path | None:
    candidates = []
    tool_dirs = [get_resource_path("bin"), get_resource_path("tools")]
    if platform.system().lower() == "windows":
        candidates.append(f"{tool_name}.exe")
    candidates.append(tool_name)

    for tool_dir in tool_dirs:
        for candidate in candidates:
            tool_path = tool_dir / candidate
            if tool_path.exists():
                return tool_path
    return None


def download_ffmpeg(os_name: str, log_cb=None) -> Path:
    bin_dir = get_resource_path("bin")
    bin_dir.mkdir(parents=True, exist_ok=True)
    if os_name == "windows":
        url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        expected = ["ffmpeg.exe", "ffprobe.exe"]
    elif os_name == "darwin":
        url = "https://evermeet.cx/ffmpeg/getrelease/zip"
        expected = ["ffmpeg", "ffprobe"]
    else:
        url = (
            "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
            "ffmpeg-master-latest-linux64-gpl.zip"
        )
        expected = ["ffmpeg", "ffprobe"]

    temp_dir = Path(tempfile.mkdtemp(prefix="ffmpeg_dl_"))
    archive_path = temp_dir / Path(url).name
    if log_cb:
        log_cb(f"Downloading FFmpeg from {url}")
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with open(archive_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)

    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(temp_dir)
    for binary in expected:
        matches = list(temp_dir.rglob(binary))
        if matches:
            shutil.copy2(matches[0], bin_dir / binary)

    for binary in expected:
        target = bin_dir / binary
        if not target.exists():
            raise FileNotFoundError(f"Missing {binary} after extraction.")
        if os_name != "windows":
            target.chmod(0o755)
    return bin_dir


def run_subprocess(command: list[str], log_cb=None) -> int:
    if log_cb:
        log_cb(f"Running: {' '.join(command)}")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if process.stdout:
        for line in process.stdout:
            if log_cb:
                log_cb(line.rstrip())
    return process.wait()


def get_audio_duration(path: Path, ffprobe_path: Path) -> float:
    command = [
        str(ffprobe_path),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    try:
        return float(process.stdout.strip())
    except ValueError:
        return 0.0


def get_video_duration(path: Path, ffprobe_path: Path) -> float:
    command = [
        str(ffprobe_path),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    try:
        return float(process.stdout.strip())
    except ValueError:
        return 0.0


def fetch_whisper_models(log_cb=None) -> list[str]:
    url = f"https://huggingface.co/api/models/{WHISPER_REPO}"
    if log_cb:
        log_cb("Fetching Whisper models from Hugging Face...")
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    data = response.json()
    models = []
    for sibling in data.get("siblings", []):
        filename = sibling.get("rfilename", "")
        if filename.startswith("ggml-") and filename.endswith(".bin"):
            models.append(filename.replace("ggml-", "").replace(".bin", ""))
    return sorted(set(models))


def ensure_whisper_model(model: str, models_dir: Path, log_cb=None) -> Path:
    models_dir.mkdir(parents=True, exist_ok=True)
    model_name = f"ggml-{model}.bin"
    model_path = models_dir / model_name
    if model_path.exists():
        return model_path

    url = f"https://huggingface.co/{WHISPER_REPO}/resolve/main/{model_name}"
    if log_cb:
        log_cb(f"Downloading Whisper model from {url}")
    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with open(model_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    if log_cb:
        log_cb(f"Model downloaded to {model_path}")
    return model_path


def parse_srt(content: str) -> list[dict]:
    entries = []
    blocks = [block.strip() for block in content.split("\n\n") if block.strip()]
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        index = lines[0].strip()
        timestamps = lines[1].strip()
        text = " ".join(line.strip() for line in lines[2:])
        entries.append({"index": index, "timestamps": timestamps, "text": text})
    return entries


def parse_timestamp(timestamp: str) -> float:
    hours, minutes, seconds = timestamp.split(":")
    seconds, millis = seconds.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000.0


def render_srt(entries: list[dict]) -> str:
    blocks = []
    for entry in entries:
        blocks.append(f"{entry['index']}\n{entry['timestamps']}\n{entry['text']}")
    return "\n\n".join(blocks) + "\n"


@dataclass
class AppSettings:
    video_path: str
    source_mode: str
    whisper_model: str
    source_language: str
    srt_path: str
    provider: str
    provider_model: str
    target_language: str
    translate_all: bool
    glossary_instructions: str
    enable_tts: bool
    tts_provider: str
    force_audio_sync: bool
    speed: float
    pitch: float
    enable_subtitles: bool
    font_family: str
    font_size: int
    text_color: str
    border_width: int
    position: str
    mute_original: bool
    duck_audio: int
    blur_fill: bool
    blur_height: int
    blur_mode: str
    cuda: bool
    output_dir: str


class ModelFetchThread(QtCore.QThread):
    models_ready = QtCore.pyqtSignal(list)
    failed = QtCore.pyqtSignal(str)

    def run(self):
        try:
            models = fetch_whisper_models()
            self.models_ready.emit(models)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class DependencyDownloadThread(QtCore.QThread):
    status = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()
    failed = QtCore.pyqtSignal(str)

    def run(self):
        try:
            os_name = platform.system().lower()
            if os_name.startswith("win"):
                os_key = "windows"
            elif os_name.startswith("darwin"):
                os_key = "darwin"
            else:
                os_key = "linux"
            self.status.emit("Downloading essential components (FFmpeg)...")
            download_ffmpeg(os_key)
            self.finished.emit()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class WorkerThread(QtCore.QThread):
    progress = QtCore.pyqtSignal(int)
    status = QtCore.pyqtSignal(str)
    log = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(str)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, settings: AppSettings, config: AppConfig, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.config = config
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        try:
            self._run_pipeline()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))

    def _run_pipeline(self):
        settings = self.settings
        if not settings.video_path:
            raise ValueError("Video path is required.")

        work_dir = Path(tempfile.mkdtemp(prefix="video_trans_"))
        self.log.emit(f"Working directory: {work_dir}")
        cache_dir = get_resource_path("tmp")
        cache_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg_path = find_tool("ffmpeg")
        ffprobe_path = find_tool("ffprobe")
        if not ffmpeg_path or not ffprobe_path:
            raise FileNotFoundError("ffmpeg/ffprobe not found in bin/ or tools/ folder.")

        audio_path = work_dir / "audio.wav"
        self._step(5, "Extracting audio")
        extract_command = [
            str(ffmpeg_path),
            "-y",
            "-i",
            settings.video_path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(audio_path),
        ]
        if settings.cuda:
            extract_command.insert(1, "-hwaccel")
            extract_command.insert(2, "cuda")
        if run_subprocess(extract_command, self.log.emit) != 0:
            raise RuntimeError("FFmpeg audio extraction failed.")

        srt_path = None
        if settings.source_mode == "ASR":
            self._step(20, "Running ASR")
            whisper_path = find_tool("whisper")
            if not whisper_path:
                raise FileNotFoundError("whisper executable not found in bin/ or tools/ folder.")
            video_path = Path(settings.video_path)
            video_mtime = video_path.stat().st_mtime
            asr_key = generate_cache_key(video_path, video_mtime, settings.whisper_model)
            cached_asr = cache_dir / f"asr_{asr_key}.srt"
            if cached_asr.exists():
                self.log.emit("Restored ASR from cache.")
                srt_path = cached_asr
            else:
                models_dir = Path.home() / ".video_translation_studio" / "models"
                model_path = ensure_whisper_model(
                    settings.whisper_model,
                    models_dir,
                    self.log.emit,
                )
                srt_path = work_dir / "transcript.srt"
                whisper_command = [
                    str(whisper_path),
                    "-m",
                    str(model_path),
                    "-f",
                    str(audio_path),
                    "-l",
                    settings.source_language,
                    "-osrt",
                    "-of",
                    str(work_dir / "transcript"),
                ]
                if run_subprocess(whisper_command, self.log.emit) != 0:
                    raise RuntimeError("Whisper ASR failed.")
                shutil.copy2(srt_path, cached_asr)
        else:
            if not settings.srt_path:
                raise ValueError("SRT path is required when using SRT mode.")
            srt_path = Path(settings.srt_path)

        self._step(40, "Translating subtitles")
        with open(srt_path, "r", encoding="utf-8", errors="replace") as handle:
            source_content = handle.read()
        entries = parse_srt(source_content)

        trans_key = generate_cache_key(
            source_content,
            settings.target_language,
            settings.provider,
            settings.provider_model,
            settings.glossary_instructions,
        )
        translated_srt = cache_dir / f"trans_{trans_key}.srt"
        if translated_srt.exists():
            self.log.emit("Restored translation from cache.")
            with open(translated_srt, "r", encoding="utf-8", errors="replace") as handle:
                translated_entries = parse_srt(handle.read())
        else:
            translated_entries = self._translate_entries(entries, settings)
            with open(translated_srt, "w", encoding="utf-8", errors="replace") as handle:
                handle.write(render_srt(translated_entries))

        tts_audio = None
        if settings.enable_tts:
            self._step(60, "Generating TTS")
            translated_text = "\n".join(entry["text"] for entry in translated_entries)
            tts_key = generate_cache_key(
                translated_text,
                settings.tts_provider,
                settings.target_language,
                settings.speed,
                settings.pitch,
                settings.force_audio_sync,
            )
            tts_audio = cache_dir / f"dub_{tts_key}.wav"
            if tts_audio.exists():
                self.log.emit("Restored TTS from cache.")
            else:
                video_duration = get_video_duration(Path(settings.video_path), ffprobe_path)
                self._generate_tts(
                    translated_entries,
                    tts_audio,
                    ffmpeg_path,
                    ffprobe_path,
                    video_duration,
                    cache_dir,
                    settings.force_audio_sync,
                )
                self._apply_audio_fx(tts_audio, settings, ffmpeg_path)

        self._step(80, "Rendering output")
        render_command = [
            str(ffmpeg_path),
            "-y",
            "-i",
            settings.video_path,
        ]
        if tts_audio:
            render_command += ["-i", str(tts_audio)]
        video_filter_args = self._build_video_filter_args(translated_srt, settings)
        render_command += video_filter_args

        has_video_map = "-map" in video_filter_args
        if tts_audio:
            if not has_video_map:
                render_command += ["-map", "0:v"]
            render_command += ["-map", "1:a"]
        else:
            if not has_video_map:
                render_command += ["-map", "0:v"]
            render_command += ["-map", "0:a?"]
        if settings.mute_original:
            render_command += ["-c:a", "aac", "-b:a", "192k"]
        else:
            duck_volume = max(0.1, settings.duck_audio / 100.0)
            render_command += ["-filter:a", f"volume={duck_volume}"]

        output_dir = Path(settings.output_dir or work_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{Path(settings.video_path).stem}_translated.mp4"
        render_command.append(str(output_path))
        if run_subprocess(render_command, self.log.emit) != 0:
            raise RuntimeError("Video rendering failed.")

        self._step(100, "Completed")
        self.finished.emit(str(output_path))

    def _step(self, value: int, message: str):
        self.progress.emit(value)
        self.status.emit(message)

    def _translate_entries(self, entries: list[dict], settings: AppSettings) -> list[dict]:
        if settings.provider == "ChatGPT" and openai is None:
            raise RuntimeError("openai package not installed.")
        if settings.provider == "Gemini" and genai is None:
            raise RuntimeError("google-genai package not installed.")

        if settings.translate_all:
            combined_text = "\n".join(entry["text"] for entry in entries)
            translated_text = self._translate_text(combined_text, settings)
            translated_lines = translated_text.splitlines()
            for idx, entry in enumerate(entries):
                entry["text"] = translated_lines[idx] if idx < len(translated_lines) else entry["text"]
        else:
            for entry in entries:
                entry["text"] = self._translate_text(entry["text"], settings)
        return entries

    def _translate_text(self, text: str, settings: AppSettings) -> str:
        glossary = settings.glossary_instructions.strip()
        system_instructions = "You are a professional translator."
        if glossary:
            system_instructions += f"\nAdditional instructions:\n{glossary}"
        prompt = (
            f"Translate the following text to {settings.target_language}.\n"
            "Preserve line breaks and do not add commentary.\n\n"
            f"{text}"
        )
        if settings.provider == "ChatGPT":
            client = openai.OpenAI(api_key=self.config.openai_api_key)
            response = client.chat.completions.create(
                model=settings.provider_model,
                messages=[
                    {"role": "system", "content": system_instructions},
                    {"role": "user", "content": prompt},
                ],
            )
            return response.choices[0].message.content.strip()
        if settings.provider == "Gemini":
            client = genai.Client(api_key=self.config.gemini_api_key)
            response = client.models.generate_content(
                model=settings.provider_model,
                contents=f"{system_instructions}\n\n{prompt}",
            )
            return response.text.strip()
        return text

    def _generate_tts(
        self,
        entries: list[dict],
        output_path: Path,
        ffmpeg_path: Path,
        ffprobe_path: Path,
        video_duration: float,
        cache_dir: Path,
        force_audio_sync: bool,
    ):
        segment_paths = []
        for idx, entry in enumerate(entries, start=1):
            timestamps = entry["timestamps"].split(" --> ")
            if len(timestamps) != 2:
                continue
            start_time = parse_timestamp(timestamps[0])
            end_time = parse_timestamp(timestamps[1])
            max_duration = max(0.1, end_time - start_time)
            raw_path = self._generate_tts_segment(
                entry["text"],
                idx,
                cache_dir,
                ffmpeg_path,
            )
            adjusted_path = raw_path
            if force_audio_sync:
                adjusted_path = self._fit_audio_to_slot(
                    raw_path,
                    max_duration,
                    ffmpeg_path,
                    ffprobe_path,
                )
            delay_ms = int(start_time * 1000)
            segment_paths.append((adjusted_path, delay_ms))

        if not segment_paths:
            raise RuntimeError("No TTS segments generated.")

        self._mix_segments(segment_paths, output_path, ffmpeg_path, video_duration)

    def _generate_tts_segment(
        self,
        text: str,
        index: int,
        cache_dir: Path,
        ffmpeg_path: Path,
    ) -> Path:
        raw_key = generate_cache_key(
            text,
            self.settings.tts_provider,
            self.settings.target_language,
            self.config.custom_tts_url,
            self.config.custom_tts_key,
        )
        output_path = cache_dir / f"tts_raw_{raw_key}_{index}.wav"
        if output_path.exists():
            self.log.emit("Restored TTS segment from cache.")
            return output_path

        temp_output = output_path.with_suffix(".mp3")
        if self.settings.tts_provider == "Edge TTS":
            if edge_tts is None:
                raise RuntimeError("edge-tts package not installed.")

            async def _run():
                communicate = edge_tts.Communicate(text, self.settings.target_language)
                await communicate.save(str(temp_output))

            thread = threading.Thread(target=lambda: asyncio.run(_run()), daemon=True)
            thread.start()
            thread.join()
        else:
            if not self.config.custom_tts_url:
                raise ValueError("Custom API URL is required.")
            payload = {
                "text": text,
                "language": self.settings.target_language,
            }
            headers = {}
            if self.config.custom_tts_key:
                headers["Authorization"] = self.config.custom_tts_key
            response = requests.post(
                self.config.custom_tts_url,
                data=payload,
                headers=headers,
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Custom API failed: {data.get('msg')}")
            audio_url = data.get("data")
            if not audio_url:
                raise RuntimeError("Custom API response missing audio URL.")
            audio_response = requests.get(audio_url, timeout=60)
            audio_response.raise_for_status()
            with open(temp_output, "wb") as handle:
                handle.write(audio_response.content)

        convert_command = [
            str(ffmpeg_path),
            "-y",
            "-i",
            str(temp_output),
            "-ac",
            "1",
            "-ar",
            "44100",
            str(output_path),
        ]
        if run_subprocess(convert_command, self.log.emit) != 0:
            raise RuntimeError("TTS audio conversion failed.")
        if temp_output.exists():
            temp_output.unlink()
        return output_path

    def _build_atempo_chain(self, speed_factor: float) -> str:
        factors = []
        remaining = speed_factor
        while remaining > 2.0:
            factors.append(2.0)
            remaining /= 2.0
        factors.append(max(0.5, min(2.0, remaining)))
        return ",".join(f"atempo={factor}" for factor in factors)

    def _fit_audio_to_slot(
        self,
        file_path: Path,
        max_duration: float,
        ffmpeg_path: Path,
        ffprobe_path: Path,
    ) -> Path:
        current_duration = get_audio_duration(file_path, ffprobe_path)
        if current_duration <= 0:
            return file_path
        speed_factor = current_duration / max_duration if current_duration > max_duration else 1.0
        atempo_chain = self._build_atempo_chain(speed_factor)
        adjusted_path = file_path.with_name(f"{file_path.stem}_fit.wav")
        filter_chain = f"{atempo_chain},apad=pad_dur={max_duration},atrim=0:{max_duration}"
        command = [
            str(ffmpeg_path),
            "-y",
            "-i",
            str(file_path),
            "-filter:a",
            filter_chain,
            str(adjusted_path),
        ]
        if run_subprocess(command, self.log.emit) != 0:
            raise RuntimeError("Audio time-stretch failed.")
        return adjusted_path

    def _mix_segments(
        self,
        segment_paths: list[tuple[Path, int]],
        output_path: Path,
        ffmpeg_path: Path,
        video_duration: float,
    ):
        command = [str(ffmpeg_path), "-y"]
        filter_parts = []
        inputs = []
        for idx, (path, delay_ms) in enumerate(segment_paths):
            command += ["-i", str(path)]
            filter_parts.append(f"[{idx}:a]adelay={delay_ms}|{delay_ms}[a{idx}]")
            inputs.append(f"[a{idx}]")
        if len(inputs) == 1:
            filter_parts.append(f"{inputs[0]}anull[aout]")
        else:
            filter_parts.append(
                f"{''.join(inputs)}amix=inputs={len(inputs)}:normalize=0[aout]"
            )
        filter_complex = ";".join(filter_parts)
        command += [
            "-filter_complex",
            filter_complex,
            "-map",
            "[aout]",
            "-ac",
            "1",
            "-ar",
            "44100",
            str(output_path),
        ]
        if run_subprocess(command, self.log.emit) != 0:
            raise RuntimeError("TTS segment mixing failed.")

        if video_duration > 0:
            trimmed_output = output_path.with_name(f"{output_path.stem}_dur.wav")
            trim_command = [
                str(ffmpeg_path),
                "-y",
                "-i",
                str(output_path),
                "-filter:a",
                f"apad=pad_dur={video_duration},atrim=0:{video_duration}",
                str(trimmed_output),
            ]
            if run_subprocess(trim_command, self.log.emit) != 0:
                raise RuntimeError("Audio duration trim failed.")
            shutil.move(trimmed_output, output_path)

    def _apply_audio_fx(self, audio_path: Path, settings: AppSettings, ffmpeg_path: Path):
        if settings.speed == 1.0 and settings.pitch == 0.0:
            return
        fx_output = audio_path.with_name("tts_audio_fx.wav")
        atempo = max(0.5, min(2.0, settings.speed))
        pitch_ratio = 2 ** (settings.pitch / 12.0)
        filter_chain = f"asetrate=44100*{pitch_ratio},atempo={atempo}"
        command = [
            str(ffmpeg_path),
            "-y",
            "-i",
            str(audio_path),
            "-filter:a",
            filter_chain,
            str(fx_output),
        ]
        if run_subprocess(command, self.log.emit) != 0:
            raise RuntimeError("Audio post-processing failed.")
        shutil.move(fx_output, audio_path)

    def _build_subtitle_filter(self, srt_path: Path, settings: AppSettings) -> str:
        alignment = {"Bottom": "2", "Center": "5", "Top": "8"}.get(settings.position, "2")
        style = (
            f"FontName={settings.font_family},"
            f"FontSize={settings.font_size},"
            f"PrimaryColour=&H{settings.text_color.lstrip('#')},"
            f"Outline={settings.border_width},"
            f"Alignment={alignment}"
        )
        safe_path = escape_ffmpeg_path(str(srt_path))
        return f"subtitles='{safe_path}':force_style='{style}'"

    def _build_blur_filter(self, settings: AppSettings) -> str:
        height_ratio = max(1, min(100, settings.blur_height)) / 100.0
        if settings.blur_mode == "Blur":
            return (
                "boxblur=luma_radius=10:luma_power=1:"
                "chroma_radius=10:chroma_power=1"
            )
        color = "black"
        return f"drawbox=y=ih*{1 - height_ratio}:h=ih*{height_ratio}:color={color}:t=fill"

    def _build_video_filter_args(self, srt_path: Path, settings: AppSettings) -> list[str]:
        if not settings.blur_fill:
            if settings.enable_subtitles:
                return ["-vf", self._build_subtitle_filter(srt_path, settings)]
            return []

        height_ratio = max(1, min(100, settings.blur_height)) / 100.0
        crop_h = f"ih*{height_ratio}"
        crop_y = f"ih-{crop_h}"
        blur_filter = self._build_blur_filter(settings)
        filter_parts = [
            f"[0:v]crop=iw:{crop_h}:0:{crop_y}[bottom]",
            f"[bottom]{blur_filter}[blurred]",
            "[0:v][blurred]overlay=0:main_h-overlay_h[bg_processed]",
        ]
        if settings.enable_subtitles:
            subtitle_filter = self._build_subtitle_filter(srt_path, settings)
            filter_parts.append(f"[bg_processed]{subtitle_filter}[vout]")
        else:
            filter_parts.append("[bg_processed]null[vout]")
        filter_complex = ";".join(filter_parts)
        return ["-filter_complex", filter_complex, "-map", "[vout]"]


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(500, 400)
        self.config_manager = config_manager
        self.config = config_manager.config

        layout = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget()

        general_tab = QtWidgets.QWidget()
        general_layout = QtWidgets.QFormLayout(general_tab)
        self.openai_key_edit = QtWidgets.QLineEdit(self.config.openai_api_key)
        self.openai_key_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.gemini_key_edit = QtWidgets.QLineEdit(self.config.gemini_api_key)
        self.gemini_key_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.custom_tts_key_edit = QtWidgets.QLineEdit(self.config.custom_tts_key)
        self.custom_tts_key_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.output_dir_edit = QtWidgets.QLineEdit(self.config.default_output_dir)
        output_button = QtWidgets.QPushButton("Browse")
        output_button.clicked.connect(self._browse_output_dir)
        output_layout = QtWidgets.QHBoxLayout()
        output_layout.addWidget(self.output_dir_edit)
        output_layout.addWidget(output_button)

        general_layout.addRow("OpenAI API Key:", self.openai_key_edit)
        general_layout.addRow("Gemini API Key:", self.gemini_key_edit)
        general_layout.addRow("Custom TTS Key:", self.custom_tts_key_edit)
        general_layout.addRow("Default Output Folder:", output_layout)

        tts_tab = QtWidgets.QWidget()
        tts_layout = QtWidgets.QFormLayout(tts_tab)
        self.custom_tts_url_edit = QtWidgets.QLineEdit(self.config.custom_tts_url)
        self.custom_tts_url_edit.setPlaceholderText("https://example.com/tts")
        tts_layout.addRow("Custom TTS API URL:", self.custom_tts_url_edit)

        tabs.addTab(general_tab, "General")
        tabs.addTab(tts_tab, "TTS / Dubbing")
        layout.addWidget(tabs)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._save)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _browse_output_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if path:
            self.output_dir_edit.setText(path)

    def _save(self):
        self.config.openai_api_key = self.openai_key_edit.text().strip()
        self.config.gemini_api_key = self.gemini_key_edit.text().strip()
        self.config.custom_tts_key = self.custom_tts_key_edit.text().strip()
        self.config.custom_tts_url = self.custom_tts_url_edit.text().strip()
        self.config.default_output_dir = self.output_dir_edit.text().strip()
        self.config_manager.save()
        self.accept()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1300, 750)
        self.config_manager = config_manager
        self.config = config_manager.config
        self.worker = None
        self.model_fetch_thread = None

        self._build_menu()
        self._apply_theme()

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QHBoxLayout(central)

        column_layout = QtWidgets.QHBoxLayout()
        main_layout.addLayout(column_layout)

        column_layout.addWidget(self._build_input_column())
        column_layout.addWidget(self._build_processing_column())
        column_layout.addWidget(self._build_output_column())

        self._update_source_mode()
        self._update_provider_models()
        self._load_defaults()
        self._fetch_models_async()

    def _build_menu(self):
        menu = self.menuBar().addMenu("Settings")
        settings_action = QtGui.QAction("Preferences", self)
        settings_action.triggered.connect(self._open_settings)
        menu.addAction(settings_action)

    def _apply_theme(self):
        palette = QtGui.QPalette()
        palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#1e1e1e"))
        palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor("#f0f0f0"))
        palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#2b2b2b"))
        palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor("#3a3a3a"))
        palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor("#f0f0f0"))
        palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor("#3c3c3c"))
        palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor("#f0f0f0"))
        palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor("#0078d4"))
        palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor("#ffffff"))
        self.setPalette(palette)

    def _build_input_column(self):
        group = QtWidgets.QGroupBox("Column 1: Input")
        layout = QtWidgets.QVBoxLayout(group)

        self.video_path_edit = QtWidgets.QLineEdit()
        browse_button = QtWidgets.QPushButton("Browse Video")
        browse_button.clicked.connect(self._browse_video)

        video_layout = QtWidgets.QHBoxLayout()
        video_layout.addWidget(self.video_path_edit)
        video_layout.addWidget(browse_button)

        source_group = QtWidgets.QGroupBox("Source Audio")
        source_layout = QtWidgets.QGridLayout(source_group)

        self.asr_radio = QtWidgets.QRadioButton("ASR (Automatic Speech Recognition)")
        self.srt_radio = QtWidgets.QRadioButton("SRT File")
        self.asr_radio.setChecked(True)
        self.asr_radio.toggled.connect(self._update_source_mode)

        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.setPlaceholderText("Loading models...")
        self.source_lang_combo = QtWidgets.QComboBox()
        self.source_lang_combo.addItems(["auto", "en", "vi", "zh", "ja", "ko", "fr", "de", "es"])

        self.srt_path_edit = QtWidgets.QLineEdit()
        srt_button = QtWidgets.QPushButton("Browse SRT")
        srt_button.clicked.connect(self._browse_srt)

        source_layout.addWidget(self.asr_radio, 0, 0, 1, 2)
        source_layout.addWidget(QtWidgets.QLabel("Whisper Model:"), 1, 0)
        source_layout.addWidget(self.model_combo, 1, 1)
        source_layout.addWidget(QtWidgets.QLabel("Source Language:"), 2, 0)
        source_layout.addWidget(self.source_lang_combo, 2, 1)
        source_layout.addWidget(self.srt_radio, 3, 0, 1, 2)
        source_layout.addWidget(self.srt_path_edit, 4, 0)
        source_layout.addWidget(srt_button, 4, 1)

        layout.addWidget(QtWidgets.QLabel("Video File"))
        layout.addLayout(video_layout)
        layout.addWidget(source_group)
        layout.addStretch()
        return group

    def _build_processing_column(self):
        group = QtWidgets.QGroupBox("Column 2: Processing")
        layout = QtWidgets.QVBoxLayout(group)

        translation_group = QtWidgets.QGroupBox("Translation")
        translation_layout = QtWidgets.QGridLayout(translation_group)

        self.provider_combo = QtWidgets.QComboBox()
        self.provider_combo.addItems(["ChatGPT", "Gemini"])
        self.provider_combo.currentTextChanged.connect(self._update_provider_models)
        self.model_combo_provider = QtWidgets.QComboBox()
        self.target_lang_combo = QtWidgets.QComboBox()
        self.target_lang_combo.addItems(["en", "vi", "zh-cn", "ja", "ko", "fr", "de", "es"])
        self.translate_all_checkbox = QtWidgets.QCheckBox("Translate All at Once")

        translation_layout.addWidget(QtWidgets.QLabel("Provider:"), 0, 0)
        translation_layout.addWidget(self.provider_combo, 0, 1)
        translation_layout.addWidget(QtWidgets.QLabel("Model:"), 1, 0)
        translation_layout.addWidget(self.model_combo_provider, 1, 1)
        translation_layout.addWidget(QtWidgets.QLabel("Target Language:"), 2, 0)
        translation_layout.addWidget(self.target_lang_combo, 2, 1)
        translation_layout.addWidget(self.translate_all_checkbox, 3, 0, 1, 2)

        glossary_group = QtWidgets.QGroupBox("Glossary / Instructions")
        glossary_layout = QtWidgets.QVBoxLayout(glossary_group)
        self.glossary_text = QtWidgets.QTextEdit()
        self.glossary_text.setPlaceholderText(
            "Enter translation rules or instructions here (free-form)."
        )
        glossary_layout.addWidget(self.glossary_text)

        tts_group = QtWidgets.QGroupBox("Dubbing (TTS)")
        tts_layout = QtWidgets.QGridLayout(tts_group)
        self.tts_enable_checkbox = QtWidgets.QCheckBox("Enable TTS")
        self.tts_provider_combo = QtWidgets.QComboBox()
        self.tts_provider_combo.addItems(["Edge TTS", "Custom API"])
        self.force_sync_checkbox = QtWidgets.QCheckBox("Force Audio Sync (Fit to Slot)")
        self.force_sync_checkbox.setChecked(True)
        self.force_sync_checkbox.setToolTip(
            "If checked, audio speed will be adjusted to fit strictly within the subtitle "
            "timeline. If unchecked, audio plays at natural speed (may overlap with next "
            "sentence)."
        )
        self.speed_input = QtWidgets.QDoubleSpinBox()
        self.speed_input.setRange(0.5, 2.0)
        self.speed_input.setSingleStep(0.1)
        self.speed_input.setValue(1.0)
        self.pitch_input = QtWidgets.QDoubleSpinBox()
        self.pitch_input.setRange(-12.0, 12.0)
        self.pitch_input.setSingleStep(0.5)
        self.pitch_input.setValue(0.0)

        tts_layout.addWidget(self.tts_enable_checkbox, 0, 0, 1, 2)
        tts_layout.addWidget(QtWidgets.QLabel("Provider:"), 1, 0)
        tts_layout.addWidget(self.tts_provider_combo, 1, 1)
        tts_layout.addWidget(self.force_sync_checkbox, 2, 0, 1, 2)
        tts_layout.addWidget(QtWidgets.QLabel("Speed:"), 3, 0)
        tts_layout.addWidget(self.speed_input, 3, 1)
        tts_layout.addWidget(QtWidgets.QLabel("Pitch:"), 4, 0)
        tts_layout.addWidget(self.pitch_input, 4, 1)

        layout.addWidget(translation_group)
        layout.addWidget(glossary_group)
        layout.addWidget(tts_group)
        layout.addStretch()
        return group

    def _build_output_column(self):
        group = QtWidgets.QGroupBox("Column 3: Output / Preview")
        layout = QtWidgets.QVBoxLayout(group)

        subtitle_group = QtWidgets.QGroupBox("Subtitles")
        subtitle_layout = QtWidgets.QGridLayout(subtitle_group)
        self.subtitle_enable_checkbox = QtWidgets.QCheckBox("Enable Subtitles")
        self.font_family_combo = QtWidgets.QComboBox()
        self.font_family_combo.addItems(QtGui.QFontDatabase.families())
        self.font_size_spin = QtWidgets.QSpinBox()
        self.font_size_spin.setRange(8, 72)
        self.font_size_spin.setValue(24)
        self.text_color_button = QtWidgets.QPushButton("Select Color")
        self.text_color_button.clicked.connect(self._select_color)
        self.text_color_display = QtWidgets.QLineEdit("#FFFFFF")
        self.border_width_spin = QtWidgets.QSpinBox()
        self.border_width_spin.setRange(0, 10)
        self.border_width_spin.setValue(2)
        self.position_combo = QtWidgets.QComboBox()
        self.position_combo.addItems(["Bottom", "Center", "Top"])

        subtitle_layout.addWidget(self.subtitle_enable_checkbox, 0, 0, 1, 2)
        subtitle_layout.addWidget(QtWidgets.QLabel("Font Family:"), 1, 0)
        subtitle_layout.addWidget(self.font_family_combo, 1, 1)
        subtitle_layout.addWidget(QtWidgets.QLabel("Font Size:"), 2, 0)
        subtitle_layout.addWidget(self.font_size_spin, 2, 1)
        subtitle_layout.addWidget(QtWidgets.QLabel("Text Color:"), 3, 0)
        subtitle_layout.addWidget(self.text_color_button, 3, 1)
        subtitle_layout.addWidget(self.text_color_display, 4, 0, 1, 2)
        subtitle_layout.addWidget(QtWidgets.QLabel("Border Width:"), 5, 0)
        subtitle_layout.addWidget(self.border_width_spin, 5, 1)
        subtitle_layout.addWidget(QtWidgets.QLabel("Position:"), 6, 0)
        subtitle_layout.addWidget(self.position_combo, 6, 1)

        post_group = QtWidgets.QGroupBox("Post-Processing")
        post_layout = QtWidgets.QGridLayout(post_group)
        self.mute_radio = QtWidgets.QRadioButton("Mute Original")
        self.duck_radio = QtWidgets.QRadioButton("Duck Audio")
        self.mute_radio.setChecked(True)
        self.duck_input = QtWidgets.QSpinBox()
        self.duck_input.setRange(0, 100)
        self.duck_input.setValue(30)
        self.blur_checkbox = QtWidgets.QCheckBox("Blur/Fill Bottom")
        self.blur_height_spin = QtWidgets.QSpinBox()
        self.blur_height_spin.setRange(5, 50)
        self.blur_height_spin.setValue(20)
        self.blur_mode_combo = QtWidgets.QComboBox()
        self.blur_mode_combo.addItems(["Blur", "Solid Color Fill"])
        self.cuda_checkbox = QtWidgets.QCheckBox("Enable CUDA Acceleration")

        post_layout.addWidget(self.mute_radio, 0, 0)
        post_layout.addWidget(self.duck_radio, 0, 1)
        post_layout.addWidget(QtWidgets.QLabel("Duck Volume:"), 1, 0)
        post_layout.addWidget(self.duck_input, 1, 1)
        post_layout.addWidget(self.blur_checkbox, 2, 0)
        post_layout.addWidget(QtWidgets.QLabel("Height %:"), 3, 0)
        post_layout.addWidget(self.blur_height_spin, 3, 1)
        post_layout.addWidget(self.blur_mode_combo, 4, 0, 1, 2)
        post_layout.addWidget(self.cuda_checkbox, 5, 0, 1, 2)

        execution_group = QtWidgets.QGroupBox("Execution & Status")
        execution_layout = QtWidgets.QVBoxLayout(execution_group)
        self.start_button = QtWidgets.QPushButton("Start")
        self.start_button.clicked.connect(self._start_processing)
        self.progress_bar = QtWidgets.QProgressBar()
        self.status_label = QtWidgets.QLabel("Idle")
        self.log_console = QtWidgets.QTextEdit()
        self.log_console.setReadOnly(True)

        execution_layout.addWidget(self.start_button)
        execution_layout.addWidget(self.progress_bar)
        execution_layout.addWidget(self.status_label)
        execution_layout.addWidget(self.log_console)

        layout.addWidget(subtitle_group)
        layout.addWidget(post_group)
        layout.addWidget(execution_group)
        return group

    def _load_defaults(self):
        self.target_lang_combo.setCurrentText(self.config.default_target_language)
        self.provider_combo.setCurrentText(self.config.default_provider)
        self.tts_provider_combo.setCurrentText(self.config.default_tts_provider)
        self.translate_all_checkbox.setChecked(self.config.default_translate_all)
        self.tts_enable_checkbox.setChecked(self.config.default_enable_tts)
        self.subtitle_enable_checkbox.setChecked(self.config.default_enable_subtitles)
        self.source_lang_combo.setCurrentText(self.config.default_whisper_language)
        if "Arial" in QtGui.QFontDatabase.families():
            self.font_family_combo.setCurrentText("Arial")

    def _open_settings(self):
        dialog = SettingsDialog(self.config_manager, self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.config = self.config_manager.config

    def _fetch_models_async(self):
        self.model_fetch_thread = ModelFetchThread()
        self.model_fetch_thread.models_ready.connect(self._apply_models)
        self.model_fetch_thread.failed.connect(self._log_model_error)
        self.model_fetch_thread.start()

    def _apply_models(self, models: list[str]):
        self.model_combo.clear()
        if not models:
            self.model_combo.addItem("No models found")
            return
        self.model_combo.addItems(models)

    def _log_model_error(self, error: str):
        self.log_console.append(f"Model fetch failed: {error}")
        self.model_combo.clear()
        self.model_combo.addItem("Model fetch failed")

    def _update_source_mode(self):
        asr_enabled = self.asr_radio.isChecked()
        self.model_combo.setEnabled(asr_enabled)
        self.source_lang_combo.setEnabled(asr_enabled)
        self.srt_path_edit.setEnabled(not asr_enabled)

    def _update_provider_models(self):
        provider = self.provider_combo.currentText()
        self.model_combo_provider.clear()
        if provider == "ChatGPT":
            self.model_combo_provider.addItems(OPENAI_MODELS)
        else:
            self.model_combo_provider.addItems(GEMINI_MODELS)

    def _browse_video(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Video",
            "",
            f"Video Files ({SUPPORTED_VIDEO_EXTENSIONS})",
        )
        if path:
            self.video_path_edit.setText(path)

    def _browse_srt(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select SRT",
            "",
            "SRT Files (*.srt)",
        )
        if path:
            self.srt_path_edit.setText(path)

    def _select_color(self):
        color = QtWidgets.QColorDialog.getColor()
        if color.isValid():
            self.text_color_display.setText(color.name())

    def _start_processing(self):
        if self.worker and self.worker.isRunning():
            QtWidgets.QMessageBox.warning(self, "Busy", "Processing already running.")
            return
        if self.tts_provider_combo.currentText() == "Custom API" and not self.config.custom_tts_url:
            QtWidgets.QMessageBox.warning(
                self,
                "Missing Configuration",
                "Please configure the Custom TTS Endpoint in Settings.",
            )
            return

        settings = AppSettings(
            video_path=self.video_path_edit.text().strip(),
            source_mode="ASR" if self.asr_radio.isChecked() else "SRT",
            whisper_model=self.model_combo.currentText(),
            source_language=self.source_lang_combo.currentText(),
            srt_path=self.srt_path_edit.text().strip(),
            provider=self.provider_combo.currentText(),
            provider_model=self.model_combo_provider.currentText(),
            target_language=self.target_lang_combo.currentText(),
            translate_all=self.translate_all_checkbox.isChecked(),
            glossary_instructions=self.glossary_text.toPlainText(),
            enable_tts=self.tts_enable_checkbox.isChecked(),
            tts_provider=self.tts_provider_combo.currentText(),
            force_audio_sync=self.force_sync_checkbox.isChecked(),
            speed=self.speed_input.value(),
            pitch=float(self.pitch_input.value()),
            enable_subtitles=self.subtitle_enable_checkbox.isChecked(),
            font_family=self.font_family_combo.currentText().strip(),
            font_size=self.font_size_spin.value(),
            text_color=self.text_color_display.text().strip(),
            border_width=self.border_width_spin.value(),
            position=self.position_combo.currentText(),
            mute_original=self.mute_radio.isChecked(),
            duck_audio=self.duck_input.value(),
            blur_fill=self.blur_checkbox.isChecked(),
            blur_height=self.blur_height_spin.value(),
            blur_mode=self.blur_mode_combo.currentText(),
            cuda=self.cuda_checkbox.isChecked(),
            output_dir=self.config.default_output_dir,
        )

        self.worker = WorkerThread(settings, self.config)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.status.connect(self.status_label.setText)
        self.worker.log.connect(self.log_console.append)
        self.worker.finished.connect(self._handle_finished)
        self.worker.failed.connect(self._handle_failed)
        self.worker.start()

    def _handle_finished(self, output_path: str):
        self.status_label.setText("Done")
        QtWidgets.QMessageBox.information(self, "Completed", f"Output saved to {output_path}")

    def _handle_failed(self, error: str):
        self.status_label.setText("Failed")
        QtWidgets.QMessageBox.critical(self, "Error", error)


def main():
    app = QtWidgets.QApplication(sys.argv)
    if not find_tool("ffmpeg") or not find_tool("ffprobe"):
        progress = QtWidgets.QProgressDialog(
            "Downloading essential components (FFmpeg)...",
            None,
            0,
            0,
        )
        progress.setWindowTitle("Initializing")
        progress.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        progress.setCancelButton(None)
        progress.show()

        loop = QtCore.QEventLoop()
        downloader = DependencyDownloadThread()

        def _finish():
            loop.quit()

        def _fail(message: str):
            QtWidgets.QMessageBox.critical(
                None,
                "Dependency Error",
                f"Failed to download FFmpeg: {message}",
            )
            loop.quit()

        downloader.status.connect(progress.setLabelText)
        downloader.finished.connect(_finish)
        downloader.failed.connect(_fail)
        downloader.start()
        loop.exec()
        progress.close()
        if not find_tool("ffmpeg") or not find_tool("ffprobe"):
            return
    config_manager = ConfigManager()
    window = MainWindow(config_manager)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
