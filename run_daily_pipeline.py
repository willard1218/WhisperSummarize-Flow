#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from download_latest_podcast import fetch_bytes, sanitize_filename
from recipient_groups import load_recipient_groups, resolve_emails
from run_registered_podcasts import (
    make_podcast_output_dir_name,
    parse_audio_path,
    parse_run_date,
    resolve_podcast_title,
    transcript_path_for,
)
from run_registered_youtube import make_channel_slug

NO_EPISODE_EXIT_CODE = 2


@dataclass
class DailyItem:
    label: str
    kind: str
    source_url: str
    emails: list[str]
    output_dir: Path
    audio_path: Path | None = None
    transcript_path: Path | None = None
    mail_attachment_path: Path | None = None
    title: str = ""
    failed: bool = False
    messages: list[str] = field(default_factory=list)


def load_subscriptions(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    data = json.loads(path.read_text(encoding="utf-8"))
    subscriptions = data.get("subscriptions", [])
    if not isinstance(subscriptions, list):
        raise SystemExit(f"Invalid subscriptions list in {path}")
    return [item for item in subscriptions if isinstance(item, dict)]


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


def build_items(
    podcast_config: Path,
    youtube_config: Path,
    recipient_config: Path,
    output_root: Path,
) -> list[DailyItem]:
    groups = load_recipient_groups(recipient_config)
    items: list[DailyItem] = []

    for index, subscription in enumerate(load_subscriptions(podcast_config), start=1):
        rss_url = (subscription.get("rss_url") or "").strip()
        source_url = (subscription.get("podcast_url") or rss_url).strip()
        emails = resolve_emails(subscription, groups)
        title = (subscription.get("podcast_title") or "").strip()

        if not title and rss_url:
            try:
                title = resolve_podcast_title(rss_url)
            except Exception:
                title = ""

        output_dir = output_root / make_podcast_output_dir_name(rss_url, title)
        items.append(
            DailyItem(
                label=f"Podcast {index}",
                kind="podcast",
                source_url=source_url,
                emails=emails,
                output_dir=output_dir,
                title=title,
            )
        )

    for index, subscription in enumerate(load_subscriptions(youtube_config), start=1):
        channel_url = (subscription.get("channel_url") or "").strip()
        emails = resolve_emails(subscription, groups)
        output_dir = output_root / make_channel_slug(channel_url)
        items.append(
            DailyItem(
                label=f"YouTube {index}" if index > 1 else "YouTube",
                kind="youtube",
                source_url=channel_url,
                emails=emails,
                output_dir=output_dir,
            )
        )

    return items


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True)


def log_event(event: str, status: str, seconds: float, item: str = "", detail: str = "") -> None:
    parts = [
        "EVENT",
        event,
        f"status={status}",
        f"seconds={seconds:.2f}",
    ]
    if item:
        parts.append(f'item="{item}"')
    if detail:
        parts.append(f'detail="{detail}"')
    print(" ".join(parts))


def print_completed_process(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)


def download_podcast(item: DailyItem, run_date: date, downloader: Path) -> bool:
    item.output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(downloader),
        item.source_url,
        "-o",
        str(item.output_dir),
        "--episode-date",
        run_date.isoformat(),
    ]
    if item.title:
        command.extend(["--show-title", item.title])

    started = time.monotonic()
    result = run_command(command)
    elapsed = time.monotonic() - started
    print_completed_process(result)
    if result.returncode == NO_EPISODE_EXIT_CODE:
        item.messages.append(f"no episode found for {item.label}")
        log_event("download_podcast", "skipped", elapsed, item.label, item.source_url)
        return True
    if result.returncode != 0:
        item.failed = True
        item.messages.append(f"download failed for {item.label}")
        log_event("download_podcast", "failed", elapsed, item.label, item.source_url)
        return False

    item.audio_path = parse_audio_path(result.stdout)
    if item.audio_path is None:
        item.failed = True
        item.messages.append(f"could not parse audio path for {item.label}")
        log_event("download_podcast", "failed", elapsed, item.label, "audio path missing")
        return False

    log_event("download_podcast", "ok", elapsed, item.label, item.audio_path.name)
    return True


