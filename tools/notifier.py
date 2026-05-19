import hashlib
import json
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

# Use the centralized logger
from logger import get_logger

logger = get_logger("notifier")

def strip_emojis(text: str) -> str:
    return "".join(c for c in text if ord(c) <= 0xFFFF)

def marker_path_for(transcript_path: Path, identifier: str) -> Path:
    digest = hashlib.sha1(identifier.encode("utf-8")).hexdigest()[:12]
    return transcript_path.with_name(f"{transcript_path.name}.{digest}.sent")

def chunk_telegram_message(message: str, max_length: int = 4000) -> list[str]:
    if max_length <= 0: raise ValueError("max_length must be positive")
    return [message[i:i + max_length] for i in range(0, len(message), max_length)] or [""]

class TelegramBotClient:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    def send(self, message: str, max_length: int = 4000) -> bool:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        chunks = chunk_telegram_message(message, max_length=max_length)
        
        # Audit log for AI: record the full message being sent
        logger.info(f"Outgoing Telegram notification chat_id={self.chat_id} chunks={len(chunks)} body=\"{message}\"", action="send_telegram")

        for index, chunk in enumerate(chunks):
            if index > 0: time.sleep(1)
            payload = {"chat_id": self.chat_id, "text": chunk, "disable_web_page_preview": True}
            try:
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as response:
                    if response.status != 200:
                        logger.error(f"Telegram chunk send failed status={response.status} index={index}", action="send_failed")
                        return False
            except Exception as e:
                logger.error(f"Telegram error index={index} error=\"{e}\"", action="send_error")
                return False
        return True

def send_mail(recipient: str, subject: str, attachment_paths: Path | Iterable[Path], body: str = "") -> None:
    attachments = [attachment_paths] if isinstance(attachment_paths, Path) else list(attachment_paths)
    if not attachments: raise ValueError("attachment_paths must not be empty")

    host, user, password = os.environ.get("SMTP_HOST"), os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
    port = int(os.environ.get("SMTP_PORT", 587))
    from_addr = os.environ.get("SMTP_FROM", user)

    if not all([host, user, password]):
        raise RuntimeError("SMTP settings incomplete")

    # Audit log for AI: record full email details
    logger.info(f"Outgoing Email to={recipient} subject=\"{subject}\" attachments={[p.name for p in attachments]} body=\"{body}\"", action="send_mail")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = recipient
    msg.set_content(body or f"Attached: {', '.join(p.name for p in attachments)}")

    for p in attachments:
        with open(p, "rb") as f:
            msg.add_attachment(f.read(), maintype="application", subtype="octet-stream", filename=p.name)

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)

def send_telegram_msg(message: str) -> bool:
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("Missing Telegram config", action="send_skipped")
        return False
    return TelegramBotClient(token, chat_id).send(message)

class BaseNotifier:
    def __init__(self, name: str): self.name = name
    def is_enabled(self, args) -> bool: raise NotImplementedError
    def notify(self, items: List[any], args) -> None: raise NotImplementedError
    def log_event(self, status: str, seconds: float, item: str = "", detail: str = ""):
        msg = f"EVENT {self.name} status={status} duration={seconds:.2f}s"
        if item: msg += f" item=\"{item}\""
        if detail: msg += f" detail=\"{detail}\""
        logger.info(msg, action="notify_event")

class MailNotifier(BaseNotifier):
    def __init__(self): super().__init__("mail")
    def is_enabled(self, args): return getattr(args, "enable_mail", False)
    def notify(self, items, args):
        subject_date = args.run_date.isoformat()
        for item in items:
            if not item.mail_attachment_path or item.failed: continue
            for email in item.emails:
                marker = marker_path_for(item.mail_attachment_path, email)
                if marker.exists(): continue
                
                title = item.title or item.label
                subject = f"{title} - {subject_date}"
                body = "\n\n".join(filter(None, [f"Source URL: {item.source_url}", getattr(item, 'duration_str', ''), item.mail_body or f"Attached: {item.mail_attachment_path.name}"]))
                
                t_start = time.monotonic()
                try:
                    send_mail(email, subject, item.mail_attachment_path, body)
                    marker.touch()
                    self.log_event("ok", time.monotonic()-t_start, item.label, email)
                except Exception as e:
                    self.log_event("failed", 0, item.label, f"{email}: {e}")

class TelegramNotifier(BaseNotifier):
    def __init__(self): super().__init__("telegram")
    def is_enabled(self, args): return getattr(args, "enable_telegram", False)
    def notify(self, items, args):
        t_start = time.monotonic()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "default")
        processed = [it for it in items if it.download_ready or it.failed]
        if not processed: return

        try:
            for item in items:
                if not (item.mail_body and item.mail_attachment_path): continue
                marker = marker_path_for(item.mail_attachment_path, chat_id)
                if marker.exists():
                    logger.info(f"Telegram already sent item=\"{item.label}\"", action="notify_skip")
                    continue
                
                time.sleep(1)
                msg = f"[Summary] {args.run_date}\n[Detail] # {item.title or item.label}\n\n{item.mail_body}"
                if send_telegram_msg(msg): marker.touch()
            self.log_event("ok", time.monotonic()-t_start)
        except Exception as e:
            self.log_event("failed", 0, detail=str(e))

def get_notifiers() -> List[BaseNotifier]:
    notifiers = []
    for cls in BaseNotifier.__subclasses__():
        try: notifiers.append(cls())
        except Exception as e: logger.error(f"Failed to load notifier {cls.__name__}: {e}", action="setup_error")
    return notifiers
