import subprocess
import os
import json
import re
from pathlib import Path
from abc import ABC, abstractmethod

class BaseTranscriber(ABC):
    @abstractmethod
    def transcribe(self, audio_path: Path, output_dir: Path) -> Path | None:
        """Transcribes the audio file and returns the path to the resulting transcript."""
        pass

    @staticmethod
    def get_audio_duration(audio_path: Path) -> str:
        """Returns the duration of the audio file in HH:MM:SS format using ffprobe."""
        ffprobe_bin = os.environ.get("FFPROBE_BIN", "ffprobe")
        try:
            cmd = [
                ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)
            ]
            output = subprocess.check_output(cmd, text=True).strip()
            total_seconds = float(output)
            
            hours = int(total_seconds // 3600)
            minutes = int((total_seconds % 3600) // 60)
            seconds = int(total_seconds % 60)
            return f"{hours:02}:{minutes:02}:{seconds:02}"
        except Exception as e:
            print(f"Error getting audio duration: {e}")
            return "00:00:00"

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
        should_convert = not wav_path.exists()
        if not should_convert:
            # Check if source is newer
            if audio_path.stat().st_mtime > wav_path.stat().st_mtime:
                should_convert = True
        
        if should_convert:
            print(f"[FFmpeg] Converting to 16kHz WAV: {audio_path.name}")
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
        
        segments = []
        speaker_pattern = re.compile(r"^SPEAKER\s+\S+\s+\d+\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(.+?)\s+<NA>\s+(\S+)\s+<NA>\s+<NA>")
        
        # Stream output to console while capturing speaker segments
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        if process.stdout:
            for line in process.stdout:
                print(line, end="")
                match = speaker_pattern.match(line.strip())
                if match:
                    start = float(match.group(1))
                    duration = float(match.group(2))
                    segments.append({
                        "start": start,
                        "end": start + duration,
                        "text": match.group(3).strip(),
                        "speaker": match.group(4)
                    })
        
        process.wait()
        
        if segments:
            # Successfully captured speaker info from stdout
            return WhisperKitReportWriter(audio_path).write(segments)

        # Fallback: WhisperKit produces a JSON report. We need to find it and convert it to .srt.txt and .txt
        # Example: reports/<audio_name>/transcription.json or reports/<audio_name>.json
        audio_stem = wav_path.stem
        report_json = report_dir / audio_stem / "transcription.json"
        
        if not report_json.exists():
            report_json = report_dir / f"{audio_stem}.json"
        
        if process.returncode == 0 and report_json.exists():
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