def read_youtube_latest(channel_url: str) -> dict[str, Any] | None:
    started = time.monotonic()
    result = run_command(
        [
            "yt-dlp",
            "--flat-playlist",
            "--playlist-end",
            "1",
            "--match-filter",
            "live_status=was_live",
            "--print-json",
            channel_url,
        ]
    )
    elapsed = time.monotonic() - started
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    if result.returncode != 0 or not result.stdout.strip():
        log_event("resolve_youtube_latest", "failed", elapsed, "YouTube", channel_url)
        return None
    log_event("resolve_youtube_latest", "ok", elapsed, "YouTube", channel_url)
    return json.loads(result.stdout.splitlines()[-1])


def find_youtube_audio(output_dir: Path, video_id: str) -> Path | None:
    matches = sorted(output_dir.glob(f"*__{video_id}.mp3"))
    return matches[0] if matches else None


def download_youtube(item: DailyItem, archive_file: Path) -> bool:
    item.output_dir.mkdir(parents=True, exist_ok=True)
    latest = read_youtube_latest(item.source_url)
    if latest is None:
        item.failed = True
        item.messages.append(f"no completed livestream found for {item.label}")
        print(f"No completed livestream found at {item.source_url}", file=sys.stderr)
        return False

    video_id = str(latest.get("id") or "").strip()
    video_url = str(latest.get("webpage_url") or latest.get("url") or "").strip()
    item.title = str(latest.get("title") or "").strip()
    if not video_id or not video_url:
        item.failed = True
        item.messages.append(f"could not resolve latest livestream for {item.label}")
        print(f"Could not resolve the latest livestream video for {item.source_url}", file=sys.stderr)
        return False

    existing = find_youtube_audio(item.output_dir, video_id)
    if existing is not None:
        item.audio_path = existing
        print(f"File already exists: {existing}")
        log_event("download_youtube", "ok", 0.0, item.label, existing.name)
        return True

    started = time.monotonic()
    result = run_command(
        [
            "yt-dlp",
            "--download-archive",
            str(archive_file),
            "--no-overwrites",
            "-f",
            "bestaudio/best",
            "-x",
            "--audio-format",
            "mp3",
            "--paths",
            str(item.output_dir),
            "-o",
            "%(title).200B__%(id)s.%(ext)s",
            video_url,
        ]
    )
    elapsed = time.monotonic() - started
    print_completed_process(result)
    if result.returncode != 0:
        item.failed = True
        item.messages.append(f"download failed for {item.label}")
        log_event("download_youtube", "failed", elapsed, item.label, video_url)
        return False

    item.audio_path = find_youtube_audio(item.output_dir, video_id)
    if item.audio_path is None:
        item.failed = True
        item.messages.append(f"downloaded mp3 not found for {item.label}")
        print(f"Downloaded mp3 not found for video {video_id}", file=sys.stderr)
        log_event("download_youtube", "failed", elapsed, item.label, "audio path missing")
        return False
    log_event("download_youtube", "ok", elapsed, item.label, item.audio_path.name)
    return True


def transcript_candidates(audio_path: Path) -> list[Path]:
    return [
        audio_path.with_suffix("").with_name(f"{audio_path.with_suffix('').name}.srt.txt"),
        audio_path.with_suffix("").with_name(f"{audio_path.with_suffix('').name}.txt"),
    ]


def resolve_transcript_path(audio_path: Path) -> Path | None:
    for path in transcript_candidates(audio_path):
        if path.exists():
            return path
    return None


def translated_transcript_path(path: Path) -> Path:
    if path.name.endswith(".srt.txt"):
        return path.with_name(path.name[:-8] + ".zh-Hant.srt.txt")
    raise ValueError("Only .srt.txt transcripts are supported for Traditional Chinese conversion.")


