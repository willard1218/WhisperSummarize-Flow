import hashlib
import os
import smtplib
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

def marker_path_for(transcript_path: Path, email: str) -> Path:
    """Returns the path to the marker file that indicates an email has been sent."""
    digest = hashlib.sha1(email.encode("utf-8")).hexdigest()[:12]
    return transcript_path.with_name(f"{transcript_path.name}.{digest}.mail-sent")

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
