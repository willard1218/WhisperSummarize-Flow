from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.output_paths import (
    podcast_directory_name,
    subscribed_podcast_output_dir,
    subscribed_youtube_output_dir,
    telegram_media_output_dir,
    telegram_url_output_dir,
    youtube_channel_directory_name,
    youtube_video_id,
)


class OutputPathTests(unittest.TestCase):
    def test_podcast_subscription_uses_podcast_title(self) -> None:
        root = Path("/tmp/output")
        result = subscribed_podcast_output_dir(root, "My Podcast")
        self.assertEqual(result, Path("/tmp/output/podcast/My Podcast"))

    def test_youtube_subscription_uses_handle_name(self) -> None:
        root = Path("/tmp/output")
        result = subscribed_youtube_output_dir(root, "https://www.youtube.com/@Alansays/streams")
        self.assertEqual(result, Path("/tmp/output/youtube/Alansays"))

    def test_telegram_youtube_url_uses_video_id(self) -> None:
        root = Path("/tmp/output")
        result = telegram_url_output_dir(root, "https://youtube.com/watch?v=SBH44qRxxKU")
        self.assertEqual(result, Path("/tmp/output/telegram/youtube/SBH44qRxxKU"))

    def test_telegram_apple_podcast_url(self) -> None:
        root = Path("/tmp/output")
        result = telegram_url_output_dir(root, "https://podcasts.apple.com/tw/podcast/id1552655948")
        self.assertEqual(result, Path("/tmp/output/telegram/apple_podcast/id1552655948"))

    def test_telegram_soundon_podcast_url(self) -> None:
        root = Path("/tmp/output")
        result = telegram_url_output_dir(root, "https://player.soundon.fm/p/uuid/episodes/episode-id")
        self.assertEqual(result, Path("/tmp/output/telegram/soundon_podcast/episode-id"))

    def test_telegram_media_dir_buckets_audio_and_video(self) -> None:
        root = Path("/tmp/output")
        audio_dir = telegram_media_output_dir(root, "audio", "meeting note.m4a")
        video_dir = telegram_media_output_dir(root, "video", "screen.mov")

        self.assertIn("/telegram/audio/", str(audio_dir))
        self.assertIn("/telegram/video/", str(video_dir))

    def test_metadata_write_and_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir)
            from tools.output_paths import write_task_metadata, update_task_metadata
            
            # Initial write
            write_task_metadata(path, {"a": 1, "b": 2})
            meta_file = path / "metadata.json"
            self.assertTrue(meta_file.exists())
            
            # Update
            update_task_metadata(path, {"b": 3, "c": 4})
            import json
            data = json.loads(meta_file.read_text())
            self.assertEqual(data["a"], 1)
            self.assertEqual(data["b"], 3)
            self.assertEqual(data["c"], 4)


if __name__ == "__main__":
    unittest.main()
