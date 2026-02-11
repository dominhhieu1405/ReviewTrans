import asyncio
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from PyQt6 import QtCore, QtWidgets

try:
    import edge_tts
except ImportError:
    edge_tts = None

try:
    import openai
except ImportError:
    openai = None

try:
    import google.generativeai as genai
except ImportError:
    genai = None


APP_NAME = "Video Translation Studio"
WHISPER_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
SUPPORTED_VIDEO_EXTENSIONS = "*.mp4 *.mkv *.avi *.mov"


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


def run_subprocess(command: list[str], log_cb=None) -> int:
    if log_cb:
        log_cb(f"Running: {' '.join(command)}")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if process.stdout:
        for line in process.stdout:
            if log_cb:
                log_cb(line.rstrip())
    return process.wait()


def get_media_duration(ffprobe_path: Path, media_path: str | Path) -> float:
    command = [
        str(ffprobe_path),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    output = subprocess.check_output(command, text=True).strip()
    return float(output) if output else 0.0


def ensure_whisper_model(model: str, models_dir: Path, log_cb=None) -> Path:
    models_dir.mkdir(parents=True, exist_ok=True)
    model_name = f"ggml-{model}.bin"
    model_path = models_dir / model_name
    if model_path.exists():
        return model_path

    url = f"{WHISPER_BASE_URL}/{model_name}"
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
    target_language: str
    translate_all: bool
    glossary: dict
    enable_tts: bool
    tts_provider: str
    custom_api_url: str
    custom_api_key: str
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


class WorkerThread(QtCore.QThread):
    progress = QtCore.pyqtSignal(int)
    status = QtCore.pyqtSignal(str)
    log = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(str)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
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
            model_path = ensure_whisper_model(settings.whisper_model, get_resource_path("models"), self.log.emit)
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
        else:
            if not settings.srt_path:
                raise ValueError("SRT path is required when using SRT mode.")
            srt_path = Path(settings.srt_path)

        self._step(40, "Translating subtitles")
        with open(srt_path, "r", encoding="utf-8") as handle:
            entries = parse_srt(handle.read())

        translated_entries = self._translate_entries(entries, settings)
        translated_srt = work_dir / "translated.srt"
        with open(translated_srt, "w", encoding="utf-8") as handle:
            handle.write(render_srt(translated_entries))

        tts_audio = None
        if settings.enable_tts:
            self._step(60, "Generating TTS")
            tts_audio = work_dir / "tts_audio.wav"
            self._generate_tts(translated_entries, tts_audio)
            self._apply_audio_fx(tts_audio, settings, ffmpeg_path)

        self._step(80, "Rendering output")
        output_path = work_dir / "output.mp4"
        filters = []
        if settings.enable_subtitles:
            subtitle_filter = self._build_subtitle_filter(translated_srt, settings)
            filters.append(subtitle_filter)
        if settings.blur_fill:
            filters.append(self._build_blur_filter(settings))

        filter_complex = ",".join(filters) if filters else None
        render_command = [
            str(ffmpeg_path),
            "-y",
            "-i",
            settings.video_path,
        ]
        if tts_audio:
            render_command += ["-i", str(tts_audio)]
        if filter_complex:
            render_command += ["-vf", filter_complex]

        if tts_audio:
            render_command += ["-map", "0:v", "-map", "1:a"]
        if settings.mute_original:
            render_command += ["-c:a", "aac", "-b:a", "192k"]
        else:
            duck_volume = max(0.1, settings.duck_audio / 100.0)
            render_command += ["-filter:a", f"volume={duck_volume}"]

        render_command.append(str(output_path))
        if run_subprocess(render_command, self.log.emit) != 0:
            raise RuntimeError("Video rendering failed.")

        self._step(100, "Completed")
        self.finished.emit(str(output_path))

    def _step(self, value: int, message: str):
        self.progress.emit(value)
        self.status.emit(message)

    def _translate_entries(self, entries: list[dict], settings: AppSettings) -> list[dict]:
        if settings.provider == "ChatGPT":
            if openai is None:
                raise RuntimeError("openai package not installed.")
        if settings.provider == "Gemini":
            if genai is None:
                raise RuntimeError("google-generativeai package not installed.")

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

    def _apply_glossary(self, text: str, glossary: dict) -> str:
        for source, target in glossary.items():
            text = text.replace(source, target)
        return text

    def _translate_text(self, text: str, settings: AppSettings) -> str:
        text = self._apply_glossary(text, settings.glossary)
        prompt = (
            f"Translate the following text to {settings.target_language}.\n"
            f"Preserve line breaks and do not add commentary.\n\n{text}"
        )
        if settings.provider == "ChatGPT":
            client = openai.OpenAI()
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content.strip()
        if settings.provider == "Gemini":
            genai.configure()
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)
            return response.text.strip()
        return text

    def _generate_tts(self, entries: list[dict], output_path: Path):
        text = "\n".join(entry["text"] for entry in entries)
        if self.settings.tts_provider == "Edge TTS":
            if edge_tts is None:
                raise RuntimeError("edge-tts package not installed.")

            async def _run():
                communicate = edge_tts.Communicate(text, self.settings.target_language)
                await communicate.save(str(output_path))

            thread = threading.Thread(target=lambda: asyncio.run(_run()), daemon=True)
            thread.start()
            thread.join()
        else:
            if not self.settings.custom_api_url:
                raise ValueError("Custom API URL is required.")
            payload = {
                "text": text,
                "language": self.settings.target_language,
            }
            headers = {}
            if self.settings.custom_api_key:
                headers["Authorization"] = self.settings.custom_api_key
            response = requests.post(
                self.settings.custom_api_url,
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
            with open(output_path, "wb") as handle:
                handle.write(audio_response.content)

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
        return f"subtitles='{srt_path}':force_style='{style}'"

    def _build_blur_filter(self, settings: AppSettings) -> str:
        height_ratio = max(1, min(100, settings.blur_height)) / 100.0
        if settings.blur_mode == "Blur":
            return (
                f"boxblur=luma_radius=10:luma_power=1:"
                f"chroma_radius=10:chroma_power=1"
            )
        color = "black"
        return f"drawbox=y=ih*{1 - height_ratio}:h=ih*{height_ratio}:color={color}:t=fill"


class MergeWorkerThread(QtCore.QThread):
    progress = QtCore.pyqtSignal(int)
    status = QtCore.pyqtSignal(str)
    elapsed = QtCore.pyqtSignal(float)
    log = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(str)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, video_paths: list[str], output_path: str, parent=None):
        super().__init__(parent)
        self.video_paths = video_paths
        self.output_path = output_path

    def run(self):
        try:
            self._run_merge()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))

    def _run_merge(self):
        if len(self.video_paths) < 2:
            raise ValueError("Please select at least 2 videos to merge.")
        if not self.output_path:
            raise ValueError("Output path is required.")

        ffmpeg_path = find_tool("ffmpeg")
        ffprobe_path = find_tool("ffprobe")
        if not ffmpeg_path or not ffprobe_path:
            raise FileNotFoundError("ffmpeg/ffprobe not found in bin/ or tools/ folder.")

        total_duration = sum(get_media_duration(ffprobe_path, path) for path in self.video_paths)

        work_dir = Path(tempfile.mkdtemp(prefix="video_merge_"))
        concat_file = work_dir / "inputs.txt"
        with open(concat_file, "w", encoding="utf-8") as handle:
            for video_path in self.video_paths:
                escaped = video_path.replace("'", "'\\''")
                handle.write(f"file '{escaped}'\n")

        self.status.emit("Merging videos...")
        start_time = time.perf_counter()
        command = [
            str(ffmpeg_path),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            "-progress",
            "pipe:1",
            "-nostats",
            self.output_path,
        ]
        self.log.emit(f"Running: {' '.join(command)}")
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        if process.stdout:
            for line in process.stdout:
                text = line.strip()
                if not text:
                    continue
                if text.startswith("out_time_ms="):
                    out_ms = int(text.split("=", maxsplit=1)[1])
                    elapsed_seconds = out_ms / 1_000_000
                    self.elapsed.emit(elapsed_seconds)
                    if total_duration > 0:
                        progress = min(99, int((elapsed_seconds / total_duration) * 100))
                        self.progress.emit(progress)
                elif text.startswith("progress=") and text.endswith("end"):
                    self.progress.emit(100)
                    final_elapsed = max(total_duration, time.perf_counter() - start_time)
                    self.elapsed.emit(final_elapsed)
                else:
                    self.log.emit(text)

        if process.wait() != 0:
            raise RuntimeError(
                "Video merge failed. Ensure all selected videos use compatible codecs."
            )

        self.status.emit("Completed")
        self.finished.emit(self.output_path)


