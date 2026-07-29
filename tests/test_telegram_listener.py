from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.telegram_listener import (
    ListenerSettings,
    MediaMessage,
    MessageInterpreter,
    PipelineLauncher,
    TelegramPoller,
    TelegramUpdateHandler,
    TranscriptionStatusProvider,
)
from tools.registry import Registry


class FakeApiClient:
    def __init__(self):
        self.sent_messages: list[tuple[str | int, str, dict | None]] = []
        self.answered: list[tuple[str, str | None]] = []
        self.edited: list[tuple[str | int, int, dict | None]] = []
        self.updates: dict | None = None

    def send_message(self, chat_id, text, reply_markup=None):  # noqa: ANN001
        self.sent_messages.append((chat_id, text, reply_markup))
        return {"ok": True}

    def answer_callback_query(self, callback_query_id, text=None):  # noqa: ANN001
        self.answered.append((callback_query_id, text))
        return {"ok": True}

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):  # noqa: ANN001
        self.edited.append((chat_id, message_id, reply_markup))
        return {"ok": True}

    def get_updates(self, offset, timeout=30):  # noqa: ANN001
        return self.updates


class FakeLauncher:
    def __init__(self):
        self.calls: list[dict] = []

    def run(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)


class FakeDownloader:
    def __init__(self, should_succeed: bool = True):
        self.should_succeed = should_succeed
        self.calls: list[tuple[str, Path]] = []

    def download(self, file_id: str, destination: Path) -> bool:
        self.calls.append((file_id, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.should_succeed:
            destination.write_text("audio", encoding="utf-8")
        return self.should_succeed


class FakeStatusProvider:
    def __init__(self, description: str):
        self.description = description

    def describe(self) -> str:
        return self.description


class TelegramListenerTests(unittest.TestCase):
    def make_handler(self, temp_dir: str, status: str = "空閒中"):
        settings = ListenerSettings(
            base_dir=Path(temp_dir),
            bot_token="token",
            owner_chat_id="owner",
            python_executable="python3",
        )
        api_client = FakeApiClient()
        launcher = FakeLauncher()
        downloader = FakeDownloader()
        registry = Registry(Path(temp_dir) / "tasks.db")
        handler = TelegramUpdateHandler(
            settings=settings,
            api_client=api_client,
            status_provider=FakeStatusProvider(status),
            pipeline_launcher=launcher,
            file_downloader=downloader,
            registry=registry,
        )
        return handler, api_client, launcher, downloader, registry

    def test_status_command_reports_current_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler, api_client, _, _, _ = self.make_handler(temp_dir, status="忙碌中")
            handler.handle({"message": {"chat": {"id": "owner"}, "text": "/status"}})

            self.assertIn("目前系統狀態：\n忙碌中", api_client.sent_messages[0][1])

    def test_url_message_enqueues_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler, api_client, _, _, registry = self.make_handler(temp_dir, status="忙碌中")
            url = "https://youtube.com/watch?v=abc"
            handler.handle(
                {"message": {"chat": {"id": "owner"}, "text": url}}
            )

            chat_id, text, reply_markup = api_client.sent_messages[0]
            self.assertEqual(chat_id, "owner")
            self.assertIn("✅ 已收到網址並加入排隊", text)
            self.assertIn(url, text)
            self.assertIsNone(reply_markup)

            task = registry.get_next_pending_task()
            self.assertIsNotNone(task)
            self.assertEqual(task["task_type"], "url")
            self.assertEqual(task["payload"], url)

    def test_apple_podcast_url_enqueues_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler, api_client, _, _, registry = self.make_handler(temp_dir)
            url = "https://podcasts.apple.com/tw/podcast/id123"
            handler.handle(
                {"message": {"chat": {"id": "owner"}, "text": url}}
            )

            self.assertIn("✅ 已收到網址並加入排隊", api_client.sent_messages[0][1])
            self.assertIn(url, api_client.sent_messages[0][1])

            task = registry.get_next_pending_task()
            self.assertIsNotNone(task)
            self.assertEqual(task["task_type"], "url")
            self.assertEqual(task["payload"], url)

    def test_media_message_downloads_and_enqueues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler, api_client, launcher, downloader, registry = self.make_handler(temp_dir)
            handler.handle(
                {
                    "message": {
                        "message_id": 100,
                        "chat": {"id": "owner"},
                        "audio": {"file_id": "file-1", "file_name": "clip.mp3", "duration": 12},
                    }
                }
            )

            self.assertEqual(downloader.calls[0][0], "file-1")
            self.assertIn("收到媒體：clip.mp3", api_client.sent_messages[0][1])
            self.assertIn("✅ 下載完成，已加入排隊", api_client.sent_messages[1][1])
            metadata_path = Path(temp_dir) / "output" / "telegram"
            self.assertTrue(any(path.name == "metadata.json" for path in metadata_path.rglob("metadata.json")))

            task = registry.get_next_pending_task()
            self.assertIsNotNone(task)
            self.assertEqual(task["task_type"], "file")
            self.assertTrue(str(task["payload"]).endswith("clip.mp3"))

    def test_unauthorized_chat_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler, api_client, launcher, _, _ = self.make_handler(temp_dir)
            handler.handle({"message": {"chat": {"id": "stranger"}, "text": "/status"}})

            self.assertEqual(api_client.sent_messages, [])
            self.assertEqual(launcher.calls, [])

    def test_callback_query_is_answered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler, api_client, _, _, _ = self.make_handler(temp_dir)

            handler.handle(
                {
                    "callback_query": {
                        "id": "cb-1",
                        "data": "cancel",
                        "message": {"chat": {"id": "owner"}, "message_id": 10},
                    }
                }
            )

            self.assertEqual(api_client.answered[0][0], "cb-1")

    def test_video_message_downloads_and_enqueues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler, api_client, launcher, downloader, registry = self.make_handler(temp_dir)
            handler.handle(
                {
                    "message": {
                        "message_id": 101,
                        "chat": {"id": "owner"},
                        "video": {"file_id": "vid-1", "file_name": "movie.mp4", "duration": 30},
                    }
                }
            )

            self.assertEqual(downloader.calls[0][0], "vid-1")
            self.assertIn("/output/telegram/video/", str(downloader.calls[0][1]))
            self.assertIn("收到媒體：movie.mp4", api_client.sent_messages[0][1])
            self.assertIn("✅ 下載完成，已加入排隊", api_client.sent_messages[1][1])

            task = registry.get_next_pending_task()
            self.assertIsNotNone(task)
            self.assertTrue(str(task["payload"]).endswith("movie.mp4"))

    def test_video_note_downloads_and_enqueues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler, api_client, _, downloader, registry = self.make_handler(temp_dir)
            handler.handle(
                {
                    "message": {
                        "message_id": 102,
                        "chat": {"id": "owner"},
                        "video_note": {"file_id": "vn-1", "duration": 5},
                    }
                }
            )

            self.assertEqual(downloader.calls[0][0], "vn-1")
            self.assertTrue(any("vn_" in str(call[1]) for call in downloader.calls))
            self.assertIn("收到媒體", api_client.sent_messages[0][1])

            task = registry.get_next_pending_task()
            self.assertIsNotNone(task)
            self.assertEqual(task["task_type"], "file")

    def test_caption_url_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler, api_client, _, _, _ = self.make_handler(temp_dir)
            handler.handle(
                {
                    "message": {
                        "chat": {"id": "owner"},
                        "caption": "Check this: https://youtube.com/watch?v=xyz",
                    }
                }
            )

            self.assertIn("✅ 已收到網址並加入排隊", api_client.sent_messages[0][1])
            self.assertIn("https://youtube.com/watch?v=xyz", api_client.sent_messages[0][1])

    def test_transcription_status_provider_real_locks(self) -> None:
        import fcntl
        import os
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_file = Path(temp_dir) / "test.lock"
            dir_lock = Path(temp_dir) / "dir.lock"

            provider = TranscriptionStatusProvider(lock_paths=[lock_file, dir_lock])

            self.assertEqual(provider.describe(), "空閒中")

            with open(lock_file, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                self.assertEqual(provider.describe(), "忙碌中")

            lock_file.touch()
            self.assertEqual(provider.describe(), "空閒中")

            dir_lock.mkdir()
            (dir_lock / "pid").write_text(str(os.getpid()))
            self.assertEqual(provider.describe(), "忙碌中")

            (dir_lock / "pid").write_text("999999")
            self.assertEqual(provider.describe(), "空閒中")

    def test_poller_advances_offset_even_when_handler_raises(self) -> None:
        api_client = FakeApiClient()
        api_client.updates = {"ok": True, "result": [{"update_id": 3, "message": {}}, {"update_id": 4, "message": {}}]}

        class FlakyHandler:
            def __init__(self):
                self.calls = 0

            def handle(self, update):  # noqa: ANN001
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("boom")

        poller = TelegramPoller(api_client, FlakyHandler())
        poller.poll_once()

        self.assertEqual(poller.last_update_id, 4)


if __name__ == "__main__":
    unittest.main()
