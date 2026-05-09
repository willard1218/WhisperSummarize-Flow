#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from download_latest_podcast import fetch_bytes, sanitize_filename
from recipient_groups import resolve_emails, load_recipient_groups
from notifier import marker_path_for, send_mail

NO_EPISODE_EXIT_CODE = 2

def parse_run_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date '{value}'. Expected YYYY-MM-DD.") from exc

def load_subscriptions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    subscriptions = data.get("subscriptions", [])
    return [item for item in subscriptions if isinstance(item, dict)]

def make_podcast_slug(rss_url: str) -> str:
    tail = rss_url.rstrip("/").rsplit("/", 1)[-1]
    base = tail.rsplit(".", 1)[0]
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", base).strip("-").lower()
    return cleaned or "podcast"

def sanitize_slug_part(value: str) -> str:
    return sanitize_filename(value).replace(" ", "-").strip("-")

def resolve_podcast_title(rss_url: str) -> str:
    rss_data = fetch_bytes(rss_url)
    root = ET.fromstring(rss_data)
    channel = root.find("channel")
    return (channel.findtext("title") or "").strip() if channel is not None else ""

def make_podcast_output_dir_name(rss_url: str, podcast_title: str) -> str:
    slug = make_podcast_slug(rss_url)
    title_slug = sanitize_slug_part(podcast_title)
    return f"{title_slug}__{slug}" if title_slug else slug

def parse_audio_path(output: str) -> Path | None:
    for line in output.splitlines():
        if line.startswith("Saved: ") or line.startswith("File already exists: "):
            return Path(line.split(": ", 1)[1].strip())
    return None

def transcript_path_for(audio_path: Path) -> Path:
    return audio_path.with_suffix("").with_name(audio_path.with_suffix("").name + ".srt.txt")

def download_single_podcast(rss_url: str, output_dir: Path, run_date: date | None, downloader: Path, title: str = "") -> subprocess.CompletedProcess:
    command = [sys.executable, str(downloader), rss_url, "-o", str(output_dir)]
    if run_date:
        command.extend(["--episode-date", run_date.isoformat()])
    if title:
        command.extend(["--show-title", title])
    return subprocess.run(command, text=True, capture_output=True)

def main() -> int:
    parser = argparse.ArgumentParser(description="Process registered podcasts.")
    parser.add_argument("--config", default="subscriptions.json")
    parser.add_argument("--date", dest="run_date", type=parse_run_date, default=date.today())
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
    downloader = base_dir / "download_latest_podcast.py"
    converter_script = base_dir / "convert_transcript_opencc.py"
    recipient_groups = load_recipient_groups(Path(args.recipient_config).expanduser().resolve())
    subscriptions = load_subscriptions(Path(args.config).expanduser().resolve())

    overall_success = True
    for sub in subscriptions:
        rss_url = sub.get("rss_url", "").strip()
        emails = [debug_email] if args.debug else resolve_emails(sub, recipient_groups)
        if not rss_url or not emails: continue

        title = sub.get("podcast_title", "").strip() or resolve_podcast_title(rss_url)
        out_dir = output_root / make_podcast_output_dir_name(rss_url, title)
        out_dir.mkdir(parents=True, exist_ok=True)

        res = download_single_podcast(rss_url, out_dir, args.run_date, downloader, title)
        if res.returncode == NO_EPISODE_EXIT_CODE and args.debug:
            print(f"Debug mode fallback: Fetching latest episode for '{title}' regardless of date")
            res = download_single_podcast(rss_url, out_dir, None, downloader, title)

        if res.returncode != 0 and res.returncode != NO_EPISODE_EXIT_CODE:
            overall_success = False; continue

        audio_path = parse_audio_path(res.stdout)
        if not audio_path: continue

        subprocess.run([args.transcribe_script, str(audio_path)])
        transcript_path = transcript_path_for(audio_path)
        if not transcript_path.exists(): continue

        mail_path = transcript_path
        if args.traditionalize_transcript:
            if transcript_path.name.endswith(".srt.txt"):
                hant_path = transcript_path.with_name(transcript_path.name[:-8] + ".zh-Hant.srt.txt")
                conv = subprocess.run([sys.executable, str(converter_script), str(transcript_path), "--output-path", str(hant_path), "--config", args.opencc_config])
                if conv.returncode == 0: mail_path = hant_path
            
            txt_path = transcript_path.with_name(transcript_path.name.replace(".srt.txt", ".txt"))
            if txt_path.exists():
                txt_hant = txt_path.with_name(txt_path.name[:-4] + ".zh-Hant.txt")
                subprocess.run([sys.executable, str(converter_script), str(txt_path), "--output-path", str(txt_hant), "--config", args.opencc_config])

        subject = f"Podcast transcript {audio_path.stem}"
        for email in emails:
            if not marker_path_for(mail_path, email).exists():
                try:
                    send_mail(email, subject, mail_path)
                    marker_path_for(mail_path, email).touch()
                except Exception: overall_success = False
    return 0 if overall_success else 1

if __name__ == "__main__":
    raise SystemExit(main())
