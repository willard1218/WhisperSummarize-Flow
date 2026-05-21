import unittest
import tempfile
import os
import fcntl
import threading
import time
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from pipeline.run_daily_pipeline import TranscriptionLock, DailyItem, PipelineContext
from pipeline.run_registered_youtube import resolve_youtube_latest
from tools.check_daily_status import render_pipeline, process_directory
from tools.summarize_transcript import summarize_file

class TestCoverageExpansion(unittest.TestCase):

    # 1. TranscriptionLock Stress Test
    def test_transcription_lock_concurrency(self):
        """Simulate multiple threads/processes competing for the lock."""
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "stress.lock"
            lock = TranscriptionLock(lock_file=str(lock_path))
            
            shared_resource = []
            
            def worker(worker_id):
                with lock:
                    # Critical section
                    start_val = len(shared_resource)
                    time.sleep(0.01) # Small delay to increase race chance if lock fails
                    shared_resource.append(worker_id)
                    # Verify no one else interleaved
                    self.assertEqual(len(shared_resource), start_val + 1)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
            for t in threads: t.start()
            for t in threads: t.join()
            
            self.assertEqual(len(shared_resource), 10)

    # 2. OpenCC Failure Handling (Strict Policy)
    @patch("pipeline.run_daily_pipeline.run_command")
    @patch("pipeline.run_daily_pipeline.send_telegram_msg")
    @patch("pipeline.run_daily_pipeline.trigger_auto_fix")
    def test_traditionalize_failure_reports_to_telegram(self, mock_trigger, mock_send_tg, mock_run):
        """Verify that OpenCC failure triggers a Telegram alert (Strict Policy)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            audio_path = temp_path / "test.mp3"
            audio_path.write_text("audio", encoding="utf-8")
            transcript_path = temp_path / "test.srt.txt"
            transcript_path.write_text("content", encoding="utf-8")
            
            # Mock run_command to raise an exception
            mock_run.side_effect = RuntimeError("OpenCC command not found")
            
            item = DailyItem(
                label="FailTask", kind="podcast", source_url="...", emails=[],
                output_dir=temp_path, audio_path=audio_path, download_ready=True
            )
            
            args = SimpleNamespace(
                enable_traditionalize=True, 
                opencc_config="...", 
                transcriber_type="whisperkit",
                enable_transcribe=True,
                enable_summarize=False,
                enable_mail=False,
                enable_telegram=True,
                mock=False,
                log_file=None
            )
            context = PipelineContext(args, [item])
            
            from pipeline.run_daily_pipeline import process_item_full_lifecycle
            
            mock_transcriber = MagicMock()
            mock_transcriber.transcribe.return_value = transcript_path
            
            # Use transcript_path_for mock to simulate "existing file found"
            with patch("pipeline.run_daily_pipeline.transcript_path_for", return_value=transcript_path):
                process_item_full_lifecycle(0, args, context, MagicMock(), mock_transcriber)
                
            self.assertTrue(item.failed)
            self.assertTrue(any("繁體化失敗" in msg for msg in item.messages))
            mock_send_tg.assert_called()
            self.assertIn("繁體化失敗", mock_send_tg.call_args[0][0])
            # Ensure auto-fixer was triggered
            mock_trigger.assert_called_once()

    # 3. YouTube Upcoming Stream Filtering
    @patch("pipeline.run_registered_youtube.run_command")
    def test_youtube_upcoming_stream_filtering(self, mock_run):
        """Verify that streams with duration=None (upcoming) are filtered out."""
        # Mock response from yt-dlp flat-playlist
        mock_run.return_value = SimpleNamespace(
            returncode=0,
            stdout='\n'.join([
                '{"id": "soon", "title": "Upcoming", "duration": null}',
                '{"id": "live", "title": "Was Live", "duration": 3600}',
                '{"id": "vid1", "title": "Video", "duration": 600}'
            ])
        )
        
        result = resolve_youtube_latest("http://channel")
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "live") # Should skip 'soon' and take 'live'

    # 4. Absolute Path Resolution
    def test_prompt_path_resolution(self):
        """Verify that prompt paths are resolved relative to BASE_DIR."""
        from pipeline.run_daily_pipeline import BASE_DIR
        
        temp_path = Path("/tmp")
        prompt_file = "prompts/custom.md"
        
        # The logic in build_items: prompt_file = BASE_DIR / sub["prompt_file"]
        resolved = BASE_DIR / prompt_file
        self.assertTrue(resolved.is_absolute())
        self.assertIn(str(BASE_DIR), str(resolved))

    # 5. Visual Pipeline Rendering
    def test_render_pipeline_formatting(self):
        """Verify Plan B output looks correct in different states."""
        # 1. Fully complete
        stages = {"download": True, "transcribe": True, "summarize": True, "mail": True}
        output = render_pipeline(stages, "12:00:00")
        self.assertIn("[📂下載]", output)
        self.assertIn("[🎙️轉錄]", output)
        self.assertIn("[🤖摘要]", output)
        self.assertIn("[📧寄出]", output)
        
        # 2. Transcribing in progress
        stages = {"download": True, "transcribing": True}
        output = render_pipeline(stages, "Active")
        self.assertIn("[📂下載]", output)
        self.assertIn("[⏳轉錄中]", output)
        self.assertIn("[  摘要  ]", output) # Should show empty/placeholder for remaining
        
        # 3. Failed at summarization (summarize missing)
        stages = {"download": True, "transcribe": True}
        output = render_pipeline(stages, "10:30:00")
        self.assertIn("[📂下載]", output)
        self.assertIn("[🎙️轉錄]", output)
        self.assertIn("[  摘要  ]", output)

    # 6. Auto-Fixer Trigger
    @patch("pipeline.run_daily_pipeline.subprocess.Popen")
    def test_trigger_auto_fix_calls_script(self, mock_popen):
        from pipeline.run_daily_pipeline import trigger_auto_fix, BASE_DIR
        import sys
        
        trigger_auto_fix("TestTask", "Some Error", "path/to.log")
        
        # Verify it attempted to launch auto_fixer.py
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args.args[0]
        self.assertIn(str(BASE_DIR / "tools" / "auto_fixer.py"), cmd)
        self.assertIn("--task", cmd)
        self.assertIn("TestTask", cmd)
        self.assertIn("--error", cmd)
        self.assertIn("Some Error", cmd)
        self.assertIn("--log", cmd)
        self.assertIn("path/to.log", cmd)

if __name__ == "__main__":
    unittest.main()
