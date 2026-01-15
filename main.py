from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
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


def run_subprocess(command: list[str], log_cb=None, stop_event: threading.Event | None = None) -> int:
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
    stop_watcher = None
    if stop_event is not None:
        stop_watcher = threading.Thread(
            target=_terminate_on_stop,
            args=(process, stop_event),
            daemon=True,
        )
        stop_watcher.start()
    if process.stdout:
        for line in process.stdout:
            if log_cb:
                log_cb(line.rstrip())
    return_code = process.wait()
    if stop_event is not None and stop_event.is_set():
        return 1
    return return_code


def _terminate_on_stop(process: subprocess.Popen, stop_event: threading.Event) -> None:
    stop_event.wait()
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()


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


def get_video_height(path: Path, ffprobe_path: Path) -> int:
    command = [
        str(ffprobe_path),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=height",
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
        return int(process.stdout.strip())
    except ValueError:
        return 0


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
    blur_padding: float
    blur_mode: str
    cuda: bool
    watermark_top_left: str
    watermark_top_right: str
    flip_horizontal: bool
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


class StopProcessing(Exception):
    pass


class WorkerThread(QtCore.QThread):
    progress = QtCore.pyqtSignal(int)
    status = QtCore.pyqtSignal(str)
    log = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(str)
    failed = QtCore.pyqtSignal(str)
    stopped = QtCore.pyqtSignal()

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
        except StopProcessing:
            self.stopped.emit()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))

    def _run_pipeline(self):
        settings = self.settings
        self._check_stop()
        if not settings.video_path:
            raise ValueError("Video path is required.")

        work_dir = Path(tempfile.mkdtemp(prefix="video_trans_"))
        video_path = Path(settings.video_path)
        video_stem = video_path.stem
        output_dir = Path(settings.output_dir) if settings.output_dir else video_path.parent / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        self.log.emit(f"Working directory: {work_dir}")
        cache_dir = get_resource_path("tmp")
        cache_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg_path = find_tool("ffmpeg")
        ffprobe_path = find_tool("ffprobe")
        if not ffmpeg_path or not ffprobe_path:
            raise FileNotFoundError("ffmpeg/ffprobe not found in bin/ or tools/ folder.")

        audio_path = work_dir / "audio.wav"
        self._step(5, "Extracting audio")
        self._check_stop()
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
        if run_subprocess(extract_command, self.log.emit, self._stop_event) != 0:
            self._check_stop()
            raise RuntimeError("FFmpeg audio extraction failed.")

        srt_path = None
        if settings.source_mode == "ASR":
            self._step(20, "Running ASR")
            self._check_stop()
            whisper_path = find_tool("whisper")
            if not whisper_path:
                raise FileNotFoundError("whisper executable not found in bin/ or tools/ folder.")
            cached_asr = cache_dir / (
                f"asr_{video_stem}_{settings.whisper_model}_{settings.source_language}.srt"
            )
            if cached_asr.exists():
                self.log.emit("Restored ASR from cache.")
                srt_path = cached_asr
                shutil.copy2(cached_asr, output_dir / f"asr_{video_stem}.srt")
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
                if run_subprocess(whisper_command, self.log.emit, self._stop_event) != 0:
                    self._check_stop()
                    raise RuntimeError("Whisper ASR failed.")
                shutil.copy2(srt_path, cached_asr)
                shutil.copy2(srt_path, output_dir / f"asr_{video_stem}.srt")
        else:
            if not settings.srt_path:
                raise ValueError("SRT path is required when using SRT mode.")
            srt_path = Path(settings.srt_path)

        self._step(40, "Translating subtitles")
        self._check_stop()
        with open(srt_path, "r", encoding="utf-8", errors="replace") as handle:
            source_content = handle.read()
        entries = parse_srt(source_content)

        translated_srt = cache_dir / (
            f"trans_{video_stem}_{settings.provider_model}_{settings.target_language}.srt"
        )
        if translated_srt.exists():
            self.log.emit("Restored translation from cache.")
            with open(translated_srt, "r", encoding="utf-8", errors="replace") as handle:
                translated_entries = parse_srt(handle.read())
        else:
            translated_entries = self._translate_entries(entries, settings)
            with open(translated_srt, "w", encoding="utf-8", errors="replace") as handle:
                handle.write(render_srt(translated_entries))
        translated_output = output_dir / f"trans_{video_stem}.srt"
        final_subtitle_output = output_dir / f"sub_{video_stem}.srt"
        translated_content = render_srt(translated_entries)
        translated_output.write_text(translated_content, encoding="utf-8")
        final_subtitle_output.write_text(translated_content, encoding="utf-8")

        tts_audio = None
        if settings.enable_tts:
            self._step(60, "Generating TTS")
            self._check_stop()
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
            shutil.copy2(tts_audio, output_dir / f"dub_{video_stem}.wav")

        self._step(80, "Rendering output")
        self._check_stop()
        video_height = get_video_height(video_path, ffprobe_path)
        watermark_paths = self._collect_watermark_paths(settings)
        render_command = [str(ffmpeg_path), "-y", "-i", settings.video_path]
        for watermark, _position in watermark_paths:
            render_command += ["-i", str(watermark)]
        if tts_audio:
            render_command += ["-i", str(tts_audio)]
        video_filter_args = self._build_video_filter_args(
            final_subtitle_output,
            settings,
            video_height,
            watermark_paths,
        )
        render_command += video_filter_args

        has_video_map = "-map" in video_filter_args
        tts_input_index = 1 + len(watermark_paths)
        if tts_audio:
            if not has_video_map:
                render_command += ["-map", "0:v"]
            render_command += ["-map", f"{tts_input_index}:a"]
        else:
            if not has_video_map:
                render_command += ["-map", "0:v"]
            render_command += ["-map", "0:a?"]
        if settings.mute_original:
            render_command += ["-c:a", "aac", "-b:a", "192k"]
        else:
            duck_volume = max(0.1, settings.duck_audio / 100.0)
            render_command += ["-filter:a", f"volume={duck_volume}"]

        output_path = output_dir / f"final_{video_stem}.mp4"
        render_command.append(str(output_path))
        if run_subprocess(render_command, self.log.emit, self._stop_event) != 0:
            self._check_stop()
            raise RuntimeError("Video rendering failed.")

        self._step(100, "Completed")
        self.finished.emit(str(output_path))

    def _step(self, value: int, message: str):
        self._check_stop()
        self.progress.emit(value)
        self.status.emit(message)

    def _check_stop(self):
        if self._stop_event.is_set():
            raise StopProcessing

    def _translate_entries(self, entries: list[dict], settings: AppSettings) -> list[dict]:
        if settings.provider == "ChatGPT" and openai is None:
            raise RuntimeError("openai package not installed.")
        if settings.provider == "Gemini" and genai is None:
            raise RuntimeError("google-genai package not installed.")

        entry_count = len(entries)
        if entry_count < 300:
            self._check_stop()
            combined_text = "\n".join(entry["text"] for entry in entries)
            translated_text = self._translate_text(combined_text, settings)
            translated_lines = translated_text.splitlines()
            for idx, entry in enumerate(entries):
                entry["text"] = translated_lines[idx] if idx < len(translated_lines) else entry["text"]
            return entries

        for chunk in self._split_translation_chunks(entries):
            self._check_stop()
            combined_text = "\n".join(entry["text"] for entry in chunk)
            translated_text = self._translate_text(combined_text, settings)
            translated_lines = translated_text.splitlines()
            for idx, entry in enumerate(chunk):
                entry["text"] = translated_lines[idx] if idx < len(translated_lines) else entry["text"]
        return entries

    def _split_translation_chunks(self, entries: list[dict]) -> list[list[dict]]:
        chunks = []
        index = 0
        total = len(entries)
        while index < total:
            remaining = total - index
            if remaining < 250:
                chunks.append(entries[index:])
                break
            chunks.append(entries[index : index + 200])
            index += 200
        return chunks

    def _translate_text(self, text: str, settings: AppSettings) -> str:
        glossary = settings.glossary_instructions.strip()
        system_instructions = (
            "You are a professional translator.\n"
            "CONSTRAINT: You are translating for Movie Dubbing. The translated text must "
            "be concise and match the spoken duration of the original text as closely as "
            "possible. Avoid wordy explanations or expansions. Choose shorter synonyms "
            "where possible. If the source sentence is short (e.g., 'No.'), the target must "
            "be short (e.g., 'Không.'). Do not make it 'Tôi không đồng ý với điều đó.' "
            "unless necessary for context.\n"
            "RULE: If a sentence translates to a single word (e.g., 'Không', 'Được'), you "
            "MUST merge it into the previous or next sentence, whichever fits the context "
            "better. Ensure every translated segment has at least 2 words or is "
            "grammatically complete."
        )
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
            self._check_stop()
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
        speed_rate = max(0.1, min(1.9, self.settings.speed))
        raw_key = generate_cache_key(
            text,
            self.settings.tts_provider,
            self.settings.target_language,
            self.config.custom_tts_url,
            self.config.custom_tts_key,
            self.config.vbee_app_id,
            self.config.vbee_voice_code,
            speed_rate,
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
        elif self.settings.tts_provider == "vBee TTS":
            audio_url = self._generate_vbee_tts(text, speed_rate)
            audio_response = requests.get(audio_url, timeout=60)
            audio_response.raise_for_status()
            with open(temp_output, "wb") as handle:
                handle.write(audio_response.content)
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
        if run_subprocess(convert_command, self.log.emit, self._stop_event) != 0:
            self._check_stop()
            raise RuntimeError("TTS audio conversion failed.")
        if temp_output.exists():
            temp_output.unlink()
        return output_path

    def _generate_vbee_tts(self, text: str, speed_rate: float) -> str:
        if not self.config.vbee_app_id or not self.config.vbee_token:
            raise ValueError("vBee credentials are required.")
        payload = {
            "app_id": self.config.vbee_app_id,
            "response_type": "indirect",
            "callbackUrl": "https://www.k6vn.org/",
            "input_text": text,
            "voice_code": self.config.vbee_voice_code,
            "speed_rate": f"{speed_rate:.2f}",
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.vbee_token}",
        }
        response = requests.post(
            "https://vbee.vn/api/v1/tts",
            json=payload,
            headers=headers,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != 1:
            raise RuntimeError("vBee TTS request failed.")
        request_id = data.get("result", {}).get("request_id")
        if not request_id:
            raise RuntimeError("vBee TTS missing request_id.")

        for _ in range(self.config.vbee_max_retries):
            self._check_stop()
            time.sleep(1)
            poll_response = requests.get(
                f"https://vbee.vn/api/v1/tts/{request_id}",
                headers={"Authorization": f"Bearer {self.config.vbee_token}"},
                timeout=30,
            )
            poll_response.raise_for_status()
            poll_data = poll_response.json()
            audio_link = poll_data.get("result", {}).get("audio_link")
            if audio_link:
                return audio_link
        raise TimeoutError("vBee TTS polling timed out.")

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
        if run_subprocess(command, self.log.emit, self._stop_event) != 0:
            self._check_stop()
            raise RuntimeError("Audio time-stretch failed.")
        return adjusted_path

    def _mix_segments(
        self,
        segment_paths: list[tuple[Path, int]],
        output_path: Path,
        ffmpeg_path: Path,
        video_duration: float,
    ):
        batch_size = 20
        temp_files: list[Path] = []
        base_audio = output_path.with_name("base_accumulator.wav")
        temp_mix = output_path.with_name("temp_mix.wav")
        try:
            base_command = [
                str(ffmpeg_path),
                "-y",
                "-f",
                "lavfi",
                "-t",
                f"{max(video_duration, 0.1)}",
                "-i",
                "anullsrc=channel_layout=mono:sample_rate=44100",
                "-ac",
                "1",
                "-ar",
                "44100",
                str(base_audio),
            ]
            if run_subprocess(base_command, self.log.emit, self._stop_event) != 0:
                self._check_stop()
                raise RuntimeError("Base audio generation failed.")
            temp_files.append(base_audio)

            for start in range(0, len(segment_paths), batch_size):
                self._check_stop()
                batch = segment_paths[start : start + batch_size]
                command = [str(ffmpeg_path), "-y", "-i", str(base_audio)]
                filter_parts = []
                inputs = ["[0:a]"]
                for idx, (path, delay_ms) in enumerate(batch):
                    command += ["-i", str(path)]
                    input_index = idx + 1
                    filter_parts.append(
                        f"[{input_index}:a]adelay={delay_ms}|{delay_ms}[a{input_index}]"
                    )
                    inputs.append(f"[a{input_index}]")
                filter_parts.append(
                    f"{''.join(inputs)}amix=inputs={len(inputs)}:"
                    "duration=first:dropout_transition=0:normalize=0[aout]"
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
                    str(temp_mix),
                ]
                if run_subprocess(command, self.log.emit, self._stop_event) != 0:
                    self._check_stop()
                    raise RuntimeError("TTS segment mixing failed.")
                shutil.move(temp_mix, base_audio)

            final_command = [
                str(ffmpeg_path),
                "-y",
                "-i",
                str(base_audio),
                "-ac",
                "1",
                "-ar",
                "44100",
                str(output_path),
            ]
            if run_subprocess(final_command, self.log.emit, self._stop_event) != 0:
                self._check_stop()
                raise RuntimeError("Final audio export failed.")
        finally:
            for temp_file in temp_files + [temp_mix]:
                if temp_file.exists():
                    temp_file.unlink()

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
        if run_subprocess(command, self.log.emit, self._stop_event) != 0:
            self._check_stop()
            raise RuntimeError("Audio post-processing failed.")
        shutil.move(fx_output, audio_path)

    def _build_subtitle_filter(
        self,
        srt_path: Path,
        settings: AppSettings,
        margin_v: int,
    ) -> str:
        alignment = {"Bottom": "2", "Center": "5", "Top": "8"}.get(settings.position, "2")
        style = (
            f"FontName={settings.font_family},"
            f"FontSize={settings.font_size},"
            f"PrimaryColour=&H{settings.text_color.lstrip('#')},"
            f"Outline={settings.border_width},"
            f"Alignment={alignment},"
            f"MarginV={margin_v}"
        )
        safe_path = escape_ffmpeg_path(str(srt_path))
        return f"subtitles='{safe_path}':force_style='{style}'"

    def _build_blur_filter(self, settings: AppSettings) -> str:
        if settings.blur_mode == "Blur":
            return (
                "boxblur=luma_radius=10:luma_power=1:"
                "chroma_radius=10:chroma_power=1"
            )
        color = "black"
        return f"drawbox=y=0:h=ih:color={color}:t=fill"

    def _collect_watermark_paths(self, settings: AppSettings) -> list[tuple[Path, str]]:
        paths = []
        for label, raw_path, position in (
            ("Top-Left", settings.watermark_top_left, "overlay=10:10"),
            ("Top-Right", settings.watermark_top_right, "overlay=main_w-overlay_w-10:10"),
        ):
            if not raw_path:
                continue
            path = Path(raw_path)
            if not path.exists():
                self.log.emit(f"Watermark {label} not found: {path}")
                continue
            paths.append((path, position))
        return paths

    def _build_video_filter_args(
        self,
        srt_path: Path | None,
        settings: AppSettings,
        video_height: int,
        watermark_paths: list[tuple[Path, str]],
    ) -> list[str]:
        margin_v = int(video_height * 0.05) if video_height > 0 else 0
        subtitle_enabled = settings.enable_subtitles and srt_path and srt_path.exists()
        use_complex = settings.blur_fill or settings.flip_horizontal or watermark_paths

        if not use_complex and subtitle_enabled:
            return ["-vf", self._build_subtitle_filter(srt_path, settings, margin_v)]
        if not use_complex and not subtitle_enabled:
            return []

        filter_parts = []
        current = "[0:v]"

        if settings.flip_horizontal:
            filter_parts.append(f"{current}hflip[vflip]")
            current = "[vflip]"

        if settings.blur_fill:
            height_ratio = min(0.95, max(0.01, settings.blur_height / 100.0))
            padding_ratio = max(0.0, settings.blur_padding / 100.0)
            crop_h = f"ih*{height_ratio}"
            crop_y = f"ih*(1-{height_ratio}-{padding_ratio})"
            blur_filter = self._build_blur_filter(settings)
            filter_parts.append(f"{current}crop=iw:{crop_h}:0:{crop_y}[bottom]")
            filter_parts.append(f"[bottom]{blur_filter}[blurred]")
            filter_parts.append(
                f"{current}[blurred]overlay=0:ih*({1 - height_ratio - padding_ratio})[bg_processed]"
            )
            current = "[bg_processed]"

        for idx, (_watermark_path, position) in enumerate(watermark_paths):
            input_label = f"[{idx + 1}:v]"
            next_label = f"[wm{idx}]"
            filter_parts.append(f"{current}{input_label}{position}{next_label}")
            current = next_label

        if subtitle_enabled:
            subtitle_filter = self._build_subtitle_filter(srt_path, settings, margin_v)
            filter_parts.append(f"{current}{subtitle_filter}[vout]")
        else:
            filter_parts.append(f"{current}null[vout]")

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
        general_layout.addRow("OpenAI API Key:", self.openai_key_edit)
        general_layout.addRow("Gemini API Key:", self.gemini_key_edit)
        general_layout.addRow("Custom TTS Key:", self.custom_tts_key_edit)

        tts_tab = QtWidgets.QWidget()
        tts_layout = QtWidgets.QFormLayout(tts_tab)
        self.custom_tts_url_edit = QtWidgets.QLineEdit(self.config.custom_tts_url)
        self.custom_tts_url_edit.setPlaceholderText("https://example.com/tts")
        tts_layout.addRow("Custom TTS API URL:", self.custom_tts_url_edit)

        self.vbee_app_id_edit = QtWidgets.QLineEdit(self.config.vbee_app_id)
        self.vbee_token_edit = QtWidgets.QLineEdit(self.config.vbee_token)
        self.vbee_token_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.vbee_voice_code_edit = QtWidgets.QLineEdit(self.config.vbee_voice_code)
        self.vbee_max_retries_spin = QtWidgets.QSpinBox()
        self.vbee_max_retries_spin.setRange(1, 60)
        self.vbee_max_retries_spin.setValue(self.config.vbee_max_retries)

        tts_layout.addRow("vBee App ID:", self.vbee_app_id_edit)
        tts_layout.addRow("vBee Token:", self.vbee_token_edit)
        tts_layout.addRow("vBee Voice Code:", self.vbee_voice_code_edit)
        tts_layout.addRow("vBee Max Retries:", self.vbee_max_retries_spin)

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

    def _save(self):
        self.config.openai_api_key = self.openai_key_edit.text().strip()
        self.config.gemini_api_key = self.gemini_key_edit.text().strip()
        self.config.custom_tts_key = self.custom_tts_key_edit.text().strip()
        self.config.custom_tts_url = self.custom_tts_url_edit.text().strip()
        self.config.vbee_app_id = self.vbee_app_id_edit.text().strip()
        self.config.vbee_token = self.vbee_token_edit.text().strip()
        self.config.vbee_voice_code = self.vbee_voice_code_edit.text().strip()
        self.config.vbee_max_retries = self.vbee_max_retries_spin.value()
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
        self._session_state = None
        self._user_stop_requested = False

        self._build_menu()
        self._apply_theme()

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QHBoxLayout(central)

        column_layout = QtWidgets.QHBoxLayout()
        main_layout.addLayout(column_layout)

        column_layout.addWidget(self._build_left_column())
        column_layout.addWidget(self._build_center_column())
        column_layout.addWidget(self._build_right_column())

        self._update_source_mode()
        self._update_provider_models()
        self._load_defaults()
        self.load_session_state()
        self._fetch_models_async()
        self._init_timer()

    def _build_menu(self):
        menu = self.menuBar().addMenu("Settings")
        self.settings_action = QtGui.QAction("Preferences", self)
        self.settings_action.triggered.connect(self._open_settings)
        menu.addAction(self.settings_action)
        self.clear_temp_action = QtGui.QAction("Clear Temp Files", self)
        self.clear_temp_action.triggered.connect(self._clear_temp_files)
        menu.addAction(self.clear_temp_action)

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

    def _build_left_column(self):
        self.left_column_group = QtWidgets.QGroupBox("Column 1: Source & Translation")
        layout = QtWidgets.QVBoxLayout(self.left_column_group)
        layout.addWidget(self._build_video_source_group())
        layout.addWidget(self._build_asr_trans_group())
        layout.addStretch()
        return self.left_column_group

    def _build_video_source_group(self):
        self.video_source_group = QtWidgets.QGroupBox("Video Source")
        layout = QtWidgets.QVBoxLayout(self.video_source_group)

        self.video_path_edit = QtWidgets.QLineEdit()
        self.video_browse_button = QtWidgets.QPushButton("Browse Video")
        self.video_browse_button.clicked.connect(self._browse_video)

        video_layout = QtWidgets.QHBoxLayout()
        video_layout.addWidget(self.video_path_edit)
        video_layout.addWidget(self.video_browse_button)
        layout.addWidget(QtWidgets.QLabel("Video File"))
        layout.addLayout(video_layout)

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
        self.srt_browse_button = QtWidgets.QPushButton("Browse SRT")
        self.srt_browse_button.clicked.connect(self._browse_srt)

        source_layout.addWidget(self.asr_radio, 0, 0, 1, 2)
        source_layout.addWidget(QtWidgets.QLabel("Whisper Model:"), 1, 0)
        source_layout.addWidget(self.model_combo, 1, 1)
        source_layout.addWidget(QtWidgets.QLabel("Source Language:"), 2, 0)
        source_layout.addWidget(self.source_lang_combo, 2, 1)
        source_layout.addWidget(self.srt_radio, 3, 0, 1, 2)
        source_layout.addWidget(self.srt_path_edit, 4, 0)
        source_layout.addWidget(self.srt_browse_button, 4, 1)

        layout.addWidget(source_group)
        return self.video_source_group

    def _build_asr_trans_group(self):
        self.asr_trans_group = QtWidgets.QGroupBox("ASR / Translation Settings")
        layout = QtWidgets.QVBoxLayout(self.asr_trans_group)

        translation_group = QtWidgets.QGroupBox("Translation")
        translation_layout = QtWidgets.QGridLayout(translation_group)

        self.provider_combo = QtWidgets.QComboBox()
        self.provider_combo.addItems(["Gemini", "ChatGPT"])
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

        layout.addWidget(translation_group)
        layout.addWidget(glossary_group)
        return self.asr_trans_group

    def _build_dubbing_watermark_group(self):
        self.dubbing_group = QtWidgets.QGroupBox("Dubbing / Watermark Settings")
        layout = QtWidgets.QVBoxLayout(self.dubbing_group)

        tts_group = QtWidgets.QGroupBox("Dubbing (TTS)")
        tts_layout = QtWidgets.QGridLayout(tts_group)
        self.tts_enable_checkbox = QtWidgets.QCheckBox("Enable TTS")
        self.tts_provider_combo = QtWidgets.QComboBox()
        self.tts_provider_combo.addItems(["Edge TTS", "Custom API", "vBee TTS"])
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

        watermark_group = QtWidgets.QGroupBox("Watermarks & Effects")
        watermark_layout = QtWidgets.QGridLayout(watermark_group)
        self.watermark_top_left_edit = QtWidgets.QLineEdit()
        self.watermark_top_left_button = QtWidgets.QPushButton("Browse")
        self.watermark_top_left_button.clicked.connect(
            lambda: self._browse_watermark(self.watermark_top_left_edit)
        )
        self.watermark_top_right_edit = QtWidgets.QLineEdit()
        self.watermark_top_right_button = QtWidgets.QPushButton("Browse")
        self.watermark_top_right_button.clicked.connect(
            lambda: self._browse_watermark(self.watermark_top_right_edit)
        )
        self.flip_checkbox = QtWidgets.QCheckBox("Flip Video Horizontally")

        watermark_layout.addWidget(QtWidgets.QLabel("Watermark Top-Left"), 0, 0)
        watermark_layout.addWidget(self.watermark_top_left_edit, 0, 1)
        watermark_layout.addWidget(self.watermark_top_left_button, 0, 2)
        watermark_layout.addWidget(QtWidgets.QLabel("Watermark Top-Right"), 1, 0)
        watermark_layout.addWidget(self.watermark_top_right_edit, 1, 1)
        watermark_layout.addWidget(self.watermark_top_right_button, 1, 2)
        watermark_layout.addWidget(self.flip_checkbox, 2, 0, 1, 3)

        post_group = QtWidgets.QGroupBox("Post-Processing")
        post_layout = QtWidgets.QGridLayout(post_group)
        self.mute_radio = QtWidgets.QRadioButton("Mute Original")
        self.duck_radio = QtWidgets.QRadioButton("Duck Audio")
        self.mute_radio.setChecked(True)
        self.duck_input = QtWidgets.QSpinBox()
        self.duck_input.setRange(0, 100)
        self.duck_input.setValue(30)
        self.blur_checkbox = QtWidgets.QCheckBox("Blur/Fill Bottom")
        self.blur_checkbox.setChecked(True)
        self.blur_height_spin = QtWidgets.QSpinBox()
        self.blur_height_spin.setRange(5, 50)
        self.blur_height_spin.setValue(20)
        self.blur_padding_spin = QtWidgets.QDoubleSpinBox()
        self.blur_padding_spin.setRange(0.0, 20.0)
        self.blur_padding_spin.setSingleStep(0.1)
        self.blur_padding_spin.setValue(5.0)
        self.blur_mode_combo = QtWidgets.QComboBox()
        self.blur_mode_combo.addItems(["Blur", "Solid Color Fill"])
        self.cuda_checkbox = QtWidgets.QCheckBox("Enable CUDA Acceleration")

        post_layout.addWidget(self.mute_radio, 0, 0)
        post_layout.addWidget(self.duck_radio, 0, 1)
        post_layout.addWidget(QtWidgets.QLabel("Duck Volume:"), 1, 0)
        post_layout.addWidget(self.duck_input, 1, 1)
        post_layout.addWidget(self.blur_checkbox, 2, 0, 1, 2)
        post_layout.addWidget(QtWidgets.QLabel("Height %:"), 3, 0)
        post_layout.addWidget(self.blur_height_spin, 3, 1)
        post_layout.addWidget(QtWidgets.QLabel("Blur Bottom Padding (%):"), 4, 0)
        post_layout.addWidget(self.blur_padding_spin, 4, 1)
        post_layout.addWidget(self.blur_mode_combo, 5, 0, 1, 2)
        post_layout.addWidget(self.cuda_checkbox, 6, 0, 1, 2)

        layout.addWidget(tts_group)
        layout.addWidget(watermark_group)
        layout.addWidget(post_group)
        return self.dubbing_group

    def _build_center_column(self):
        self.center_column_group = QtWidgets.QGroupBox("Column 2: Visuals & Audio")
        layout = QtWidgets.QVBoxLayout(self.center_column_group)
        layout.addWidget(self._build_dubbing_watermark_group())
        layout.addWidget(self._build_subtitle_group())
        layout.addStretch()
        return self.center_column_group

    def _build_subtitle_group(self):
        self.subtitle_group = QtWidgets.QGroupBox("Subtitles")
        subtitle_layout = QtWidgets.QGridLayout(self.subtitle_group)
        self.subtitle_enable_checkbox = QtWidgets.QCheckBox("Enable Subtitles")
        self.font_family_combo = QtWidgets.QComboBox()
        self.font_family_combo.addItems(QtGui.QFontDatabase.families())
        self.font_size_spin = QtWidgets.QSpinBox()
        self.font_size_spin.setRange(8, 72)
        self.font_size_spin.setValue(16)
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

        return self.subtitle_group

    def _build_preview_group(self):
        self.preview_group = QtWidgets.QGroupBox("Preview")
        layout = QtWidgets.QVBoxLayout(self.preview_group)
        self.preview_button = QtWidgets.QPushButton("Generate Preview")
        self.preview_button.clicked.connect(self._generate_preview)
        self.preview_label = QtWidgets.QLabel("Preview will appear here.")
        self.preview_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(240)
        self.preview_label.setStyleSheet("border: 1px solid #444444;")
        layout.addWidget(self.preview_button)
        layout.addWidget(self.preview_label)
        return self.preview_group

    def _build_right_column(self):
        self.right_column_group = QtWidgets.QGroupBox("Column 3: Preview & Status")
        layout = QtWidgets.QVBoxLayout(self.right_column_group)
        layout.addWidget(self._build_preview_group())
        layout.addWidget(self._build_execution_group())
        return self.right_column_group

    def _build_execution_group(self):
        self.execution_group = QtWidgets.QGroupBox("Execution Status")
        execution_layout = QtWidgets.QVBoxLayout(self.execution_group)
        self.start_button = QtWidgets.QPushButton("Start")
        self.start_button.clicked.connect(self._toggle_processing)
        self._start_button_default_style = self.start_button.styleSheet()
        self.progress_bar = QtWidgets.QProgressBar()
        self.status_label = QtWidgets.QLabel("Idle")
        self.processing_time_label = QtWidgets.QLabel("Processing Time: 00:00")
        self.log_console = QtWidgets.QTextEdit()
        self.log_console.setReadOnly(True)

        execution_layout.addWidget(self.start_button)
        execution_layout.addWidget(self.progress_bar)
        execution_layout.addWidget(self.status_label)
        execution_layout.addWidget(self.processing_time_label)
        execution_layout.addWidget(self.log_console)
        return self.execution_group

    def _load_defaults(self):
        self.target_lang_combo.setCurrentText(self.config.default_target_language)
        self.provider_combo.setCurrentText(self.config.default_provider)
        self.model_combo_provider.setCurrentText("gemini-flash-latest")
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
        if self._session_state:
            self._set_combo_value(self.model_combo, self._session_state)
            self._session_state = None

    def _log_model_error(self, error: str):
        self.log_console.append(f"Model fetch failed: {error}")
        self.model_combo.clear()
        self.model_combo.addItem("Model fetch failed")

    def _update_source_mode(self):
        asr_enabled = self.asr_radio.isChecked()
        self.model_combo.setEnabled(asr_enabled)
        self.source_lang_combo.setEnabled(asr_enabled)
        self.srt_path_edit.setEnabled(not asr_enabled)
        self.srt_browse_button.setEnabled(not asr_enabled)

    def _update_provider_models(self):
        provider = self.provider_combo.currentText()
        self.model_combo_provider.clear()
        if provider == "ChatGPT":
            self.model_combo_provider.addItems(OPENAI_MODELS)
        else:
            self.model_combo_provider.addItems(
                ["gemini-flash-latest"]
                + [model for model in GEMINI_MODELS if model != "gemini-flash-latest"]
            )

    def _browse_video(self):
        start_dir = self.config.last_opened_directory or ""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Video",
            start_dir,
            f"Video Files ({SUPPORTED_VIDEO_EXTENSIONS})",
        )
        if path:
            self.video_path_edit.setText(path)
            self.config.last_opened_directory = str(Path(path).parent)
            self.config_manager.save()

    def _browse_srt(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select SRT",
            "",
            "SRT Files (*.srt)",
        )
        if path:
            self.srt_path_edit.setText(path)

    def _browse_watermark(self, target_edit: QtWidgets.QLineEdit):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Watermark Image",
            "",
            "Image Files (*.png *.jpg *.jpeg *.webp)",
        )
        if path:
            target_edit.setText(path)

    def _select_color(self):
        color = QtWidgets.QColorDialog.getColor()
        if color.isValid():
            self.text_color_display.setText(color.name())

    def _clear_temp_files(self):
        temp_dir = get_resource_path("tmp")
        try:
            if temp_dir.exists():
                for item in temp_dir.iterdir():
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
            temp_dir.mkdir(parents=True, exist_ok=True)
            QtWidgets.QMessageBox.information(self, "Success", "Temporary files cleared.")
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to clear temp files: {exc}")

    def _build_settings(self) -> AppSettings:
        video_path = self.video_path_edit.text().strip()
        output_dir = str(Path(video_path).parent / "output") if video_path else ""
        return AppSettings(
            video_path=video_path,
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
            blur_padding=self.blur_padding_spin.value(),
            blur_mode=self.blur_mode_combo.currentText(),
            cuda=self.cuda_checkbox.isChecked(),
            watermark_top_left=self.watermark_top_left_edit.text().strip(),
            watermark_top_right=self.watermark_top_right_edit.text().strip(),
            flip_horizontal=self.flip_checkbox.isChecked(),
            output_dir=output_dir,
        )

    def _collect_watermark_paths(self) -> list[tuple[Path, str]]:
        paths = []
        for label, raw_path, position in (
            ("Top-Left", self.watermark_top_left_edit.text().strip(), "overlay=10:10"),
            (
                "Top-Right",
                self.watermark_top_right_edit.text().strip(),
                "overlay=main_w-overlay_w-10:10",
            ),
        ):
            if not raw_path:
                continue
            path = Path(raw_path)
            if not path.exists():
                self.log_console.append(f"Watermark {label} not found: {path}")
                continue
            paths.append((path, position))
        return paths

    def _generate_preview(self):
        settings = self._build_settings()
        if not settings.video_path:
            QtWidgets.QMessageBox.warning(self, "Missing Video", "Please select a video file.")
            return
        video_path = Path(settings.video_path)
        if not video_path.exists():
            QtWidgets.QMessageBox.warning(self, "Missing Video", "Selected video file not found.")
            return
        ffmpeg_path = find_tool("ffmpeg")
        ffprobe_path = find_tool("ffprobe")
        if not ffmpeg_path or not ffprobe_path:
            QtWidgets.QMessageBox.warning(self, "Missing Tools", "ffmpeg/ffprobe not found.")
            return

        duration = get_video_duration(video_path, ffprobe_path)
        seek_time = max(0.0, duration / 2.0)
        watermark_paths = self._collect_watermark_paths()

        preview_dir = get_resource_path("tmp")
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview_path = preview_dir / "preview_frame.jpg"

        command = [str(ffmpeg_path), "-y", "-ss", f"{seek_time:.2f}", "-i", str(video_path)]
        for watermark, _position in watermark_paths:
            command += ["-i", str(watermark)]
        command += ["-vframes", "1"]

        video_height = get_video_height(video_path, ffprobe_path)
        srt_path = Path(settings.srt_path) if settings.srt_path else None
        filter_args = self._build_preview_filter_args(
            srt_path,
            settings,
            video_height,
            watermark_paths,
        )
        command += filter_args
        command.append(str(preview_path))

        if run_subprocess(command, self.log_console.append) != 0:
            QtWidgets.QMessageBox.critical(self, "Preview Error", "Failed to generate preview.")
            return
        pixmap = QtGui.QPixmap(str(preview_path))
        if pixmap.isNull():
            QtWidgets.QMessageBox.warning(self, "Preview Error", "Preview image could not be loaded.")
            return
        scaled = pixmap.scaled(
            self.preview_label.width(),
            self.preview_label.height(),
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)

    def _build_preview_subtitle_filter(
        self,
        srt_path: Path,
        settings: AppSettings,
        margin_v: int,
    ) -> str:
        alignment = {"Bottom": "2", "Center": "5", "Top": "8"}.get(settings.position, "2")
        style = (
            f"FontName={settings.font_family},"
            f"FontSize={settings.font_size},"
            f"PrimaryColour=&H{settings.text_color.lstrip('#')},"
            f"Outline={settings.border_width},"
            f"Alignment={alignment},"
            f"MarginV={margin_v}"
        )
        safe_path = escape_ffmpeg_path(str(srt_path))
        return f"subtitles='{safe_path}':force_style='{style}'"

    def _build_preview_blur_filter(self, settings: AppSettings) -> str:
        if settings.blur_mode == "Blur":
            return (
                "boxblur=luma_radius=10:luma_power=1:"
                "chroma_radius=10:chroma_power=1"
            )
        color = "black"
        return f"drawbox=y=0:h=ih:color={color}:t=fill"

    def _build_preview_filter_args(
        self,
        srt_path: Path | None,
        settings: AppSettings,
        video_height: int,
        watermark_paths: list[tuple[Path, str]],
    ) -> list[str]:
        margin_v = int(video_height * 0.05) if video_height > 0 else 0
        subtitle_enabled = settings.enable_subtitles and srt_path and srt_path.exists()
        use_complex = settings.blur_fill or settings.flip_horizontal or watermark_paths

        if not use_complex and subtitle_enabled:
            return ["-vf", self._build_preview_subtitle_filter(srt_path, settings, margin_v)]
        if not use_complex and not subtitle_enabled:
            return []

        filter_parts = []
        current = "[0:v]"

        if settings.flip_horizontal:
            filter_parts.append(f"{current}hflip[vflip]")
            current = "[vflip]"

        if settings.blur_fill:
            height_ratio = min(0.95, max(0.01, settings.blur_height / 100.0))
            padding_ratio = max(0.0, settings.blur_padding / 100.0)
            crop_h = f"ih*{height_ratio}"
            crop_y = f"ih*(1-{height_ratio}-{padding_ratio})"
            blur_filter = self._build_preview_blur_filter(settings)
            filter_parts.append(f"{current}crop=iw:{crop_h}:0:{crop_y}[bottom]")
            filter_parts.append(f"[bottom]{blur_filter}[blurred]")
            filter_parts.append(
                f"{current}[blurred]overlay=0:ih*({1 - height_ratio - padding_ratio})[bg_processed]"
            )
            current = "[bg_processed]"

        for idx, (_watermark_path, position) in enumerate(watermark_paths):
            input_label = f"[{idx + 1}:v]"
            next_label = f"[wm{idx}]"
            filter_parts.append(f"{current}{input_label}{position}{next_label}")
            current = next_label

        if subtitle_enabled:
            subtitle_filter = self._build_preview_subtitle_filter(srt_path, settings, margin_v)
            filter_parts.append(f"{current}{subtitle_filter}[vout]")
        else:
            filter_parts.append(f"{current}null[vout]")

        filter_complex = ";".join(filter_parts)
        return ["-filter_complex", filter_complex, "-map", "[vout]"]

    def _toggle_processing(self):
        if self.worker and self.worker.isRunning():
            self._stop_processing()
            return
        self._start_processing()

    def _stop_processing(self):
        if not self.worker or not self.worker.isRunning():
            return
        self._user_stop_requested = True
        self.worker.stop()
        self.worker.wait(500)
        if self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait(1000)
        self._set_running_state(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("Stopped")
        self._stop_timer()
        self.log_console.append("Process stopped by user.")

    def _start_processing(self):
        self._user_stop_requested = False
        if self.tts_provider_combo.currentText() == "Custom API" and not self.config.custom_tts_url:
            QtWidgets.QMessageBox.warning(
                self,
                "Missing Configuration",
                "Please configure the Custom TTS Endpoint in Settings.",
            )
            return

        self.save_session_state()
        settings = self._build_settings()

        self.worker = WorkerThread(settings, self.config)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.status.connect(self.status_label.setText)
        self.worker.log.connect(self.log_console.append)
        self.worker.finished.connect(self._handle_finished)
        self.worker.failed.connect(self._handle_failed)
        self.worker.stopped.connect(self._handle_stopped)
        self.worker.started.connect(self._start_timer)
        self._set_running_state(True)
        self.worker.start()

    def _handle_finished(self, output_path: str):
        self.status_label.setText("Done")
        self._stop_timer()
        self._set_running_state(False)
        self._user_stop_requested = False
        QtWidgets.QMessageBox.information(self, "Completed", f"Output saved to {output_path}")

    def _handle_failed(self, error: str):
        self.status_label.setText("Failed")
        self._stop_timer()
        self._set_running_state(False)
        self._user_stop_requested = False
        QtWidgets.QMessageBox.critical(self, "Error", error)

    def _handle_stopped(self):
        self.status_label.setText("Stopped")
        self.progress_bar.setValue(0)
        self._stop_timer()
        self._set_running_state(False)
        if not self._user_stop_requested:
            self.log_console.append("Process stopped by user.")
        self._user_stop_requested = False

    def _set_running_state(self, running: bool):
        if running:
            self.start_button.setText("Stop")
            self.start_button.setStyleSheet("background-color: #c62828; color: #ffffff;")
        else:
            self.start_button.setText("Start")
            self.start_button.setStyleSheet(self._start_button_default_style)
        for group in (
            self.video_source_group,
            self.asr_trans_group,
            self.dubbing_group,
            self.subtitle_group,
            self.preview_group,
        ):
            group.setEnabled(not running)
        self.settings_action.setEnabled(not running)
        self.clear_temp_action.setEnabled(not running)

    def _init_timer(self):
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick_timer)
        self._elapsed_seconds = 0

    def _start_timer(self):
        self._elapsed_seconds = 0
        self.processing_time_label.setText("Processing Time: 00:00")
        self._timer.start()

    def _tick_timer(self):
        self._elapsed_seconds += 1
        minutes, seconds = divmod(self._elapsed_seconds, 60)
        self.processing_time_label.setText(f"Processing Time: {minutes:02d}:{seconds:02d}")

    def _stop_timer(self):
        self._timer.stop()

    def _session_config_path(self) -> Path:
        base_dir = self.config_manager.config_path.parent
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir / "session_config.json"

    def save_session_state(self) -> None:
        video_path = self.video_path_edit.text().strip()
        if video_path:
            self.config.last_opened_directory = str(Path(video_path).parent)
        data = {
            "video_path": video_path,
            "source_mode": "ASR" if self.asr_radio.isChecked() else "SRT",
            "whisper_model": self.model_combo.currentText(),
            "source_language": self.source_lang_combo.currentText(),
            "provider": self.provider_combo.currentText(),
            "provider_model": self.model_combo_provider.currentText(),
            "target_language": self.target_lang_combo.currentText(),
            "translate_all": self.translate_all_checkbox.isChecked(),
            "glossary": self.glossary_text.toPlainText(),
            "enable_tts": self.tts_enable_checkbox.isChecked(),
            "tts_provider": self.tts_provider_combo.currentText(),
            "force_audio_sync": self.force_sync_checkbox.isChecked(),
            "speed": self.speed_input.value(),
            "pitch": float(self.pitch_input.value()),
            "enable_subtitles": self.subtitle_enable_checkbox.isChecked(),
            "font_family": self.font_family_combo.currentText(),
            "font_size": self.font_size_spin.value(),
            "text_color": self.text_color_display.text(),
            "border_width": self.border_width_spin.value(),
            "position": self.position_combo.currentText(),
            "mute_original": self.mute_radio.isChecked(),
            "duck_audio": self.duck_input.value(),
            "blur_fill": self.blur_checkbox.isChecked(),
            "blur_height": self.blur_height_spin.value(),
            "blur_padding": self.blur_padding_spin.value(),
            "blur_mode": self.blur_mode_combo.currentText(),
            "cuda": self.cuda_checkbox.isChecked(),
            "watermark_top_left": self.watermark_top_left_edit.text(),
            "watermark_top_right": self.watermark_top_right_edit.text(),
            "flip_horizontal": self.flip_checkbox.isChecked(),
            "last_opened_directory": self.config.last_opened_directory,
            "config": {
                "openai_api_key": self.config.openai_api_key,
                "gemini_api_key": self.config.gemini_api_key,
                "custom_tts_key": self.config.custom_tts_key,
                "custom_tts_url": self.config.custom_tts_url,
                "vbee_app_id": self.config.vbee_app_id,
                "vbee_token": self.config.vbee_token,
                "vbee_voice_code": self.config.vbee_voice_code,
                "vbee_max_retries": self.config.vbee_max_retries,
            },
        }
        session_path = self._session_config_path()
        with session_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)

    def load_session_state(self) -> None:
        session_path = self._session_config_path()
        if not session_path.exists():
            return
        with session_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        self.video_path_edit.setText(data.get("video_path", ""))
        source_mode = data.get("source_mode", "ASR")
        self.asr_radio.setChecked(source_mode == "ASR")
        self.srt_radio.setChecked(source_mode == "SRT")
        self.srt_path_edit.setText("")
        self.source_lang_combo.setCurrentText(data.get("source_language", "auto"))
        self.provider_combo.setCurrentText(data.get("provider", "Gemini"))
        self._update_provider_models()
        self._set_combo_value(self.model_combo_provider, data.get("provider_model", ""))
        self.target_lang_combo.setCurrentText(data.get("target_language", "vi"))
        self.translate_all_checkbox.setChecked(data.get("translate_all", False))
        self.glossary_text.setPlainText(data.get("glossary", ""))
        self.tts_enable_checkbox.setChecked(data.get("enable_tts", False))
        self.tts_provider_combo.setCurrentText(data.get("tts_provider", "Edge TTS"))
        self.force_sync_checkbox.setChecked(data.get("force_audio_sync", True))
        self.speed_input.setValue(float(data.get("speed", 1.0)))
        self.pitch_input.setValue(float(data.get("pitch", 0.0)))
        self.subtitle_enable_checkbox.setChecked(data.get("enable_subtitles", True))
        self._set_combo_value(self.font_family_combo, data.get("font_family", ""))
        self.font_size_spin.setValue(int(data.get("font_size", 16)))
        self.text_color_display.setText(data.get("text_color", "#FFFFFF"))
        self.border_width_spin.setValue(int(data.get("border_width", 2)))
        self.position_combo.setCurrentText(data.get("position", "Bottom"))
        self.mute_radio.setChecked(data.get("mute_original", True))
        self.duck_radio.setChecked(not data.get("mute_original", True))
        self.duck_input.setValue(int(data.get("duck_audio", 30)))
        self.blur_checkbox.setChecked(data.get("blur_fill", True))
        self.blur_height_spin.setValue(int(data.get("blur_height", 20)))
        self.blur_padding_spin.setValue(float(data.get("blur_padding", 5.0)))
        self.blur_mode_combo.setCurrentText(data.get("blur_mode", "Blur"))
        self.cuda_checkbox.setChecked(data.get("cuda", False))
        self.watermark_top_left_edit.setText(data.get("watermark_top_left", ""))
        self.watermark_top_right_edit.setText(data.get("watermark_top_right", ""))
        self.flip_checkbox.setChecked(data.get("flip_horizontal", False))
        self.config.last_opened_directory = data.get("last_opened_directory", "")
        config_data = data.get("config", {})
        self.config.openai_api_key = config_data.get("openai_api_key", self.config.openai_api_key)
        self.config.gemini_api_key = config_data.get("gemini_api_key", self.config.gemini_api_key)
        self.config.custom_tts_key = config_data.get("custom_tts_key", self.config.custom_tts_key)
        self.config.custom_tts_url = config_data.get("custom_tts_url", self.config.custom_tts_url)
        self.config.vbee_app_id = config_data.get("vbee_app_id", self.config.vbee_app_id)
        self.config.vbee_token = config_data.get("vbee_token", self.config.vbee_token)
        self.config.vbee_voice_code = config_data.get(
            "vbee_voice_code",
            self.config.vbee_voice_code,
        )
        self.config.vbee_max_retries = int(
            config_data.get("vbee_max_retries", self.config.vbee_max_retries)
        )
        self._session_state = data.get("whisper_model")

    def _set_combo_value(self, combo: QtWidgets.QComboBox, value: str) -> None:
        if not value:
            return
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)


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
