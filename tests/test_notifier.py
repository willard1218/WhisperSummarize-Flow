from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from tools.notifier import TelegramBotClient, chunk_telegram_message


class FakeResponse:
    def __init__(self, status: int = 200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return b"{}"


class NotifierTests(unittest.TestCase):
    def test_chunk_telegram_message_splits_text(self) -> None:
        chunks = chunk_telegram_message("abcdef", max_length=2)
        self.assertEqual(chunks, ["ab", "cd", "ef"])

    def test_chunk_telegram_message_rejects_invalid_max_length(self) -> None:
        with self.assertRaises(ValueError):
            chunk_telegram_message("abc", max_length=0)

    def test_telegram_bot_client_sends_all_chunks(self) -> None:
        captured_payloads: list[dict] = []

        def fake_urlopen(request, timeout=30):  # noqa: ANN001
            captured_payloads.append(json.loads(request.data.decode("utf-8")))
            return FakeResponse()

        client = TelegramBotClient("token", "chat-id")
        with patch("tools.notifier.urllib.request.urlopen", side_effect=fake_urlopen), patch("tools.notifier.time.sleep"):
            ok = client.send("abcdef", max_length=3)

        self.assertTrue(ok)
        self.assertEqual([payload["text"] for payload in captured_payloads], ["abc", "def"])


if __name__ == "__main__":
    unittest.main()
