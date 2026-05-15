#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from recipient_groups import resolve_emails, load_recipient_groups
from output_paths import subscribed_youtube_output_dir, youtube_channel_directory_name

def load_subscriptions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    subscriptions = data.get("subscriptions", [])
    return [item for item in subscriptions if isinstance(item, dict)]

def make_channel_slug(channel_url: str) -> str:
    return youtube_channel_directory_name(channel_url)

def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True)

def resolve_youtube_latest(channel_url: str) -> dict | None:
    yt_dlp_bin = os.environ.get("YT_DLP_BIN", "yt-dlp")
    result = run_command([
        yt_dlp_bin, "--flat-playlist", "--playlist-end", "1",
        "--match-filter", "live_status=was_live", "--print-json", channel_url
    ])
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return json.loads(result.stdout.splitlines()[-1])

def find_youtube_audio(output_dir: Path, video_id: str) -> Path | None:
    matches = sorted(output_dir.glob(f"*__{video_id}.mp3"))
    return matches[0] if matches else None

def download_youtube_video(video_url: str, output_dir: Path, archive_file: Path | None = None) -> subprocess.CompletedProcess:
    yt_dlp_bin = os.environ.get("YT_DLP_BIN", "yt-dlp")
    ffmpeg_bin = os.environ.get("FFMPEG_BIN")
    
    command = [
        yt_dlp_bin, "--no-overwrites",
        "-f", "bestaudio/best", "-x", "--audio-format", "mp3",
        "--paths", str(output_dir), "-o", "%(title).200B__%(id)s.%(ext)s"
    ]

    if archive_file is not None:
        command[1:1] = ["--download-archive", str(archive_file)]
    
    if ffmpeg_bin:
        command.extend(["--ffmpeg-location", ffmpeg_bin])
        
    command.append(video_url)
    return run_command(command)

def main() -> int:
    parser = argparse.ArgumentParser(description="Process registered YouTube channels.")
    parser.add_argument("--config", default="youtube_subscriptions.json")
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--transcribe-script", default=os.environ.get("GENSRT_SCRIPT", "gensrt.sh"))
    parser.add_argument("--recipient-config", default=os.environ.get("RECIPIENT_CONFIG_FILE", "recipient_groups.local.json"))
    parser.add_argument("--traditionalize-transcript", action="store_true", default=os.environ.get("OPENCC_TRADITIONALIZE", "0") == "1")
    parser.add_argument("--opencc-config", default=os.environ.get("OPENCC_CONFIG", "s2twp.json"))
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    debug_email = os.environ.get("DEBUG_RECIPIENT")
    if args.debug and not debug_email:
        print("DEBUG_RECIPIENT not set.", file=sys.stderr)
        return 1

    base_dir = Path(__file__).resolve().parent
    output_root = Path(args.output_root).expanduser().resolve()
    runner = base_dir / "download_and_transcribe_latest.sh"
    subscriptions = load_subscriptions(Path(args.config).expanduser().resolve())
    recipient_groups = load_recipient_groups(Path(args.recipient_config).expanduser().resolve())

    overall_success = True
    for sub in subscriptions:
        url = sub.get("channel_url", "").strip()
        emails = [debug_email] if args.debug else resolve_emails(sub, recipient_groups)
        if not url or not emails: continue

        env = os.environ.copy()
        env["OPENCC_TRADITIONALIZE"] = "1" if args.traditionalize_transcript else "0"
        env["OPENCC_CONFIG"] = args.opencc_config
        env["DEBUG_RECIPIENT"] = debug_email or ""

        res = subprocess.run([
            "/bin/bash", str(runner), url, str(subscribed_youtube_output_dir(output_root, url)),
            ",".join(emails), args.transcribe_script
        ], text=True, capture_output=True, env=env)
        
        if res.stdout: print(res.stdout)
        if res.returncode != 0:
            overall_success = False
            continue

        transcript_path = None
        for line in res.stdout.splitlines():
            if line.startswith("Transcript: "):
                transcript_path = Path(line.split(": ", 1)[1].strip())
        
        if transcript_path and transcript_path.exists():
            from notifier import send_mail, marker_path_for
            subject = f"YouTube transcript {transcript_path.stem.split('__')[0]}"
            for email in emails:
                marker = marker_path_for(transcript_path, email)
                if not marker.exists():
                    try:
                        send_mail(email, subject, transcript_path)
                        marker.touch()
                        print(f"Sent mail to {email}")
                    except Exception as e:
                        print(f"Failed to send mail to {email}: {e}")
                        overall_success = False

    return 0 if overall_success else 1

if __name__ == "__main__":
    raise SystemExit(main())
