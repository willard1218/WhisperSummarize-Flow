#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from download_latest_podcast import fetch_bytes, sanitize_filename
from recipient_groups import resolve_emails, load_recipient_groups


def parse_run_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date '{value}'. Expected YYYY-MM-DD."
        ) from exc


def load_subscriptions(path: Path) -> list[dict]:
    if not path.exists():
        return []

    data = json.loads(path.read_text(encoding="utf-8"))
    subscriptions = data.get("subscriptions", [])
    if not isinstance(subscriptions, list):
        raise SystemExit(f"Invalid subscriptions list in {path}")
    return subscriptions


def make_podcast_slug(rss_url: str) -> str:
    tail = rss_url.rstrip("/").rsplit("/", 1)[-1]
    base = tail.rsplit(".", 1)[0]
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", base).strip("-").lower()
    return cleaned or "podcast"


def sanitize_slug_part(value: str) -> str:
    cleaned = sanitize_filename(value).replace(" ", "-").strip("-")
    return cleaned


def resolve_podcast_title(rss_url: str) -> str:
    rss_data = fetch_bytes(rss_url)
    root = ET.fromstring(rss_data)
    channel = root.find("channel")
    if channel is None:
        return ""
    return (channel.findtext("title") or "").strip()


def make_podcast_output_dir_name(rss_url: str, podcast_title: str) -> str:
    slug = make_podcast_slug(rss_url)
    title_slug = sanitize_slug_part(podcast_title)
    if title_slug:
        return f"{title_slug}__{slug}"
    return slug


def parse_audio_path(output: str) -> Path | None:
    for line in output.splitlines():
        if line.startswith("Saved: "):
            return Path(line.split(": ", 1)[1].strip())
        if line.startswith("File already exists: "):
            return Path(line.split(": ", 1)[1].strip())
    return None


def transcript_path_for(audio_path: Path) -> Path:
    return audio_path.with_suffix("").with_name(audio_path.with_suffix("").name + ".srt.txt")


def marker_path_for(transcript_path: Path, email: str) -> Path:
    digest = hashlib.sha1(email.encode("utf-8")).hexdigest()[:12]
    return transcript_path.with_name(f"{transcript_path.name}.{digest}.mail-sent")


