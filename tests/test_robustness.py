import unittest
import tempfile
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch, MagicMock

from tools.registry import Registry
from tools.summarize_transcript import summarize_file
from pipeline.run_registered_podcasts import resolve_podcast_title, sync_podcast_latest

class TestRobustness(unittest.TestCase):

    # 1. Registry (SQLite) Tests
    def test_registry_basic_ops(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test_tasks.db"
            registry = Registry(db_path)
            
            task_id = "test-task-1"
            self.assertFalse(registry.is_processed(task_id))
            
            registry.mark_processed(task_id, source_type="youtube", title="Test Video")
            self.assertTrue(registry.is_processed(task_id))
            
            # Check data persistence
            registry2 = Registry(db_path)
            self.assertTrue(registry2.is_processed(task_id))

    def test_registry_migration_from_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test_tasks.db"
            txt_path = Path(temp_dir) / "legacy.txt"
            txt_path.write_text("youtube task-yt\npodcast task-pod\nstandalone-task", encoding="utf-8")
            
            registry = Registry(db_path)
            registry.migrate_from_text(txt_path)
            
            self.assertTrue(registry.is_processed("task-yt"))
            self.assertTrue(registry.is_processed("task-pod"))
            self.assertTrue(registry.is_processed("standalone-task"))

    # 2. Podcast RSS Edge Cases
    @patch("pipeline.run_registered_podcasts.fetch_bytes")
    def test_resolve_podcast_title_empty_rss(self, mock_fetch):
        mock_bytes = b"<?xml version='1.0' encoding='UTF-8'?><rss><channel></channel></rss>"
        mock_fetch.return_value = (mock_bytes, "text/xml")
        title = resolve_podcast_title("http://empty-rss")
        self.assertEqual(title, "")

    @patch("pipeline.run_registered_podcasts.download_single_podcast")
    def test_sync_podcast_latest_no_episode(self, mock_download):
        from pipeline.run_registered_podcasts import NO_EPISODE_EXIT_CODE
        mock_download.return_value = MagicMock(returncode=NO_EPISODE_EXIT_CODE)
        
        res = sync_podcast_latest("http://url", Path("/tmp"), None)
        self.assertTrue(res.skipped)
        self.assertFalse(res.success)

    # 3. Summarization Placeholder Robustness
    @patch("tools.summarize_transcript.get_summarizers")
    def test_summarize_file_missing_placeholder(self, mock_get_sum):
        """Test if summarization behaves predictably when placeholder is missing from template."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            txt_file = temp_path / "test.txt"
            txt_file.write_text("Actual content", encoding="utf-8")
            
            # Template WITHOUT {transcript_content}
            prompt_file = temp_path / "broken_template.md"
            prompt_file.write_text("Summarize this: [No Placeholder Here]", encoding="utf-8")
            
            mock_sum = MagicMock()
            mock_sum.is_available.return_value = True
            mock_sum.summarize.return_value = "Summary result"
            mock_get_sum.return_value = [mock_sum]
            
            summary_path = summarize_file(txt_file, prompt_file=prompt_file)
            
            self.assertIsNotNone(summary_path)
            # Verify the prompt passed to summarizer didn't have the transcript
            mock_sum.summarize.assert_called_with("Summarize this: [No Placeholder Here]")

    # 4. Path Traversal Guard (Minimal)
    def test_get_task_title_strips_path_info(self):
        from tools.check_daily_status import get_task_title
        # Even if someone tries to inject paths, get_task_title should handle it as a string
        title = get_task_title("../../../etc/passwd.mp3")
        self.assertEqual(title, "../../../etc/passwd")

if __name__ == "__main__":
    unittest.main()