def traditionalize_transcript(
    transcript_path: Path,
    converter_script: Path,
    config: str,
    item_label: str,
) -> Path | None:
    if not transcript_path.name.endswith(".srt.txt"):
        print(
            f"Skipping Traditional Chinese conversion for non-.srt transcript: {transcript_path}",
            file=sys.stderr,
        )
        log_event("traditionalize", "skipped", 0.0, item_label, transcript_path.name)
        return None

    output_path = translated_transcript_path(transcript_path)
    if output_path.exists() and output_path.stat().st_mtime >= transcript_path.stat().st_mtime:
        print(f"Traditional Chinese transcript already exists: {output_path}")
        log_event("traditionalize", "ok", 0.0, item_label, output_path.name)
        return output_path

    started = time.monotonic()
    result = run_command(
        [
            sys.executable,
            str(converter_script),
            str(transcript_path),
            "--output-path",
            str(output_path),
            "--config",
            config,
        ]
    )
    elapsed = time.monotonic() - started
    print_completed_process(result)
    if result.returncode != 0 or not output_path.exists():
        log_event("traditionalize", "failed", elapsed, item_label, transcript_path.name)
        return None
    log_event("traditionalize", "ok", elapsed, item_label, output_path.name)
    return output_path


def transcribe_item(
    item: DailyItem,
    transcribe_script: Path,
    traditionalize_enabled: bool,
    converter_script: Path,
    opencc_config: str,
) -> bool:
    if item.audio_path is None:
        return False

    existing = resolve_transcript_path(item.audio_path)
    if existing is not None:
        item.transcript_path = existing
        print(f"Transcript already exists: {existing}")
        log_event("transcribe", "ok", 0.0, item.label, existing.name)
    else:
        started = time.monotonic()
        result = subprocess.run([str(transcribe_script), str(item.audio_path)])
        elapsed = time.monotonic() - started
        if result.returncode != 0:
            item.failed = True
            item.messages.append(f"transcription failed for {item.label}")
            log_event("transcribe", "failed", elapsed, item.label, item.audio_path.name)
            return False

        item.transcript_path = resolve_transcript_path(item.audio_path)
        if item.transcript_path is None:
            item.failed = True
            item.messages.append(f"transcript not found for {item.label}")
            print(f"Transcript not found for {item.audio_path}", file=sys.stderr)
            log_event("transcribe", "failed", elapsed, item.label, "transcript missing")
            return False
        log_event("transcribe", "ok", elapsed, item.label, item.transcript_path.name)

    item.mail_attachment_path = item.transcript_path
    if traditionalize_enabled:
        converted = traditionalize_transcript(
            item.transcript_path,
            converter_script,
            opencc_config,
            item.label,
        )
        if converted is not None:
            item.mail_attachment_path = converted
        else:
            item.messages.append(f"traditionalize failed for {item.label}")
            print(
                f"Traditional Chinese conversion failed for {item.label}; using original transcript.",
                file=sys.stderr,
            )

    return True