def send_mail(recipient: str, subject: str, attachment_path: Path) -> None:
    script = f'''
set recipientAddress to "{recipient}"
set subjectText to "{subject}"
set attachmentPath to POSIX file "{attachment_path}"

tell application "Mail"
  activate
  set newMessage to make new outgoing message with properties {{subject:subjectText, content:"", visible:false}}
  tell newMessage
    make new to recipient at end of to recipients with properties {{address:recipientAddress}}
    make new attachment with properties {{file name:attachmentPath}} at after the last paragraph
    send
  end tell
end tell
'''
    subprocess.run(["osascript", "-e", script], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Process all registered podcast subscriptions for a given date."
    )
    parser.add_argument(
        "--config",
        default="subscriptions.json",
        help="Path to the subscriptions JSON file (default: subscriptions.json)",
    )
    parser.add_argument(
        "--date",
        dest="run_date",
        type=parse_run_date,
        default=date.today(),
        help="Episode date to download, in YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--output-root",
        default="output",
        help="Directory for downloaded audio and transcripts (default: output)",
    )
    parser.add_argument(
        "--transcribe-script",
        default=os.environ.get("GENSRT_SCRIPT", "gensrt.sh"),
        help="Transcription script to run after download",
    )
    parser.add_argument(
        "--recipient-config",
        default=os.environ.get("RECIPIENT_CONFIG_FILE", "recipient_groups.local.json"),
        help="Path to local recipient groups JSON (default: recipient_groups.local.json)",
    )
    parser.add_argument(
        "--traditionalize-transcript",
        action="store_true",
        default=os.environ.get("OPENCC_TRADITIONALIZE", "0") == "1",
        help="Convert Simplified Chinese transcripts to Traditional Chinese through OpenCC.",
    )
    parser.add_argument(
        "--opencc-config",
        default=os.environ.get("OPENCC_CONFIG", "s2twp.json"),
        help="OpenCC config name or path (default: s2twp.json)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Debug mode: only send emails to the address specified in DEBUG_RECIPIENT env var",
    )
    args = parser.parse_args()

    debug_email = os.environ.get("DEBUG_RECIPIENT")
    if args.debug and not debug_email:
        print("DEBUG_RECIPIENT environment variable is not set.", file=sys.stderr)
        return 1

    base_dir = Path(__file__).resolve().parent
    config_path = Path(args.config).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    recipient_config = Path(args.recipient_config).expanduser().resolve()
    downloader = base_dir / "download_latest_podcast.py"
    converter_script = base_dir / "convert_transcript_opencc.py"
    subscriptions = load_subscriptions(config_path)
    recipient_groups = load_recipient_groups(recipient_config)

    if args.traditionalize_transcript and not converter_script.exists():
        print(f"OpenCC converter script not found: {converter_script}", file=sys.stderr)
        return 1

    if not subscriptions:
        print(f"No subscriptions found in {config_path}")
        return 0

    output_root.mkdir(parents=True, exist_ok=True)

    overall_success = True
    for subscription in subscriptions:
        rss_url = (subscription.get("rss_url") or "").strip()
        podcast_url = (subscription.get("podcast_url") or rss_url).strip()
        podcast_title = (subscription.get("podcast_title") or "").strip()
        emails = resolve_emails(subscription, recipient_groups)
        if args.debug and debug_email:
            emails = [debug_email]

        if not rss_url or not emails:
            print(f"Skipping invalid subscription: {subscription}")
            overall_success = False
            continue

        if not podcast_title:
            try:
                podcast_title = resolve_podcast_title(rss_url)
            except Exception:
                podcast_title = ""

        podcast_output_dir = output_root / make_podcast_output_dir_name(rss_url, podcast_title)
        podcast_output_dir.mkdir(parents=True, exist_ok=True)

        print(f"== Processing {podcast_url}")
        print(f"Resolved RSS: {rss_url}")
        print(f"Target date: {args.run_date.isoformat()}")

        command = [
            sys.executable,
            str(downloader),
            rss_url,
            "-o",
            str(podcast_output_dir),
            "--episode-date",
            args.run_date.isoformat(),
            "--transcribe-script",
            args.transcribe_script,
        ]
        if podcast_title:
            command.extend(["--show-title", podcast_title])

        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
        )

        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.returncode != 0:
            if result.stderr:
                print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
            overall_success = False
            continue

        audio_path = parse_audio_path(result.stdout)
        if audio_path is None:
            print("Could not determine downloaded file path from downloader output.", file=sys.stderr)
            overall_success = False
            continue

        transcript_path = transcript_path_for(audio_path)
        if not transcript_path.exists():
            print(f"Transcript not found: {transcript_path}", file=sys.stderr)
            overall_success = False
            continue

        mail_attachment_path = transcript_path
        if args.traditionalize_transcript:
            if transcript_path.name.endswith(".srt.txt"):
                output_path = transcript_path.with_name(
                    transcript_path.name[:-8] + ".zh-Hant.srt.txt"
                )
                print(f"Converting to Traditional Chinese: {output_path.name}")
                conv_result = subprocess.run(
                    [
                        sys.executable,
                        str(converter_script),
                        str(transcript_path),
                        "--output-path",
                        str(output_path),
                        "--config",
                        args.opencc_config,
                    ],
                    text=True,
                    capture_output=True,
                )
                if conv_result.returncode == 0 and output_path.exists():
                    mail_attachment_path = output_path
                else:
                    print("Traditional Chinese conversion failed; using original transcript.", file=sys.stderr)
                    if conv_result.stderr:
                        print(conv_result.stderr, file=sys.stderr)
            else:
                print(f"Skipping Traditional Chinese conversion for non-.srt transcript: {transcript_path.name}")

        subject = f"Podcast transcript {audio_path.stem}"
        for email in emails:
            marker_path = marker_path_for(mail_attachment_path, email)
            if marker_path.exists():
                print(f"Already sent to {email}: {mail_attachment_path.name}")
                continue

            try:
                send_mail(email, subject, mail_attachment_path)
            except subprocess.CalledProcessError as exc:
                print(
                    f"Failed to send to {email}: {mail_attachment_path.name} "
                    f"(exit {exc.returncode})",
                    file=sys.stderr,
                )
                overall_success = False
                continue

            marker_path.touch()
            print(f"Sent to {email}: {mail_attachment_path.name}")

    return 0 if overall_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
