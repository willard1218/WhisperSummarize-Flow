from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pipeline.run_daily_pipeline import DailyItem, YouTubeDownloader, build_items
from pipeline.run_registered_youtube import YouTubeSyncResult


class RunDailyPipelineTests(unittest.TestCase):
    @patch("pipeline.run_daily_pipeline.resolve_emails", return_value=["user@example.com"])
    @patch("pipeline.run_daily_pipeline.load_recipient_groups", return_value={})
    def test_build_items_creates_youtube_adhoc_task(self, _load_groups, _resolve_emails) -> None:
        args = SimpleNamespace(
            recipient_config="config/recipient_groups.local.json",
            recipient_group="all",
            url="https://youtube.com/watch?v=abc123",
            local_file=None,
            podcast_config="config/subscriptions.json",
            youtube_config="config/youtube_subscriptions.json",
            task_origin="default",
        )

        items = build_items(args, Path("/tmp/output-root"))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].kind, "youtube")
        self.assertEqual(items[0].output_dir, Path("/tmp/output-root/adhoc_yt_abc123"))

    @patch("pipeline.run_daily_pipeline.resolve_emails", return_value=["user@example.com"])
    @patch("pipeline.run_daily_pipeline.load_recipient_groups", return_value={})
    def test_build_items_creates_local_file_task(self, _load_groups, _resolve_emails) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local_file = Path(temp_dir) / "voice.m4a"
            local_file.write_text("", encoding="utf-8")
            args = SimpleNamespace(
                recipient_config="config/recipient_groups.local.json",
                recipient_group="all",
                url=None,
                local_file=str(local_file),
                podcast_config="config/subscriptions.json",
                youtube_config="config/youtube_subscriptions.json",
                task_origin="default",
            )

            items = build_items(args, Path("/tmp/output-root"))

            self.assertEqual(items[0].kind, "voice")
            self.assertEqual(items[0].audio_path, local_file.resolve())
            self.assertTrue(items[0].download_ready)

    @patch("pipeline.run_daily_pipeline.resolve_emails", return_value=["user@example.com"])
    @patch("pipeline.run_daily_pipeline.load_recipient_groups", return_value={})
    def test_build_items_routes_telegram_youtube_to_telegram_tree(self, _load_groups, _resolve_emails) -> None:
        args = SimpleNamespace(
            recipient_config="config/recipient_groups.local.json",
            recipient_group="all",
            url="https://youtube.com/watch?v=abc123",
            local_file=None,
            podcast_config="config/subscriptions.json",
            youtube_config="config/youtube_subscriptions.json",
            task_origin="telegram",
        )

        items = build_items(args, Path("/tmp/output-root"))

        self.assertEqual(items[0].output_dir, Path("/tmp/output-root/telegram/youtube/abc123"))

    @patch("pipeline.run_daily_pipeline.sync_youtube_latest")
    def test_youtube_downloader_reuses_existing_audio(self, mock_sync) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            existing_audio = output_dir / "title__abc123.mp3"
            
            mock_sync.return_value = YouTubeSyncResult(
                audio_path=existing_audio,
                success=True,
                already_exists=True
            )
            
            item = DailyItem(
                label="Ad-hoc Task",
                kind="youtube",
                source_url="https://youtube.com/watch?v=abc123",
                emails=[],
                output_dir=output_dir,
            )

            ok = YouTubeDownloader().download(item, SimpleNamespace(args=SimpleNamespace(url=None)))

            self.assertTrue(ok)
            self.assertEqual(item.audio_path, existing_audio)
            self.assertTrue(item.download_ready)
            mock_sync.assert_called_once()

    @patch("pipeline.run_daily_pipeline.sync_youtube_latest")
    def test_youtube_adhoc_download_does_not_use_global_archive(self, mock_sync) -> None:
        item = DailyItem(
            label="Ad-hoc Task",
            kind="youtube",
            source_url="https://youtube.com/watch?v=abc123",
            emails=[],
            output_dir=Path("/tmp/output-root/telegram/youtube/abc123"),
        )
        mock_sync.return_value = YouTubeSyncResult(success=True)

        YouTubeDownloader().download(
            item,
            SimpleNamespace(args=SimpleNamespace(url=item.source_url)),
        )

        # verify use_archive=False was passed to sync_youtube_latest
        self.assertFalse(mock_sync.call_args.kwargs["use_archive"])


if __name__ == "__main__":
    unittest.main()
