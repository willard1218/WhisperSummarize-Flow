from __future__ import annotations

import io
import json
import unittest
import hashlib
from pathlib import Path
from unittest.mock import patch, MagicMock

from tools.notifier import TelegramBotClient, chunk_telegram_message, MailNotifier


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

    def test_mail_notifier_sends_separate_emails_with_url(self) -> None:
        class FakeItem:
            def __init__(self, label, url, emails):
                self.label = label
                self.title = label
                self.source_url = url
                self.emails = emails
                self.mail_body = f"Body for {label}"
                self.mail_attachment_path = Path(f"/tmp/{label}.txt")
                self.failed = False

        items = [
            FakeItem("Item1", "http://url1", ["a@b.com"]),
            FakeItem("Item2", "http://url2", ["a@b.com"]),
        ]

        class Args:
            run_date = MagicMock()
            run_date.isoformat.return_value = "2026-05-16"
            enable_mail = True

        notifier = MailNotifier()
        
        with patch("tools.notifier.send_mail") as mock_send, \
             patch("tools.notifier.marker_path_for") as mock_marker:
            
            # Setup mock marker
            mock_marker_obj = MagicMock()
            mock_marker_obj.exists.return_value = False
            mock_marker.return_value = mock_marker_obj
            
            notifier.notify(items, Args())
            
            # Should have called send_mail twice
            self.assertEqual(mock_send.call_count, 2)
            
            # Check first call
            args1 = mock_send.call_args_list[0]
            self.assertEqual(args1.args[0], "a@b.com")
            self.assertIn("Item1", args1.args[1])
            self.assertIn("Source URL: http://url1", args1.args[3])
            
            # Check second call
            args2 = mock_send.call_args_list[1]
            self.assertEqual(args2.args[0], "a@b.com")
            self.assertIn("Item2", args2.args[1])
            self.assertIn("Source URL: http://url2", args2.args[3])
            
            # Should have touched marker twice
            self.assertEqual(mock_marker_obj.touch.call_count, 2)


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
