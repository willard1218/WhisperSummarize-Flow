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

    def test_whisperkit_transcriber_processes_json_report(self) -> None:
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

            transcriber = WhisperKitTranscriber("/tmp/whisperkit")
            with patch("pipeline.transcribers.subprocess.run", return_value=SimpleNamespace(returncode=0)):
                result = transcriber.transcribe(audio_path, output_dir)

            self.assertEqual(result, output_dir / "clip.srt.txt")
            self.assertTrue((output_dir / "clip.txt").exists())


if __name__ == "__main__":
    unittest.main()
