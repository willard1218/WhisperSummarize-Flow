import subprocess
import os
import json
import re
from pathlib import Path
from abc import ABC, abstractmethod
from tools.logger import get_logger

logger = get_logger("transcribers")

class BaseTranscriber(ABC):
    @abstractmethod
    def transcribe(self, audio_path: Path, output_dir: Path) -> Path | None:
        """Transcribes the audio file and returns the path to the resulting transcript."""
        pass

    @staticmethod
    def get_audio_duration(audio_path: Path) -> str:
        """Returns the duration of the audio file in HH:MM:SS format using ffprobe."""
        ffprobe_bin = os.environ.get("FFPROBE_BIN") or os.environ.get("WS_FFPROBE_BIN") or "ffprobe"
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
        self.ffmpeg_bin = os.environ.get("FFMPEG_BIN") or os.environ.get("WS_FFMPEG_BIN") or "ffmpeg"

    def transcribe(self, audio_path: Path, output_dir: Path) -> Path | None:
        # 1. Convert to WAV (16kHz mono)
        wav_path = audio_path.with_suffix(".wav")
        is_temporary_wav = (wav_path != audio_path)
        
        try:
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
            ]
            if self.model_path:
                cmd += ["--model-path", self.model_path]
            
            report_dir = output_dir / "reports"
            report_dir.mkdir(exist_ok=True)
            cmd += ["--report-path", str(report_dir), "--report"]

            logger.info(f"Running WhisperKit command=\"{' '.join(cmd)}\"", action="transcribe_start")
            
            segments = []
            speaker_pattern = re.compile(
                r"^SPEAKER\s+"
                r".+?\s+"
                r"\d+\s+"
                r"(\d+(?:\.\d+)?)\s+"
                r"(\d+(?:\.\d+)?)\s+"
                r"(.+?)\s+"
                r"<NA>\s+(\S+)\s+"
                r"<NA>\s+<NA>"
            )
            
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            if process.stdout:
                for line in process.stdout:
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
            
            # Prefer JSON report for better timing and granularity, but use captured segments for diarization
            audio_stem = wav_path.stem
            report_json = report_dir / audio_stem / "transcription.json"
            if not report_json.exists():
                report_json = report_dir / f"{audio_stem}.json"

            if process.returncode == 0 and report_json.exists():
                json_segments = self._load_json_segments(report_json)
                if segments:
                    # Enrich JSON segments with speaker info from captured segments
                    json_segments = self._enrich_with_speakers(json_segments, segments)
                return WhisperKitReportWriter(audio_path).write(json_segments)
            
            if segments:
                return WhisperKitReportWriter(audio_path).write(segments)
            
            return None
            
        finally:
            # Cleanup intermediate WAV file if it was created from a different source
            if is_temporary_wav and wav_path.exists():
                logger.info(f"Cleaning up intermediate WAV: {wav_path.name}", action="cleanup")
                wav_path.unlink(missing_ok=True)

    def _load_json_segments(self, report_json: Path) -> list[dict]:
        try:
            data = json.loads(report_json.read_text(encoding="utf-8"))
            return data.get("segments", [])
        except Exception as e:
            logger.error(f"Error loading WhisperKit report: {e}", action="report_load_error")
            return []

    def _enrich_with_speakers(self, target_segments: list[dict], speaker_segments: list[dict]) -> list[dict]:
        """Assign speaker IDs to target segments based on best time overlap with speaker segments."""
        if not speaker_segments: return target_segments
        
        for tseg in target_segments:
            t_start = tseg.get("start", 0.0)
            t_end = tseg.get("end", 0.0)
            
            best_speaker = None
            max_overlap = -1.0
            
            for sseg in speaker_segments:
                s_start = sseg.get("start", 0.0)
                s_end = sseg.get("end", 0.0)
                
                # Calculate overlap duration
                overlap_start = max(t_start, s_start)
                overlap_end = min(t_end, s_end)
                overlap = max(0, overlap_end - overlap_start)
                
                if overlap > max_overlap:
                    max_overlap = overlap
                    best_speaker = sseg.get("speaker")
            
            if best_speaker:
                tseg["speaker"] = best_speaker
                
        return target_segments

    def _process_report(self, report_json: Path, original_audio: Path) -> Path | None:
        """Deprecated: Use _load_json_segments and WhisperKitReportWriter directly."""
        segments = self._load_json_segments(report_json)
        if not segments: return None
        return WhisperKitReportWriter(original_audio).write(segments)


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
        # 1. Reconstruct word-level timestamps anchored to segments for better sync
        reconstructed_words = []
        for seg in segments:
            seg_start = seg.get("start", 0.0)
            seg_end = seg.get("end", 0.0)
            seg_speaker = seg.get("speaker")
            raw_words = seg.get("words", [])
            
            # Strip Whisper special tokens from segment text
            seg_text = seg.get("text", "").strip()
            seg_text = re.sub(r"<\|.+?\|>", "", seg_text)
            
            if not raw_words:
                # Fallback: Split large coarse segments into manageable pieces
                if seg_text:
                    split_pieces = self._split_text_segment({
                        "text": seg_text,
                        "start": seg_start,
                        "end": seg_end,
                        "speaker": seg_speaker
                    })
                    reconstructed_words.extend(split_pieces)
                continue
                
            seg_text_len = sum(len(rw.get("word", "").strip()) for rw in raw_words)
            if seg_text_len == 0: continue
            
            seg_duration = seg_end - seg_start
            elapsed_chars = 0
            for rw in raw_words:
                w_text = rw.get("word", "").strip()
                w_text = re.sub(r"<\|.+?\|>", "", w_text)
                if not w_text: continue
                char_len = len(w_text)
                w_start = seg_start + (elapsed_chars / seg_text_len) * seg_duration
                elapsed_chars += char_len
                w_end = seg_start + (elapsed_chars / seg_text_len) * seg_duration
                reconstructed_words.append({
                    "text": w_text,
                    "start": round(w_start, 3),
                    "end": round(w_end, 3),
                    "speaker": seg_speaker
                })


        if not reconstructed_words:
            return []

        # 2. Group into SRT entries with high-quality breaking rules
        must_glue_suffixes = r"(一個|一个|的|了|是|在|和|與|与|為|为|或|及|其|於|于|之|者|也|已|又|但|而|著|着|就|都|到)$"
        date_pattern = r"\d+[年月日至號号]"
        punct_to_strip = r"^[，。,．？！,.\?! \t]+"

        entries = []

        current_words = []
        chunk_start = reconstructed_words[0]["start"]

        for i, w in enumerate(reconstructed_words):
            current_words.append(w)
            w_text = w["text"]
            w_end = w["end"]
            w_speaker = w["speaker"]
            
            should_split = False
            current_len = sum(len(x["text"]) for x in current_words)
            current_duration = w_end - chunk_start
            
            # Rule A: Speaker change
            if i + 1 < len(reconstructed_words):
                if reconstructed_words[i+1]["speaker"] != w_speaker:
                    should_split = True
            
            # Rule B: Punctuation (Strong split)
            if not should_split and current_len >= 12:
                if any(p in w_text for p in "。！？"):
                    should_split = True
            
            # Rule C: Comma (Medium split)
            if not should_split and current_len >= 20:
                if any(p in w_text for p in "，,"):
                    should_split = True
            
            # Rule D: Length Hard Limits (with Glue Check and Fragment lookahead)
            if not should_split:
                is_english = bool(re.search(r'[A-Za-z]$', w_text))
                is_next_english = (i + 1 < len(reconstructed_words) and bool(re.search(r'^[A-Za-z]', reconstructed_words[i+1]["text"])))
                
                if current_len >= self.max_chars or current_duration >= self.max_duration:
                    is_glued = bool(re.search(must_glue_suffixes, w_text))
                    is_date = bool(re.search(date_pattern, w_text))
                    
                    if not is_glued and not is_date and not (is_english and is_next_english):
                        # LOOKAHEAD: Avoid leaving tiny fragments (< 6 chars) hanging in next line
                        remaining_chars = 0
                        for j in range(i + 1, min(i + 6, len(reconstructed_words))):
                            if reconstructed_words[j]["speaker"] == w_speaker:
                                if any(p in reconstructed_words[j]["text"] for p in "。！？，,"): break
                                remaining_chars += len(reconstructed_words[j]["text"])
                            else: break
                        
                        if remaining_chars > 0 and remaining_chars < 6 and current_len < 45:
                            # Swallow the fragment into current line instead of splitting
                            should_split = False
                        else:
                            should_split = True
                    elif current_len > 50: # Absolute hard limit
                        should_split = True

            if should_split:
                text = "".join(x["text"] for x in current_words).strip()
                text = re.sub(punct_to_strip, "", text)
                if text:
                    entries.append({"start": chunk_start, "end": w_end, "text": text, "speaker": w_speaker})
                current_words = []
                if i + 1 < len(reconstructed_words):
                    chunk_start = reconstructed_words[i+1]["start"]

        if current_words:
            text = "".join(x["text"] for x in current_words).strip()
            text = re.sub(punct_to_strip, "", text)
            if text:
                # Small optimization: If previous entry for same speaker exists and we are tiny, merge back
                if entries and entries[-1]["speaker"] == current_words[0]["speaker"] and len(text) < 5 and len(entries[-1]["text"]) < 40:
                    entries[-1]["text"] += text
                    entries[-1]["end"] = current_words[-1]["end"]
                else:
                    entries.append({"start": chunk_start, "end": current_words[-1]["end"], "text": text, "speaker": current_words[-1]["speaker"]})

        # Final pass: Ensure global monotonicity across segments
        final_entries = []
        last_global_end = 0.0
        for entry in entries:
            entry["start"] = max(entry["start"], last_global_end)
            entry["end"] = max(entry["end"], entry["start"] + 0.01)
            final_entries.append(entry)
            last_global_end = entry["end"]

        return final_entries

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
