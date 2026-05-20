from __future__ import annotations

import io
import json
import unittest
import hashlib
import tempfile
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

    @patch("os.environ", {
        "SMTP_HOST": "localhost",
        "SMTP_USER": "user",
        "SMTP_PASS": "pass",
        "SMTP_PORT": "587"
    })
    def test_mail_notifier_sends_separate_emails_with_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            class FakeItem:
                def __init__(self, label, url, emails, path):
                    self.label = label
                    self.title = label
                    self.source_url = url
                    self.emails = emails
                    self.mail_body = f"Body for {label}"
                    self.mail_attachment_path = path
                    self.failed = False

            item1_path = temp_path / "item1.srt.txt"
            item1_path.write_text("content", encoding="utf-8")
            item2_path = temp_path / "item2.srt.txt"
            item2_path.write_text("content", encoding="utf-8")

            items = [
                FakeItem("Item1", "http://url1", ["a@b.com"], item1_path),
                FakeItem("Item2", "http://url2", ["a@b.com"], item2_path),
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
                
                # Setup mock item properties
                items[0].duration_str = "00:10:00"
                items[0].processing_time_str = "00:02:00"
                items[0].summarization_time_str = "00:00:30"
                items[1].duration_str = "00:20:00"
                items[1].processing_time_str = "00:04:00"
                items[1].summarization_time_str = "00:01:00"

                notifier.notify(items, Args())
                
                # Should have called send_mail twice
                self.assertEqual(mock_send.call_count, 2)
                
                # Check first call
                args1 = mock_send.call_args_list[0]
                self.assertEqual(args1.args[0], "a@b.com")
                self.assertIn("Item1", args1.args[1])
                self.assertIn("Source URL: http://url1", args1.args[3])
                self.assertIn("Audio duration: 00:10:00", args1.args[3])
                self.assertIn("Transcription processing time: 00:02:00", args1.args[3])
                self.assertIn("Summarization processing time: 00:00:30", args1.args[3])
                
                # Check second call
                args2 = mock_send.call_args_list[1]
                self.assertEqual(args2.args[0], "a@b.com")
                # Should have touched marker twice
                self.assertEqual(mock_marker_obj.touch.call_count, 2)
                
                # Verify local archive exists
                digest1 = hashlib.sha1("a@b.com".encode("utf-8")).hexdigest()[:12]
                archive1 = temp_path / f"item1.srt.txt.{digest1}.mail.txt"
                
                # Debugging
                if not archive1.exists():
                    print(f"FAILED: {archive1} not found")
                    print(f"Directory contents: {list(temp_path.iterdir())}")
                
                self.assertTrue(archive1.exists())
                self.assertIn("Subject: Item1", archive1.read_text())


if __name__ == "__main__":
    unittest.main()

class SimpleMailTest(unittest.TestCase):
    @patch("os.environ", {"SMTP_HOST": "h", "SMTP_USER": "u", "SMTP_PASS": "p"})
    @patch("tools.notifier.send_mail")
    def test_archiving_logic(self, mock_send):
        with tempfile.TemporaryDirectory() as temp_dir:
            p = Path(temp_dir) / "test.txt"
            p.write_text("orig")
            
            from tools.notifier import MailNotifier
            from types import SimpleNamespace
            
            notifier = MailNotifier()
            item = SimpleNamespace(
                label="L", title="T", source_url="U", emails=["a@b.com"],
                mail_body="B", mail_attachment_path=p, failed=False
            )
            args = SimpleNamespace(run_date=MagicMock(), enable_mail=True)
            args.run_date.isoformat.return_value = "D"
            
            notifier.notify([item], args)
            
            digest = hashlib.sha1(b"a@b.com").hexdigest()[:12]
            archive = p.with_name(f"test.txt.{digest}.mail.txt")
            print(f"DEBUG: Checking {archive}")
            self.assertTrue(archive.exists())
