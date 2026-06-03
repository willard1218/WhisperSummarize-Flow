from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pipeline.transcribers import WhisperCPPTranscriber, WhisperKitReportWriter, WhisperKitTranscriber


class TranscriberTests(unittest.TestCase):
    def test_whisper_cpp_transcriber_returns_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "sample.mp3"
            transcript_path = Path(temp_dir) / "sample.srt.txt"
            audio_path.write_text("", encoding="utf-8")
            transcript_path.write_text("ok", encoding="utf-8")

            transcriber = WhisperCPPTranscriber("/tmp/gensrt.sh")
            with patch("pipeline.transcribers.subprocess.run", return_value=SimpleNamespace(returncode=0)):
                result = transcriber.transcribe(audio_path, Path(temp_dir))

            self.assertEqual(result, transcript_path)

    def test_whisperkit_report_writer_renders_srt_and_plain_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "clip.mp3"
            audio_path.write_text("", encoding="utf-8")

            writer = WhisperKitReportWriter(audio_path)
            srt_path = writer.write(
                [
                    {"start": 0.0, "end": 1.25, "text": "hello"},
                    {"start": 1.5, "end": 2.0, "text": "world", "speaker": "A"},
                ]
            )

            self.assertEqual(srt_path.read_text(encoding="utf-8").splitlines()[1], "00:00:00,000 --> 00:00:01,250")
            self.assertIn("[A] world", audio_path.with_suffix(".txt").read_text(encoding="utf-8"))

    @patch("pipeline.transcribers.subprocess.Popen")
    def test_whisperkit_transcriber_processes_json_report(self, mock_popen) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            audio_path = output_dir / "clip.mp3"
            wav_path = output_dir / "clip.wav"
            report_dir = output_dir / "reports" / "clip"
            report_dir.mkdir(parents=True)
            audio_path.write_text("", encoding="utf-8")
            wav_path.write_text("", encoding="utf-8")
            (report_dir / "transcription.json").write_text(
                json.dumps({"segments": [{"start": 0, "end": 1, "text": "hello"}]}),
                encoding="utf-8",
            )

            # Setup mock Popen
            mock_proc = mock_popen.return_value
            mock_proc.stdout = [] # Empty list as an iterable
            mock_proc.wait.return_value = 0
            mock_proc.returncode = 0

            transcriber = WhisperKitTranscriber("/tmp/whisperkit")
            result = transcriber.transcribe(audio_path, output_dir)

            self.assertEqual(result, output_dir / "clip.srt.txt")
            self.assertTrue((output_dir / "clip.txt").exists())

    @patch("pipeline.transcribers.subprocess.Popen")
    def test_whisperkit_transcriber_captures_stdout_diarization(self, mock_popen) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            audio_path = output_dir / "clip.mp3"
            wav_path = output_dir / "clip.wav"
            audio_path.write_text("", encoding="utf-8")
            wav_path.write_text("", encoding="utf-8")

            # Setup mock Popen with diarization lines (including spaces in filename)
            mock_proc = mock_popen.return_value
            mock_proc.stdout = [
                "SPEAKER Podcast Title - EP01.wav 1 1.000 2.000 Hello world <NA> A <NA> <NA>\n"
            ]
            mock_proc.wait.return_value = 0
            mock_proc.returncode = 0

            transcriber = WhisperKitTranscriber("/tmp/whisperkit")
            result = transcriber.transcribe(audio_path, output_dir)

            self.assertEqual(result, output_dir / "clip.srt.txt")
            txt_content = (output_dir / "clip.txt").read_text()
            self.assertIn("[A] Hello world", txt_content)

    def test_whisperkit_report_writer_splits_huge_segment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "huge.mp3"
            audio_path.write_text("", encoding="utf-8")

            # One huge segment for 10 minutes
            huge_text = "This is a very long text " * 100 
            writer = WhisperKitReportWriter(audio_path)
            # Ensure it splits even without word-level timestamps
            srt_path = writer.write([{"start": 0.0, "end": 600.0, "text": huge_text, "speaker": "A"}])
            
            content = srt_path.read_text()
            indices = [line for line in content.splitlines() if line.isdigit()]
            # Should have multiple entries
            self.assertGreater(len(indices), 10)
            # Each entry should be reasonably short
            for line in content.splitlines():
                if "-->" not in line and not line.isdigit() and line.strip():
                    self.assertLessEqual(len(line), 100) # Including [A] and some margin

    @patch("pipeline.transcribers.subprocess.Popen")
    def test_whisperkit_transcriber_merges_json_and_stdout_speakers(self, mock_popen) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            audio_path = output_dir / "merge.mp3"
            wav_path = output_dir / "merge.wav"
            report_dir = output_dir / "reports" / "merge"
            report_dir.mkdir(parents=True)
            audio_path.write_text("", encoding="utf-8")
            wav_path.write_text("", encoding="utf-8")
            
            # JSON has 2 segments but no speakers
            (report_dir / "transcription.json").write_text(
                json.dumps({"segments": [
                    {"start": 0.0, "end": 30.0, "text": "This is a very long first segment that should not be merged."},
                    {"start": 30.0, "end": 60.0, "text": "This is a very long second segment that should also stand alone."}
                ]}),
                encoding="utf-8",
            )

            # Stdout has 1 speaker line covering the whole duration
            mock_proc = mock_popen.return_value
            mock_proc.stdout = [
                "SPEAKER merge.wav 1 0.000 60.000 <NA> <NA> SPEAKER_01 <NA> <NA>\n"
            ]
            mock_proc.wait.return_value = 0
            mock_proc.returncode = 0

            transcriber = WhisperKitTranscriber("/tmp/whisperkit")
            result = transcriber.transcribe(audio_path, output_dir)

            self.assertEqual(result, output_dir / "merge.srt.txt")
            content = result.read_text()
            # Verify speaker assignment and that splitting occurred
            self.assertIn("[SPEAKER_01]", content)
            self.assertIn("This is a", content)
            self.assertIn("long", content)
            indices = [line for line in content.splitlines() if line.isdigit()]
            self.assertGreater(len(indices), 1)


if __name__ == "__main__":
    unittest.main()
