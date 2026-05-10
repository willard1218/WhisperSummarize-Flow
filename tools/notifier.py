import hashlib
import os
import smtplib
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable, List
from collections import defaultdict

def strip_emojis(text: str) -> str:
    """Removes emojis and other non-BMP characters from text."""
    return "".join(c for c in text if ord(c) <= 0xFFFF)

def marker_path_for(transcript_path: Path, email: str) -> Path:
    """Returns the path to the marker file that indicates an email has been sent."""
    digest = hashlib.sha1(email.encode("utf-8")).hexdigest()[:12]
    return transcript_path.with_name(f"{transcript_path.name}.{digest}.mail-sent")

# Original functions maintained for backward compatibility (e.g., run_registered_podcasts.py)
def send_mail(recipient: str, subject: str, attachment_paths: Path | Iterable[Path], body: str = "") -> None:
    """Sends an email with optional body and attachments, using SMTP or Apple Mail fallback."""
    if isinstance(attachment_paths, Path):
        attachments = [attachment_paths]
    else:
        attachments = list(attachment_paths)
    if not attachments:
        raise ValueError("attachment_paths must not be empty")

    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", 587))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    from_addr = os.environ.get("SMTP_FROM", user)

    if not all([host, user, password]):
        if sys.platform != "darwin":
            raise RuntimeError("SMTP settings are incomplete. Apple Mail fallback is only available on macOS. Please configure SMTP in local_config.sh.")

        attachment_lines = "\n".join(
            f'make new attachment with properties {{file name:POSIX file "{path}"}} at after the last paragraph'
            for path in attachments
        )
        safe_body = body.replace('"', '\\"').replace('\n', '\\n') if body else ""
        script = f'''
set recipientAddress to "{recipient}"
set subjectText to "{subject}"
set bodyText to "{safe_body}"
tell application "Mail"
  activate
  set newMessage to make new outgoing message with properties {{subject:subjectText, content:bodyText, visible:false}}
  tell newMessage
    make new to recipient at end of to recipients with properties {{address:recipientAddress}}
    {attachment_lines}
    send
  end tell
end tell
'''
        subprocess.run(["osascript", "-e", script], check=True)
        return

    # Use SMTP
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = recipient
    
    if body:
        msg.set_content(body)
    else:
        body_lines = ["Attached files:"]
        body_lines.extend(f"- {path.name}" for path in attachments)
        msg.set_content("\n".join(body_lines))

    for attachment_path in attachments:
        with open(attachment_path, "rb") as f:
            file_data = f.read()
            msg.add_attachment(
                file_data,
                maintype="application",
                subtype="octet-stream",
                filename=attachment_path.name
            )

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)

def send_telegram_msg(message: str) -> None:
    """Sends a message via Telegram Bot API, splitting long messages if necessary."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("Telegram notification skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set.")
        return

    message = strip_emojis(message)
    MAX_LENGTH = 4000
    chunks = [message[i:i + MAX_LENGTH] for i in range(0, len(message), MAX_LENGTH)]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    for i, chunk in enumerate(chunks):
        if i > 0: time.sleep(1)
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": chunk}).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req) as response:
                if response.status != 200:
                    print(f"Failed to send Telegram message. Status: {response.status}")
        except Exception as e:
            print(f"Error sending Telegram message: {e}")

# --- OCP Refactored Notifiers ---

class BaseNotifier:
    """Base class for all notification services."""
    def __init__(self, name: str):
        self.name = name

    def is_enabled(self, args) -> bool:
        raise NotImplementedError

    def notify(self, items: List[any], args) -> None:
        raise NotImplementedError

    def log_event(self, status: str, seconds: float, item: str = "", detail: str = ""):
        parts = ["EVENT", self.name, f"status={status}", f"seconds={seconds:.2f}"]
        if item: parts.append(f'item="{item}"')
        if detail: parts.append(f'detail="{detail}"')
        print(" ".join(parts))

class MailNotifier(BaseNotifier):
    def __init__(self):
        super().__init__("mail")

    def is_enabled(self, args) -> bool:
        return getattr(args, "enable_mail", False)

    def notify(self, items: List[any], args) -> None:
        pending_mail = defaultdict(list)
        for item in items:
            if not item.mail_attachment_path or item.failed:
                continue
            for email in item.emails:
                marker = marker_path_for(item.mail_attachment_path, email)
                if not marker.exists():
                    pending_mail[email].append((item, item.mail_attachment_path))

        subject_date = args.run_date.isoformat()
        for email, entries in pending_mail.items():
            attachments = [attachment for _, attachment in entries]
            subject = f"Daily transcripts {subject_date} ({len(attachments)} items)"
            combined_body = "\n\n=================================\n\n".join(
                f"# {item.title or item.label}\n\n{item.mail_body}" for item, _ in entries if item.mail_body
            )

            t_start = time.monotonic()
            try:
                send_mail(email, subject, attachments, combined_body)
                for item, attachment in entries:
                    marker_path_for(attachment, email).touch()
                    self.log_event("ok", time.monotonic()-t_start, item.label, email)
            except Exception as e:
                for item, _ in entries:
                    item.failed = True
                self.log_event("failed", time.monotonic()-t_start, "batch", f"{email}: {str(e)}")

class TelegramNotifier(BaseNotifier):
    def __init__(self):
        super().__init__("telegram")

    def is_enabled(self, args) -> bool:
        return getattr(args, "enable_telegram", False)

    def notify(self, items: List[any], args) -> None:
        t_start = time.monotonic()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        
        # Only notify if there's something meaningful to report (at least one non-skipped item)
        processed_items = [item for item in items if item.download_ready or item.failed]
        if not processed_items:
            print("Telegram notification skipped: No non-skipped items to report.")
            return

        try:
            # 1. Summary Report
            summary_lines = [f"[Summary] Daily Pipeline Summary ({args.run_date}):"]
            for item in items:
                status = "[OK]" if (item.download_ready and not item.failed) else "[FAILED]" if item.failed else "[SKIPPED]"
                summary_lines.append(f"{status} {item.title or item.label}")
            send_telegram_msg("\n".join(summary_lines))

            # 2. Detailed summaries
            for item in items:
                if item.mail_body and item.mail_attachment_path:
                    marker = marker_path_for(item.mail_attachment_path, chat_id or "telegram")
                    if not marker.exists():
                        time.sleep(1)
                        msg = f"[Detail] # {item.title or item.label}\n\n{item.mail_body}"
                        send_telegram_msg(msg)
                        marker.touch()
            
            self.log_event("ok", time.monotonic()-t_start)
        except Exception as e:
            self.log_event("failed", time.monotonic()-t_start, detail=str(e))

# Registry of available notifiers
def get_notifiers() -> List[BaseNotifier]:
    return [
        MailNotifier(),
        TelegramNotifier()
        # To add LINE: return [MailNotifier(), TelegramNotifier(), LineNotifier()]
    ]
