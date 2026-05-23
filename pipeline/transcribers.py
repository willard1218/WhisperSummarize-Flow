import subprocess
import os
import json
import re
from pathlib import Path
from abc import ABC, abstractmethod
from logger import get_logger

logger = get_logger("transcribers")

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
            logger.error(f"Error getting audio duration: {e}", action="duration_error")
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
            logger.info(f"Converting to 16kHz WAV: {audio_path.name}", action="ffmpeg_convert")
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

        logger.info(f"Running WhisperKit command=\"{' '.join(cmd)}\"", action="transcribe_start")
        
        segments = []
        # Improved regex to handle filenames and text with spaces
        # Format: SPEAKER [FILENAME] [ID] [START] [DURATION] [TEXT] <NA> [SPEAKER_ID] <NA> <NA>
        speaker_pattern = re.compile(
            r"^SPEAKER\s+"          # Start
            r".+?\s+"               # Filename (non-greedy, skip)
            r"\d+\s+"               # Stream ID (skip)
            r"(\d+(?:\.\d+)?)\s+"   # Start time (Group 1)
            r"(\d+(?:\.\d+)?)\s+"   # Duration (Group 2)
            r"(.+?)\s+"             # Text content (Group 3)
            r"<NA>\s+(\S+)\s+"      # Speaker ID (Group 4)
            r"<NA>\s+<NA>"          # Tail
        )
        
        # Capture output but don't stream every line to stdout
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        if process.stdout:
            for line in process.stdout:
                # We still need to process lines to build segments for WhisperKitReportWriter
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
            result_path = WhisperKitReportWriter(audio_path).write(segments)
            logger.info(f"Transcription finished segments={len(segments)} output=\"{result_path.name}\"", action="transcribe_ok")
            return result_path

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
            result_path = WhisperKitReportWriter(original_audio).write(data.get("segments", []))
            logger.info(f"Transcription report processed path=\"{result_path.name}\"", action="report_ok")
            return result_path
        except Exception as e:
            logger.error(f"Error processing WhisperKit report: {e}", action="report_error")
            return None


class WhisperKitReportWriter:
    def __init__(self, original_audio: Path):
        self.original_audio = original_audio
        self.max_chars = 30      # Max characters per SRT line
        self.max_duration = 5.0  # Max duration (seconds) per SRT line
        self.gap_threshold = 1.0 # Max gap (seconds) before splitting

    @staticmethod
    def format_time(seconds_value: float) -> str:
        hours = int(seconds_value // 3600)
        minutes = int((seconds_value % 3600) // 60)
        seconds = int(seconds_value % 60)
        millis = int((seconds_value - int(seconds_value)) * 1000)
        return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"

    def write(self, segments: list[dict]) -> Path:
        srt_path = self.original_audio.with_suffix(".srt.txt")
        txt_path = self.original_audio.with_suffix(".txt")

        # 1. Flatten into sub-segments based on words, speaker changes and gaps
        processed_entries = self._resegment(segments)

        with open(srt_path, "w", encoding="utf-8") as srt_handle, open(txt_path, "w", encoding="utf-8") as txt_handle:
            for index, entry in enumerate(processed_entries, 1):
                start = entry["start"]
                end = entry["end"]
                text = entry["text"]
                speaker = entry.get("speaker")
                
                line_text = f"[{speaker}] {text}" if speaker else text

                srt_handle.write(f"{index}\n")
                srt_handle.write(f"{self.format_time(start)} --> {self.format_time(end)}\n")
                srt_handle.write(f"{line_text}\n\n")
                txt_handle.write(f"{line_text}\n")

        return srt_path

    def _resegment(self, segments: list[dict]) -> list[dict]:
        entries = []
        
        for seg in segments:
            speaker = seg.get("speaker")
            words = seg.get("words", [])
            
            if not words:
                entries.extend(self._split_text_segment(seg))
                continue

            current_words = []
            chunk_start = words[0].get("start", 0)
            
            for i, w in enumerate(words):
                w_start = w.get("start", 0)
                w_end = w.get("end", 0)
                w_text = w.get("word", "").strip()
                if not w_text: continue
                
                # Always add current word first (to ensure no orphan if we split)
                current_words.append({"text": w_text, "end": w_end, "start": w_start})
                
                # Check split conditions AFTER adding the word
                should_split = False
                current_len = sum(len(x["text"]) for x in current_words)
                current_duration = w_end - chunk_start
                
                # Rule 1: Gap to NEXT word > 0.8s
                if i + 1 < len(words):
                    next_w = words[i+1]
                    if next_w.get("start", 0) - w_end >= 0.8:
                        should_split = True
                
                # Rule 2: Punctuation split (if long enough)
                if not should_split and current_len >= 15:
                    if any(p in w_text for p in "。！？，,"):
                        should_split = True
                        
                # Rule 3: Max length/duration safety net
                if not should_split:
                    if current_len >= self.max_chars or current_duration >= self.max_duration:
                        should_split = True

                if should_split:
                    entries.append({
                        "start": chunk_start,
                        "end": w_end,
                        "text": "".join(x["text"] for x in current_words).replace("  ", " ").strip(),
                        "speaker": speaker
                    })
                    current_words = []
                    if i + 1 < len(words):
                        chunk_start = words[i+1].get("start", 0)

            # Final flush
            if current_words:
                entries.append({
                    "start": chunk_start,
                    "end": current_words[-1]["end"],
                    "text": "".join(x["text"] for x in current_words).replace("  ", " ").strip(),
                    "speaker": speaker
                })

        return entries

    def _split_text_segment(self, seg: dict) -> list[dict]:
        """Simple fallback splitter for segments without word timestamps."""
        text = seg.get("text", "").strip()
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        speaker = seg.get("speaker")
        duration = end - start
        
        if len(text) <= self.max_chars and duration <= self.max_duration:
            return [{"start": start, "end": end, "text": text, "speaker": speaker}]
            
        # Rough split by character count if too long
        results = []
        num_chunks = max(int(len(text) / self.max_chars) + 1, int(duration / self.max_duration) + 1)
        chars_per_chunk = len(text) // num_chunks
        time_per_chunk = duration / num_chunks
        
        for i in range(num_chunks):
            chunk_text = text[i*chars_per_chunk : (i+1)*chars_per_chunk].strip()
            if not chunk_text: continue
            results.append({
                "start": start + i*time_per_chunk,
                "end": start + (i+1)*time_per_chunk,
                "text": chunk_text,
                "speaker": speaker
            })
        return results