def mail_item(item: DailyItem) -> bool:
    if item.mail_attachment_path is None or item.audio_path is None:
        return False

    success = True
    subject_prefix = "YouTube transcript" if item.kind == "youtube" else "Podcast transcript"
    subject = f"{subject_prefix} {item.title or item.audio_path.stem}"
    for email in item.emails:
        marker_path = marker_path_for(item.mail_attachment_path, email)
        if marker_path.exists():
            print(f"Already sent to {email}: {item.mail_attachment_path.name}")
            log_event("mail", "ok", 0.0, item.label, f"{email} already sent")
            continue

        started = time.monotonic()
        try:
            send_mail(email, subject, item.mail_attachment_path)
        except subprocess.CalledProcessError as exc:
            elapsed = time.monotonic() - started
            success = False
            item.failed = True
            item.messages.append(f"mail failed for {item.label} -> {email}")
            print(
                f"Failed to send to {email}: {item.mail_attachment_path.name} "
                f"(exit {exc.returncode})",
                file=sys.stderr,
            )
            log_event("mail", "failed", elapsed, item.label, email)
            continue

        elapsed = time.monotonic() - started
        marker_path.touch()
        print(f"Sent to {email}: {item.mail_attachment_path.name}")
        log_event("mail", "ok", elapsed, item.label, email)

    return success


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the daily pipeline in phases: download all, transcribe all, mail all."
    )
    parser.add_argument("--date", dest="run_date", type=parse_run_date, default=date.today())
    parser.add_argument("--output-root", default="output")
    parser.add_argument(
        "--transcribe-script",
        default=os.environ.get("GENSRT_SCRIPT", "gensrt.sh"),
    )
    parser.add_argument("--podcast-config", default="subscriptions.json")
    parser.add_argument("--youtube-config", default="youtube_subscriptions.json")
    parser.add_argument(
        "--recipient-config",
        default=os.environ.get("RECIPIENT_CONFIG_FILE", "recipient_groups.local.json"),
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
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Debug mode: only send emails to the address specified in DEBUG_RECIPIENT env var",
    )
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    output_root = Path(args.output_root).expanduser().resolve()
    transcribe_script = Path(args.transcribe_script).expanduser().resolve()
    podcast_config = Path(args.podcast_config).expanduser().resolve()
    youtube_config = Path(args.youtube_config).expanduser().resolve()
    recipient_config = Path(args.recipient_config).expanduser().resolve()
    downloader = base_dir / "download_latest_podcast.py"
    archive_file = base_dir / "id.txt"
    converter_script = base_dir / "convert_transcript_opencc.py"

    if not transcribe_script.exists():
        raise SystemExit(f"Transcribe script not found: {transcribe_script}")
    if args.traditionalize_transcript and not converter_script.exists():
        raise SystemExit(f"OpenCC converter script not found: {converter_script}")

    items = build_items(podcast_config, youtube_config, recipient_config, output_root)
    if args.debug:
        debug_email = os.environ.get("DEBUG_RECIPIENT")
        if not debug_email:
            raise SystemExit("DEBUG_RECIPIENT environment variable is not set.")
        print(f"DEBUG MODE: Overriding all recipients to {debug_email}")
        for item in items:
            item.emails = [debug_email]

    if not items:
        print("No subscriptions found.")
        return 0

    overall_success = True

    phase_started = time.monotonic()
    print("Download phase: start")
    for item in items:
        if not item.source_url or not item.emails:
            item.failed = True
            item.messages.append(f"invalid item: {item.label}")
            overall_success = False
            print(f"Skipping invalid item: {item.label}", file=sys.stderr)
            continue

        print(f"== Download {item.label}: {item.source_url}")
        ok = (
            download_podcast(item, args.run_date, downloader)
            if item.kind == "podcast"
            else download_youtube(item, archive_file)
        )
        overall_success = overall_success and ok
    print("Download phase: done")
    log_event("phase_download", "ok", time.monotonic() - phase_started)

    phase_started = time.monotonic()
    print("Transcribe phase: start")
    for item in items:
        if item.audio_path is None:
            print(f"Skipping transcribe for {item.label}: no audio")
            continue
        print(f"== Transcribe {item.label}: {item.audio_path}")
        ok = transcribe_item(
            item,
            transcribe_script,
            args.traditionalize_transcript,
            converter_script,
            args.opencc_config,
        )
        overall_success = overall_success and ok
    print("Transcribe phase: done")
    log_event("phase_transcribe", "ok", time.monotonic() - phase_started)

    phase_started = time.monotonic()
    print("Mail phase: start")
    for item in items:
        if item.mail_attachment_path is None:
            print(f"Skipping mail for {item.label}: no transcript")
            continue
        print(f"== Mail {item.label}: {', '.join(item.emails)}")
        ok = mail_item(item)
        overall_success = overall_success and ok
    print("Mail phase: done")
    log_event("phase_mail", "ok", time.monotonic() - phase_started)

    failed = [item for item in items if item.failed]
    if failed:
        print("Daily pipeline completed with partial failures:", file=sys.stderr)
        for item in failed:
            print(f"- {item.label}: {'; '.join(item.messages)}", file=sys.stderr)

    return 0 if overall_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
