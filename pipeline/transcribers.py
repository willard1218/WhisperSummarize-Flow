import subprocess
import time
import os
import sys
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
        import json
        try:
            with open(report_json, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            segments = data.get("segments", [])
            
            # Generate .srt.txt format
            srt_path = original_audio.with_suffix(".srt.txt")
            txt_path = original_audio.with_suffix(".txt")
            
            with open(srt_path, 'w', encoding='utf-8') as fs, open(txt_path, 'w', encoding='utf-8') as ft:
                for i, seg in enumerate(segments, 1):
                    start = seg.get("start", 0)
                    end = seg.get("end", 0)
                    text = seg.get("text", "").strip()
                    speaker = seg.get("speaker", None)
                    
                    # Format time: 00:00:00,000
                    def format_time(s):
                        hours = int(s // 3600)
                        minutes = int((s % 3600) // 60)
                        seconds = int(s % 60)
                        millis = int((s - int(s)) * 1000)
                        return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"
                    
                    line_text = text
                    if speaker:
                        line_text = f"[{speaker}] {text}"
                    
                    fs.write(f"{i}\n")
                    fs.write(f"{format_time(start)} --> {format_time(end)}\n")
                    fs.write(f"{line_text}\n\n")
                    
                    ft.write(f"{line_text}\n")
            
            return srt_path
        except Exception as e:
            print(f"Error processing WhisperKit report: {e}")
            return None
