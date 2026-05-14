from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pipeline.run_daily_pipeline import DailyItem, YouTubeDownloader, build_items


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

    @patch("pipeline.run_daily_pipeline.download_youtube_video")
    def test_youtube_downloader_reuses_existing_audio(self, download_youtube_video) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            existing_audio = output_dir / "title__abc123.mp3"
            existing_audio.write_text("", encoding="utf-8")
            item = DailyItem(
                label="Ad-hoc Task",
                kind="youtube",
                source_url="https://youtube.com/watch?v=abc123",
                emails=[],
                output_dir=output_dir,
            )

            ok = YouTubeDownloader().download(item, SimpleNamespace())

            self.assertTrue(ok)
            self.assertEqual(item.audio_path, existing_audio)
            self.assertTrue(item.download_ready)
            download_youtube_video.assert_not_called()


if __name__ == "__main__":
    unittest.main()
