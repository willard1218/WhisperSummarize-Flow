#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from recipient_groups import load_recipient_groups, resolve_emails
from run_registered_podcasts import (
    load_subscriptions as load_podcast_subs,
    make_podcast_output_dir_name,
    parse_audio_path,
    parse_run_date,
    resolve_podcast_title,
    transcript_path_for,
    download_single_podcast,
    marker_path_for,
    send_mail,
    NO_EPISODE_EXIT_CODE
)
from run_registered_youtube import (
    load_subscriptions as load_youtube_subs,
    make_channel_slug,
    resolve_youtube_latest,
    find_youtube_audio,
    download_youtube_video,
    run_command
)

def load_local_config():
    config_path = Path(__file__).resolve().parent / "local_config.sh"
    if config_path.exists():
        content = config_path.read_text()
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                value = value.strip().strip('"').strip("'")
                os.environ[key] = value

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

def log_event(event: str, status: str, seconds: float, item: str = "", detail: str = "") -> None:
    parts = ["EVENT", event, f"status={status}", f"seconds={seconds:.2f}"]
    if item: parts.append(f'item="{item}"')
    if detail: parts.append(f'detail="{detail}"')
    print(" ".join(parts))

def print_completed_process(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout: print(result.stdout, end="")
    if result.stderr: print(result.stderr, end="", file=sys.stderr)

def build_items(pod_cfg: Path, yt_cfg: Path, rec_cfg: Path, root: Path) -> list[DailyItem]:
    groups = load_recipient_groups(rec_cfg)
    items: list[DailyItem] = []
    for i, sub in enumerate(load_podcast_subs(pod_cfg), 1):
        rss = sub.get("rss_url", "").strip()
        title = sub.get("podcast_title", "").strip() or (resolve_podcast_title(rss) if rss else "")
        items.append(DailyItem(f"Podcast {i}", "podcast", sub.get("podcast_url", rss), resolve_emails(sub, groups), root / make_podcast_output_dir_name(rss, title), title=title))
    for i, sub in enumerate(load_youtube_subs(yt_cfg), 1):
        url = sub.get("channel_url", "").strip()
        items.append(DailyItem(f"YouTube {i}" if i > 1 else "YouTube", "youtube", url, resolve_emails(sub, groups), root / make_channel_slug(url)))
    return items

def main() -> int:
    load_local_config()
    parser = argparse.ArgumentParser(description="Daily pipeline.")
    parser.add_argument("--date", dest="run_date", type=parse_run_date, default=date.today())
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--transcribe-script", default=os.environ.get("GENSRT_SCRIPT", "gensrt.sh"))
    parser.add_argument("--podcast-config", default="subscriptions.json")
    parser.add_argument("--youtube-config", default="youtube_subscriptions.json")
    parser.add_argument("--recipient-config", default=os.environ.get("RECIPIENT_CONFIG_FILE", "recipient_groups.local.json"))
    parser.add_argument("--traditionalize-transcript", action="store_true", default=os.environ.get("OPENCC_TRADITIONALIZE", "0") == "1")
    parser.add_argument("--opencc-config", default=os.environ.get("OPENCC_CONFIG", "s2twp.json"))
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    root = Path(args.output_root).expanduser().resolve()
    conv_script = base_dir / "convert_transcript_opencc.py"
    downloader = base_dir / "download_latest_podcast.py"

    items = build_items(Path(args.podcast_config).expanduser().resolve(), Path(args.youtube_config).expanduser().resolve(), Path(args.recipient_config).expanduser().resolve(), root)
    
    # 強制 Debug 模式覆蓋邏輯
    if args.debug:
        debug_email = os.environ.get("DEBUG_RECIPIENT")
        if not debug_email:
            print("ERROR: DEBUG_RECIPIENT environment variable is not set.", file=sys.stderr)
            return 1
        print(f"DEBUG MODE ENABLED: All emails will be redirected to {debug_email}")
        for item in items:
            item.emails = [debug_email]

    if not items:
        print("No subscriptions found.")
        return 0

    overall_ok = True
    print("Download phase: start")
    start = time.monotonic()
    for item in items:
        item.output_dir.mkdir(parents=True, exist_ok=True)
        t_start = time.monotonic()
        if item.kind == "podcast":
            res = download_single_podcast(item.source_url, item.output_dir, args.run_date, downloader, item.title)
            if res.returncode == NO_EPISODE_EXIT_CODE and args.debug:
                print(f"Debug mode fallback: Fetching latest episode for {item.label} regardless of date")
                res = download_single_podcast(item.source_url, item.output_dir, None, downloader, item.title)
            
            print_completed_process(res)
            if res.returncode == NO_EPISODE_EXIT_CODE: log_event("download", "skipped", time.monotonic()-t_start, item.label, "no episode")
            elif res.returncode == 0:
                item.audio_path = parse_audio_path(res.stdout)
                log_event("download", "ok", time.monotonic()-t_start, item.label, item.audio_path.name if item.audio_path else "")
            else: item.failed = True; overall_ok = False
        else:
            latest = resolve_youtube_latest(item.source_url)
            if not latest: item.failed = True; overall_ok = False; continue
            item.title = latest.get("title", "")
            vid = latest.get("id", "")
            existing = find_youtube_audio(item.output_dir, vid)
            if existing: item.audio_path = existing; log_event("download", "ok", 0, item.label, existing.name)
            else:
                res = download_youtube_video(latest.get("webpage_url", ""), item.output_dir, base_dir / "id.txt")
                print_completed_process(res)
                item.audio_path = find_youtube_audio(item.output_dir, vid)
                if item.audio_path: log_event("download", "ok", time.monotonic()-t_start, item.label, item.audio_path.name)
                else: item.failed = True; overall_ok = False
    log_event("phase_download", "ok", time.monotonic() - start)

    print("Transcribe phase: start")
    start = time.monotonic()
    for item in items:
        if not item.audio_path or item.failed: continue
        t_start = time.monotonic()
        existing = transcript_path_for(item.audio_path) if item.audio_path.suffix == ".mp3" else None
        if existing and existing.exists(): item.transcript_path = existing; log_event("transcribe", "ok", 0, item.label, existing.name)
        else:
            res = subprocess.run([args.transcribe_script, str(item.audio_path)])
            item.transcript_path = transcript_path_for(item.audio_path)
            if res.returncode == 0 and item.transcript_path.exists(): log_event("transcribe", "ok", time.monotonic()-t_start, item.label, item.transcript_path.name)
            else: item.failed = True; overall_ok = False; continue
        
        item.mail_attachment_path = item.transcript_path
        if args.traditionalize_transcript and item.transcript_path.name.endswith(".srt.txt"):
            hant = item.transcript_path.with_name(item.transcript_path.name[:-8] + ".zh-Hant.srt.txt")
            if not (hant.exists() and hant.stat().st_mtime >= item.transcript_path.stat().st_mtime):
                res = run_command([sys.executable, str(conv_script), str(item.transcript_path), "--output-path", str(hant), "--config", args.opencc_config])
                if res.returncode == 0: log_event("traditionalize", "ok", time.monotonic()-t_start, item.label, hant.name)
            if hant.exists(): item.mail_attachment_path = hant
    log_event("phase_transcribe", "ok", time.monotonic() - start)

    print("Mail phase: start")
    start = time.monotonic()
    for item in items:
        if not item.mail_attachment_path or item.failed: continue
        prefix = "YouTube" if item.kind == "youtube" else "Podcast"
        subject = f"{prefix} transcript {item.title or item.audio_path.stem}"
        for email in item.emails:
            marker = marker_path_for(item.mail_attachment_path, email)
            if not marker.exists():
                t_start = time.monotonic()
                try:
                    send_mail(email, subject, item.mail_attachment_path)
                    marker.touch(); log_event("mail", "ok", time.monotonic()-t_start, item.label, email)
                except Exception: item.failed = True; overall_ok = False
    log_event("phase_mail", "ok", time.monotonic() - start)

    return 0 if overall_ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
