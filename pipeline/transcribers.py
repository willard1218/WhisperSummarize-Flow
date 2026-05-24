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
    def transcribe(self, audio_path: Path, output_dir: Path) -> tuple[Path, int] | None:
        """Transcribes the audio file and returns (transcript_path, speaker_count)."""
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

    def transcribe(self, audio_path: Path, output_dir: Path) -> tuple[Path, int] | None:
        # Currently gensrt.sh handles the lock, but run_daily_pipeline also has a lock.
        # To maintain compatibility, we just call the script.
        res = subprocess.run([self.script_path, str(audio_path)])
        if res.returncode == 0:
            # gensrt.sh produces .srt.txt
            transcript = audio_path.with_suffix(".srt.txt")
            if transcript.exists():
                return transcript, 1
        return None

class WhisperKitTranscriber(BaseTranscriber):
    def __init__(self, bin_path: str, model_path: str = None):
        self.bin_path = bin_path
        self.model_path = model_path
        self.ffmpeg_bin = os.environ.get("FFMPEG_BIN", "ffmpeg")

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
                "--language", "zh"
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
            
            result_data = None
            if segments:
                result_data = WhisperKitReportWriter(audio_path).write(segments)
                logger.info(f"Transcription finished segments={len(segments)} output=\"{result_data[0].name}\" speakers={result_data[1]}", action="transcribe_ok")
            else:
                audio_stem = wav_path.stem
                report_json = report_dir / audio_stem / "transcription.json"
                if not report_json.exists():
                    report_json = report_dir / f"{audio_stem}.json"
                
                if process.returncode == 0 and report_json.exists():
                    result_data = self._process_report(report_json, audio_path)
            
            return result_data
            
        finally:
            # Cleanup intermediate WAV file if it was created from a different source
            if is_temporary_wav and wav_path.exists():
                logger.info(f"Cleaning up intermediate WAV: {wav_path.name}", action="cleanup")
                wav_path.unlink(missing_ok=True)


    def _process_report(self, report_json: Path, original_audio: Path) -> tuple[Path, int] | None:
        try:
            data = json.loads(report_json.read_text(encoding="utf-8"))
            result_data = WhisperKitReportWriter(original_audio).write(data.get("segments", []))
            logger.info(f"Transcription report processed path=\"{result_data[0].name}\" speakers={result_data[1]}", action="report_ok")
            return result_data
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

    def write(self, segments: list[dict]) -> tuple[Path, int]:
        srt_path = self.original_audio.with_suffix(".srt.txt")
        txt_path = self.original_audio.with_suffix(".txt")

        # 1. Flatten into sub-segments based on words, speaker changes and gaps
        processed_entries = self._resegment(segments)
        
        # Only show speakers if there's more than one
        unique_speakers = {e.get("speaker") for e in processed_entries if e.get("speaker")}
        speaker_count = len(unique_speakers)
        show_speakers = speaker_count > 1

        with open(srt_path, "w", encoding="utf-8") as srt_handle, open(txt_path, "w", encoding="utf-8") as txt_handle:
            for index, entry in enumerate(processed_entries, 1):
                start = entry["start"]
                end = entry["end"]
                text = entry["text"]
                speaker = entry.get("speaker")
                
                line_text = f"[{speaker}] {text}" if (speaker and show_speakers) else text

                srt_handle.write(f"{index}\n")
                srt_handle.write(f"{self.format_time(start)} --> {self.format_time(end)}\n")
                srt_handle.write(f"{line_text}\n\n")
                txt_handle.write(f"{line_text}\n")

        return srt_path, speaker_count

    def _resegment(self, segments: list[dict]) -> list[dict]:
        # 1. Flatten all words across all segments in sequence
        all_words = []
        for seg in segments:
            speaker = seg.get("speaker")
            raw_words = seg.get("words", [])
            for rw in raw_words:
                w_text = rw.get("word", "").strip()
                if not w_text: continue
                all_words.append({"text": w_text, "speaker": speaker})

        if not all_words:
            results = []
            for seg in segments: results.extend(self._split_text_segment(seg))
            return results

        # 2. Global Linearized Timeline Reconstruction
        total_chars = sum(len(w["text"]) for w in all_words)
        total_duration = max(segments[-1].get("end", 0.0), 1.0) if segments else 1.0
        
        reconstructed_words = []
        elapsed_chars = 0
        for w in all_words:
            char_len = len(w["text"])
            w_start = (elapsed_chars / total_chars) * total_duration
            elapsed_chars += char_len
            w_end = (elapsed_chars / total_chars) * total_duration
            reconstructed_words.append({
                "text": w["text"],
                "start": round(w_start, 3),
                "end": round(w_end, 3),
                "speaker": w["speaker"]
            })

        # 3. Group into SRT entries with high-quality breaking rules
        must_glue_suffixes = r"(一個|一个|的|了|是|在|和|與|与|為|为|或|及)$"
        date_pattern = r"\d+[年月日至號号]"
        punct_to_strip = r"^[，。,．？！,.\?! \t]+"
        import re

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
            
            # Rule D: Length Hard Limits (with Glue Check)
            if not should_split:
                # If current word is English, check if it's the start of something that should be kept
                # (Simple heuristic: don't break between two English words)
                is_english = bool(re.search(r'[A-Za-z]$', w_text))
                is_next_english = False
                if i + 1 < len(reconstructed_words):
                    is_next_english = bool(re.search(r'^[A-Za-z]', reconstructed_words[i+1]["text"]))

                if current_len >= self.max_chars or current_duration >= self.max_duration:
                    is_glued = bool(re.search(must_glue_suffixes, w_text))
                    is_date = bool(re.search(date_pattern, w_text))
                    
                    if not is_glued and not is_date and not (is_english and is_next_english):
                        should_split = True
                    elif current_len > 40: # Hard limit
                        should_split = True

            if should_split:
                text = "".join(x["text"] for x in current_words).strip()
                # Remove leading punctuation
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