class GlossaryDialog(QtWidgets.QDialog):
    def __init__(self, glossary: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Glossary")
        self.resize(400, 300)
        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Source", "Target"])
        self.table.horizontalHeader().setStretchLastSection(True)
        for source, target in glossary.items():
            self._add_row(source, target)

        add_button = QtWidgets.QPushButton("Add")
        remove_button = QtWidgets.QPushButton("Remove")
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )

        add_button.clicked.connect(lambda: self._add_row("", ""))
        remove_button.clicked.connect(self._remove_selected)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.table)
        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(add_button)
        controls.addWidget(remove_button)
        controls.addStretch()
        layout.addLayout(controls)
        layout.addWidget(button_box)

    def _add_row(self, source: str, target: str):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(source))
        self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(target))

    def _remove_selected(self):
        for index in sorted({item.row() for item in self.table.selectedItems()}, reverse=True):
            self.table.removeRow(index)

    def get_glossary(self) -> dict:
        glossary = {}
        for row in range(self.table.rowCount()):
            source_item = self.table.item(row, 0)
            target_item = self.table.item(row, 1)
            if source_item and target_item:
                source = source_item.text().strip()
                target = target_item.text().strip()
                if source:
                    glossary[source] = target
        return glossary


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 750)
        self.glossary = {}
        self.worker = None
        self.merge_worker = None

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._build_translation_tab(), "Translate Video")
        self.tabs.addTab(self._build_merge_tab(), "Merge Video")
        layout.addWidget(self.tabs)

        self._update_source_mode()
        self._update_tts_provider()

    def _build_translation_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.addWidget(self._build_input_section())
        layout.addWidget(self._build_translation_section())
        layout.addWidget(self._build_tts_section())
        layout.addWidget(self._build_subtitle_section())
        layout.addWidget(self._build_post_section())
        layout.addWidget(self._build_execution_section())
        return tab

    def _build_merge_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)

        controls_layout = QtWidgets.QHBoxLayout()
        add_button = QtWidgets.QPushButton("Add Videos")
        add_button.clicked.connect(self._add_merge_videos)
        remove_button = QtWidgets.QPushButton("Remove Selected")
        remove_button.clicked.connect(self._remove_merge_video)
        clear_button = QtWidgets.QPushButton("Clear")
        clear_button.clicked.connect(self._clear_merge_videos)
        controls_layout.addWidget(add_button)
        controls_layout.addWidget(remove_button)
        controls_layout.addWidget(clear_button)
        controls_layout.addStretch()

        self.merge_list = QtWidgets.QListWidget()
        self.merge_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.merge_list.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.InternalMove)
        self.merge_list.setDefaultDropAction(QtCore.Qt.DropAction.MoveAction)
        self.merge_list.setAlternatingRowColors(True)
        self.merge_list.setToolTip("Drag and drop to reorder videos before merging")

        output_layout = QtWidgets.QHBoxLayout()
        self.merge_output_edit = QtWidgets.QLineEdit()
        self.merge_output_edit.setPlaceholderText("Output file path (*.mp4)")
        output_button = QtWidgets.QPushButton("Choose Output")
        output_button.clicked.connect(self._browse_merge_output)
        output_layout.addWidget(QtWidgets.QLabel("Output:"))
        output_layout.addWidget(self.merge_output_edit)
        output_layout.addWidget(output_button)

        self.merge_start_button = QtWidgets.QPushButton("Merge All")
        self.merge_start_button.clicked.connect(self._start_merge)
        self.merge_progress_bar = QtWidgets.QProgressBar()
        self.merge_status_label = QtWidgets.QLabel("Idle")
        self.merge_elapsed_label = QtWidgets.QLabel("Elapsed: 00:00")
        self.merge_log_console = QtWidgets.QTextEdit()
        self.merge_log_console.setReadOnly(True)

        layout.addLayout(controls_layout)
        layout.addWidget(self.merge_list)
        layout.addLayout(output_layout)
        layout.addWidget(self.merge_start_button)
        layout.addWidget(self.merge_progress_bar)
        layout.addWidget(self.merge_elapsed_label)
        layout.addWidget(self.merge_status_label)
        layout.addWidget(self.merge_log_console)
        return tab

    def _build_input_section(self):
        group = QtWidgets.QGroupBox("1. Input")
        layout = QtWidgets.QGridLayout(group)

        self.video_path_edit = QtWidgets.QLineEdit()
        browse_button = QtWidgets.QPushButton("Browse Video")
        browse_button.clicked.connect(self._browse_video)

        self.asr_radio = QtWidgets.QRadioButton("ASR (Automatic Speech Recognition)")
        self.srt_radio = QtWidgets.QRadioButton("SRT File")
        self.asr_radio.setChecked(True)
        self.asr_radio.toggled.connect(self._update_source_mode)

        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.addItems(["tiny", "base", "small", "medium", "large"])
        self.source_lang_combo = QtWidgets.QComboBox()
        self.source_lang_combo.addItems(["auto", "en", "vi", "zh", "ja", "ko", "fr", "de", "es"])

        self.srt_path_edit = QtWidgets.QLineEdit()
        srt_button = QtWidgets.QPushButton("Browse SRT")
        srt_button.clicked.connect(self._browse_srt)

        layout.addWidget(QtWidgets.QLabel("Video:"), 0, 0)
        layout.addWidget(self.video_path_edit, 0, 1)
        layout.addWidget(browse_button, 0, 2)
        layout.addWidget(self.asr_radio, 1, 0, 1, 3)
        layout.addWidget(QtWidgets.QLabel("Whisper Model:"), 2, 0)
        layout.addWidget(self.model_combo, 2, 1)
        layout.addWidget(QtWidgets.QLabel("Source Language:"), 2, 2)
        layout.addWidget(self.source_lang_combo, 2, 3)
        layout.addWidget(self.srt_radio, 3, 0, 1, 3)
        layout.addWidget(self.srt_path_edit, 4, 1)
        layout.addWidget(srt_button, 4, 2)
        return group

    def _build_translation_section(self):
        group = QtWidgets.QGroupBox("2. Translation")
        layout = QtWidgets.QGridLayout(group)

        self.provider_combo = QtWidgets.QComboBox()
        self.provider_combo.addItems(["ChatGPT", "Gemini"])
        self.target_lang_combo = QtWidgets.QComboBox()
        self.target_lang_combo.addItems(["en", "vi", "zh-cn", "ja", "ko", "fr", "de", "es"])
        self.translate_all_checkbox = QtWidgets.QCheckBox("Translate All at Once")
        self.translate_all_checkbox.setChecked(True)
        glossary_button = QtWidgets.QPushButton("Edit Glossary")
        glossary_button.clicked.connect(self._edit_glossary)

        layout.addWidget(QtWidgets.QLabel("Provider:"), 0, 0)
        layout.addWidget(self.provider_combo, 0, 1)
        layout.addWidget(QtWidgets.QLabel("Target Language:"), 0, 2)
        layout.addWidget(self.target_lang_combo, 0, 3)
        layout.addWidget(self.translate_all_checkbox, 1, 0, 1, 2)
        layout.addWidget(glossary_button, 1, 2)
        return group

    def _build_tts_section(self):
        group = QtWidgets.QGroupBox("3. Dubbing (TTS)")
        layout = QtWidgets.QGridLayout(group)
        self.tts_enable_checkbox = QtWidgets.QCheckBox("Enable TTS")
        self.tts_provider_combo = QtWidgets.QComboBox()
        self.tts_provider_combo.addItems(["Edge TTS", "Custom API"])
        self.tts_provider_combo.currentTextChanged.connect(self._update_tts_provider)
        self.api_url_edit = QtWidgets.QLineEdit()
        self.api_key_edit = QtWidgets.QLineEdit()
        self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.speed_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.speed_slider.setMinimum(50)
        self.speed_slider.setMaximum(200)
        self.speed_slider.setValue(100)
        self.pitch_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.pitch_slider.setMinimum(-12)
        self.pitch_slider.setMaximum(12)
        self.pitch_slider.setValue(0)

        layout.addWidget(self.tts_enable_checkbox, 0, 0)
        layout.addWidget(QtWidgets.QLabel("Provider:"), 1, 0)
        layout.addWidget(self.tts_provider_combo, 1, 1)
        layout.addWidget(QtWidgets.QLabel("Custom API URL:"), 2, 0)
        layout.addWidget(self.api_url_edit, 2, 1)
        layout.addWidget(QtWidgets.QLabel("API Key:"), 2, 2)
        layout.addWidget(self.api_key_edit, 2, 3)
        layout.addWidget(QtWidgets.QLabel("Speed:"), 3, 0)
        layout.addWidget(self.speed_slider, 3, 1)
        layout.addWidget(QtWidgets.QLabel("Pitch:"), 3, 2)
        layout.addWidget(self.pitch_slider, 3, 3)
        return group

    def _build_subtitle_section(self):
        group = QtWidgets.QGroupBox("4. Subtitles")
        layout = QtWidgets.QGridLayout(group)
        self.subtitle_enable_checkbox = QtWidgets.QCheckBox("Enable Subtitles")
        self.subtitle_enable_checkbox.setChecked(True)
        self.font_family_edit = QtWidgets.QLineEdit("Arial")
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

        layout.addWidget(self.subtitle_enable_checkbox, 0, 0)
        layout.addWidget(QtWidgets.QLabel("Font Family:"), 1, 0)
        layout.addWidget(self.font_family_edit, 1, 1)
        layout.addWidget(QtWidgets.QLabel("Font Size:"), 1, 2)
        layout.addWidget(self.font_size_spin, 1, 3)
        layout.addWidget(QtWidgets.QLabel("Text Color:"), 2, 0)
        layout.addWidget(self.text_color_button, 2, 1)
        layout.addWidget(self.text_color_display, 2, 2)
        layout.addWidget(QtWidgets.QLabel("Border Width:"), 2, 3)
        layout.addWidget(self.border_width_spin, 2, 4)
        layout.addWidget(QtWidgets.QLabel("Position:"), 3, 0)
        layout.addWidget(self.position_combo, 3, 1)
        return group

    def _build_post_section(self):
        group = QtWidgets.QGroupBox("5. Post-Processing")
        layout = QtWidgets.QGridLayout(group)
        self.mute_radio = QtWidgets.QRadioButton("Mute Original")
        self.duck_radio = QtWidgets.QRadioButton("Duck Audio")
        self.mute_radio.setChecked(True)
        self.duck_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.duck_slider.setMinimum(1)
        self.duck_slider.setMaximum(100)
        self.duck_slider.setValue(30)
        self.blur_checkbox = QtWidgets.QCheckBox("Blur/Fill Bottom")
        self.blur_height_spin = QtWidgets.QSpinBox()
        self.blur_height_spin.setRange(5, 50)
        self.blur_height_spin.setValue(20)
        self.blur_mode_combo = QtWidgets.QComboBox()
        self.blur_mode_combo.addItems(["Blur", "Solid Color Fill"])

        layout.addWidget(self.mute_radio, 0, 0)
        layout.addWidget(self.duck_radio, 0, 1)
        layout.addWidget(QtWidgets.QLabel("Duck Volume:"), 0, 2)
        layout.addWidget(self.duck_slider, 0, 3)
        layout.addWidget(self.blur_checkbox, 1, 0)
        layout.addWidget(QtWidgets.QLabel("Height %:"), 1, 1)
        layout.addWidget(self.blur_height_spin, 1, 2)
        layout.addWidget(self.blur_mode_combo, 1, 3)
        return group

    def _build_execution_section(self):
        group = QtWidgets.QGroupBox("6. Execution & Status")
        layout = QtWidgets.QGridLayout(group)
        self.cuda_checkbox = QtWidgets.QCheckBox("Enable CUDA Acceleration")
        self.start_button = QtWidgets.QPushButton("Start")
        self.start_button.clicked.connect(self._start_processing)
        self.progress_bar = QtWidgets.QProgressBar()
        self.status_label = QtWidgets.QLabel("Idle")
        self.log_console = QtWidgets.QTextEdit()
        self.log_console.setReadOnly(True)

        layout.addWidget(self.cuda_checkbox, 0, 0)
        layout.addWidget(self.start_button, 0, 1)
        layout.addWidget(self.progress_bar, 1, 0, 1, 2)
        layout.addWidget(self.status_label, 2, 0, 1, 2)
        layout.addWidget(self.log_console, 3, 0, 1, 2)
        return group

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

    def _add_merge_videos(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select Videos to Merge",
            "",
            f"Video Files ({SUPPORTED_VIDEO_EXTENSIONS})",
        )
        for path in paths:
            self.merge_list.addItem(path)

    def _remove_merge_video(self):
        for item in self.merge_list.selectedItems():
            self.merge_list.takeItem(self.merge_list.row(item))

    def _clear_merge_videos(self):
        self.merge_list.clear()

    def _browse_merge_output(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Merged Video",
            "merged_output.mp4",
            "MP4 Video (*.mp4)",
        )
        if path:
            self.merge_output_edit.setText(path)

    def _select_color(self):
        color = QtWidgets.QColorDialog.getColor()
        if color.isValid():
            self.text_color_display.setText(color.name())

    def _edit_glossary(self):
        dialog = GlossaryDialog(self.glossary, self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.glossary = dialog.get_glossary()

    def _update_source_mode(self):
        asr_enabled = self.asr_radio.isChecked()
        self.model_combo.setEnabled(asr_enabled)
        self.source_lang_combo.setEnabled(asr_enabled)
        self.srt_path_edit.setEnabled(not asr_enabled)

    def _update_tts_provider(self):
        use_custom = self.tts_provider_combo.currentText() == "Custom API"
        self.api_url_edit.setVisible(use_custom)
        self.api_key_edit.setVisible(use_custom)

    def _start_processing(self):
        if self.worker and self.worker.isRunning():
            QtWidgets.QMessageBox.warning(self, "Busy", "Processing already running.")
            return

        settings = AppSettings(
            video_path=self.video_path_edit.text().strip(),
            source_mode="ASR" if self.asr_radio.isChecked() else "SRT",
            whisper_model=self.model_combo.currentText(),
            source_language=self.source_lang_combo.currentText(),
            srt_path=self.srt_path_edit.text().strip(),
            provider=self.provider_combo.currentText(),
            target_language=self.target_lang_combo.currentText(),
            translate_all=self.translate_all_checkbox.isChecked(),
            glossary=self.glossary,
            enable_tts=self.tts_enable_checkbox.isChecked(),
            tts_provider=self.tts_provider_combo.currentText(),
            custom_api_url=self.api_url_edit.text().strip(),
            custom_api_key=self.api_key_edit.text().strip(),
            speed=self.speed_slider.value() / 100.0,
            pitch=float(self.pitch_slider.value()),
            enable_subtitles=self.subtitle_enable_checkbox.isChecked(),
            font_family=self.font_family_edit.text().strip(),
            font_size=self.font_size_spin.value(),
            text_color=self.text_color_display.text().strip(),
            border_width=self.border_width_spin.value(),
            position=self.position_combo.currentText(),
            mute_original=self.mute_radio.isChecked(),
            duck_audio=self.duck_slider.value(),
            blur_fill=self.blur_checkbox.isChecked(),
            blur_height=self.blur_height_spin.value(),
            blur_mode=self.blur_mode_combo.currentText(),
            cuda=self.cuda_checkbox.isChecked(),
        )

        self.worker = WorkerThread(settings)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.status.connect(self.status_label.setText)
        self.worker.log.connect(self._append_log)
        self.worker.finished.connect(self._handle_finished)
        self.worker.failed.connect(self._handle_failed)
        self.worker.start()

    def _start_merge(self):
        if self.merge_worker and self.merge_worker.isRunning():
            QtWidgets.QMessageBox.warning(self, "Busy", "Merge process already running.")
            return

        video_paths = [self.merge_list.item(index).text() for index in range(self.merge_list.count())]
        output_path = self.merge_output_edit.text().strip()
        if not output_path:
            QtWidgets.QMessageBox.warning(self, "Missing Output", "Please choose an output file.")
            return

        self.merge_progress_bar.setValue(0)
        self.merge_status_label.setText("Starting...")
        self.merge_elapsed_label.setText("Elapsed: 00:00")

        self.merge_worker = MergeWorkerThread(video_paths, output_path)
        self.merge_worker.progress.connect(self.merge_progress_bar.setValue)
        self.merge_worker.status.connect(self.merge_status_label.setText)
        self.merge_worker.elapsed.connect(self._update_merge_elapsed)
        self.merge_worker.log.connect(self._append_merge_log)
        self.merge_worker.finished.connect(self._handle_merge_finished)
        self.merge_worker.failed.connect(self._handle_merge_failed)
        self.merge_worker.start()

    def _append_log(self, text: str):
        self.log_console.append(text)

    def _append_merge_log(self, text: str):
        self.merge_log_console.append(text)

    def _update_merge_elapsed(self, seconds: float):
        minutes = int(seconds // 60)
        remain_seconds = int(seconds % 60)
        self.merge_elapsed_label.setText(f"Elapsed: {minutes:02d}:{remain_seconds:02d}")

    def _handle_finished(self, output_path: str):
        self.status_label.setText("Done")
        QtWidgets.QMessageBox.information(self, "Completed", f"Output saved to {output_path}")

    def _handle_failed(self, error: str):
        self.status_label.setText("Failed")
        QtWidgets.QMessageBox.critical(self, "Error", error)

    def _handle_merge_finished(self, output_path: str):
        self.merge_status_label.setText("Done")
        QtWidgets.QMessageBox.information(self, "Merge Completed", f"Output saved to {output_path}")

    def _handle_merge_failed(self, error: str):
        self.merge_status_label.setText("Failed")
        QtWidgets.QMessageBox.critical(self, "Merge Error", error)


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
