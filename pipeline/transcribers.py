import subprocess
import os
import json
from pathlib import Path
from abc import ABC, abstractmethod

class BaseTranscriber(ABC):
    @abstractmethod
    def transcribe(self, audio_path: Path, output_dir: Path) -> Path | None:
        """Transcribes the audio file and returns the path to the resulting transcript."""
        pass

class WhisperCPPTranscriber(BaseTranscriber):
    def __init__(self, script_path: str):
        self.script_path = script_path

    def transcribe(self, audio_path: Path, output_dir: Path) -> Path | None:
        # Currently gensrt.sh handles the lock, but run_daily_pipeline also has a lock.
        # To maintain compatibility, we just call the script.
        res = subprocess.run([self.script_path, str(audio_path)])
        if res.returncode == 0:
            # gensrt.sh produces .srt.txt
            transcript = audio_path.with_suffix(".srt.txt")
            if transcript.exists():
                return transcript
        return None

class WhisperKitTranscriber(BaseTranscriber):
    def __init__(self, bin_path: str, model_path: str = None):
        self.bin_path = bin_path
        self.model_path = model_path
        self.ffmpeg_bin = os.environ.get("FFMPEG_BIN", "ffmpeg")

    def transcribe(self, audio_path: Path, output_dir: Path) -> Path | None:
        # 1. Convert to WAV (16kHz mono)
        wav_path = audio_path.with_suffix(".wav")
        if not wav_path.exists():
            subprocess.run([
                self.ffmpeg_bin, "-y", "-i", str(audio_path),
                "-ac", "1", "-ar", "16000", str(wav_path)
            ], check=True, capture_output=True)

        # 2. Run WhisperKit
        cmd = [
            self.bin_path, "transcribe",
            "--audio-path", str(wav_path),
            "--diarization",
            "--language", "zh"
        ]
        if self.model_path:
            cmd += ["--model-path", self.model_path]
        
        # WhisperKit often outputs to the current directory or a report directory.
        # We'll use --report-path to keep it organized.
        report_dir = output_dir / "reports"
        report_dir.mkdir(exist_ok=True)
        cmd += ["--report-path", str(report_dir), "--report"]

        print(f"Running WhisperKit: {' '.join(cmd)}")
        res = subprocess.run(cmd)
        
        # WhisperKit produces a JSON report. We need to find it and convert it to .srt.txt and .txt
        # for the rest of the pipeline to work.
        # The report is usually named after the audio file.
        # Example: reports/<audio_name>/transcription.json or reports/<audio_name>.json
        audio_stem = wav_path.stem
        report_json = report_dir / audio_stem / "transcription.json"
        
        if not report_json.exists():
            report_json = report_dir / f"{audio_stem}.json"
        
        if res.returncode == 0 and report_json.exists():
            return self._process_report(report_json, audio_path)
        
        return None

    def _process_report(self, report_json: Path, original_audio: Path) -> Path | None:
        try:
            data = json.loads(report_json.read_text(encoding="utf-8"))
            return WhisperKitReportWriter(original_audio).write(data.get("segments", []))
        except Exception as e:
            print(f"Error processing WhisperKit report: {e}")
            return None


class WhisperKitReportWriter:
    def __init__(self, original_audio: Path):
        self.original_audio = original_audio

    @staticmethod
    def format_time(seconds_value: float) -> str:
        hours = int(seconds_value // 3600)
        minutes = int((seconds_value % 3600) // 60)
        seconds = int(seconds_value % 60)
        millis = int((seconds_value - int(seconds_value)) * 1000)
        return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"

    @staticmethod
    def render_line(segment: dict) -> str:
        text = segment.get("text", "").strip()
        speaker = segment.get("speaker")
        return f"[{speaker}] {text}" if speaker else text

    def write(self, segments: list[dict]) -> Path:
        srt_path = self.original_audio.with_suffix(".srt.txt")
        txt_path = self.original_audio.with_suffix(".txt")

        with open(srt_path, "w", encoding="utf-8") as srt_handle, open(txt_path, "w", encoding="utf-8") as txt_handle:
            for index, segment in enumerate(segments, 1):
                start = segment.get("start", 0)
                end = segment.get("end", 0)
                line_text = self.render_line(segment)

                srt_handle.write(f"{index}\n")
                srt_handle.write(f"{self.format_time(start)} --> {self.format_time(end)}\n")
                srt_handle.write(f"{line_text}\n\n")
                txt_handle.write(f"{line_text}\n")

        return srt_path
