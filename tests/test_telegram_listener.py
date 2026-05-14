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
        handler = TelegramUpdateHandler(
            settings=settings,
            api_client=api_client,
            status_provider=FakeStatusProvider(status),
            pipeline_launcher=launcher,
            file_downloader=downloader,
        )
        return handler, api_client, launcher, downloader

    def test_status_command_reports_current_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler, api_client, _, _ = self.make_handler(temp_dir, status="忙碌中")
            handler.handle({"message": {"chat": {"id": "owner"}, "text": "/status"}})

            self.assertEqual(api_client.sent_messages[0][1], "目前系統狀態：\n忙碌中")

    def test_url_message_sends_confirmation_button(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler, api_client, _, _ = self.make_handler(temp_dir, status="忙碌中")
            handler.handle(
                {"message": {"chat": {"id": "owner"}, "text": "https://youtube.com/watch?v=abc"}}
            )

            chat_id, text, reply_markup = api_client.sent_messages[0]
            self.assertEqual(chat_id, "owner")
            self.assertIn("是否啟動流程？", text)
            self.assertIn("等待目前任務完成後自動開始", text)
            self.assertEqual(reply_markup["inline_keyboard"][0][0]["callback_data"], "exec|https://youtube.com/watch?v=abc")

    def test_media_message_downloads_and_launches_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler, api_client, launcher, downloader = self.make_handler(temp_dir)
            handler.handle(
                {
                    "message": {
                        "chat": {"id": "owner"},
                        "audio": {"file_id": "file-1", "file_name": "clip.mp3", "duration": 12},
                    }
                }
            )

            self.assertEqual(downloader.calls[0][0], "file-1")
            self.assertEqual(launcher.calls[0]["chat_id"], "owner")
            self.assertIn("/output/telegram/audio/", str(launcher.calls[0]["local_file"]))
            self.assertTrue(str(launcher.calls[0]["local_file"]).endswith("clip.mp3"))
            self.assertIn("收到媒體檔案", api_client.sent_messages[0][1])
            metadata_path = Path(temp_dir) / "output" / "telegram"
            self.assertTrue(any(path.name == "metadata.json" for path in metadata_path.rglob("metadata.json")))

    def test_unauthorized_chat_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler, api_client, launcher, _ = self.make_handler(temp_dir)
            handler.handle({"message": {"chat": {"id": "stranger"}, "text": "/status"}})

            self.assertEqual(api_client.sent_messages, [])
            self.assertEqual(launcher.calls, [])

    def test_callback_exec_starts_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler, api_client, launcher, _ = self.make_handler(temp_dir)
            handler.handle(
                {
                    "callback_query": {
                        "id": "cb-1",
                        "data": "exec|https://youtube.com/watch?v=abc",
                        "message": {"chat": {"id": "owner"}, "message_id": 10},
                    }
                }
            )

            self.assertEqual(api_client.answered[0], ("cb-1", "任務已啟動"))
            self.assertEqual(launcher.calls[0]["url"], "https://youtube.com/watch?v=abc")

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

        poller = TelegramPoller(api_client, FlakyHandler(), sleep_seconds=0)
        poller.poll_once()

        self.assertEqual(poller.last_update_id, 4)


if __name__ == "__main__":
    unittest.main()
