#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from recipient_groups import load_recipient_groups, resolve_emails
from run_registered_podcasts import (
    load_subscriptions as load_podcast_subs,
    make_podcast_output_dir_name,
    parse_audio_path,
    parse_run_date,
    resolve_podcast_title,
    transcript_path_for,
    download_single_podcast,
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

from summarize_transcript import summarize_file
from notifier import get_notifiers

def load_local_config():
    config_path = Path(__file__).resolve().parent.parent / "config" / "local_config.sh"
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
    prompt_file: Path | None = None
    audio_path: Path | None = None
    transcript_path: Path | None = None
    mail_attachment_path: Path | None = None
    mail_body: str = ""
    title: str = ""
    failed: bool = False
    download_ready: bool = False
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
        prompt_file = Path(sub["prompt_file"]) if sub.get("prompt_file") else None
        items.append(DailyItem(f"Podcast {i}", "podcast", sub.get("podcast_url", rss), resolve_emails(sub, groups), root / make_podcast_output_dir_name(rss, title), prompt_file=prompt_file, title=title))
    for i, sub in enumerate(load_youtube_subs(yt_cfg), 1):
        url = sub.get("channel_url", "").strip()
        prompt_file = Path(sub["prompt_file"]) if sub.get("prompt_file") else None
        items.append(DailyItem(f"YouTube {i}" if i > 1 else "YouTube", "youtube", url, resolve_emails(sub, groups), root / make_channel_slug(url), prompt_file=prompt_file))
    return items

def main() -> int:
    load_local_config()
    parser = argparse.ArgumentParser(description="Daily pipeline.")
    parser.add_argument("--date", dest="run_date", type=parse_run_date, default=date.today())
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--transcribe-script", default=os.environ.get("GENSRT_SCRIPT", "gensrt.sh"))
    parser.add_argument("--podcast-config", default="config/subscriptions.json")
    parser.add_argument("--youtube-config", default="config/youtube_subscriptions.json")
    parser.add_argument("--recipient-config", default=os.environ.get("RECIPIENT_CONFIG_FILE", "config/recipient_groups.local.json"))
    parser.add_argument("--traditionalize-transcript", dest="enable_traditionalize", action="store_true", default=os.environ.get("ENABLE_TRADITIONALIZE", os.environ.get("OPENCC_TRADITIONALIZE", "0")) == "1")
    parser.add_argument("--opencc-config", default=os.environ.get("OPENCC_CONFIG", "s2twp.json"))
    parser.add_argument("--debug", action="store_true")
    
    # Stage toggles
    parser.add_argument("--enable-transcribe", type=int, default=int(os.environ.get("ENABLE_TRANSCRIBE", "1")))
    parser.add_argument("--enable-summarize", type=int, default=int(os.environ.get("ENABLE_SUMMARIZE", "1")))
    parser.add_argument("--enable-mail", type=int, default=int(os.environ.get("ENABLE_MAIL", "1")))
    parser.add_argument("--enable-telegram", type=int, default=int(os.environ.get("ENABLE_TELEGRAM", "1")))
    
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent.parent
    root = Path(args.output_root).expanduser().resolve()
    conv_script = base_dir / "tools" / "convert_transcript_opencc.py"
    downloader = base_dir / "pipeline" / "download_latest_podcast.py"

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
        
        debug_telegram = os.environ.get("DEBUG_TELEGRAM_CHAT_ID")
        if debug_telegram:
            print(f"DEBUG MODE ENABLED: Telegram messages will be redirected to {debug_telegram}")
            os.environ["TELEGRAM_CHAT_ID"] = debug_telegram

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
            if res.returncode == NO_EPISODE_EXIT_CODE:
                log_event("download", "skipped", time.monotonic()-t_start, item.label, "no episode")
            elif res.returncode == 0:
                item.audio_path = parse_audio_path(res.stdout)
                item.download_ready = item.audio_path is not None
                log_event("download", "ok", time.monotonic()-t_start, item.label, item.audio_path.name if item.audio_path else "")
            else: item.failed = True; overall_ok = False
        else:
            latest = resolve_youtube_latest(item.source_url)
            if not latest: item.failed = True; overall_ok = False; continue
            item.title = latest.get("title", "")
            vid = latest.get("id", "")
            existing = find_youtube_audio(item.output_dir, vid)
            if existing:
                item.audio_path = existing
                item.download_ready = True
                log_event("download", "ok", 0, item.label, existing.name)
            else:
                res = download_youtube_video(latest.get("webpage_url", ""), item.output_dir, base_dir / "id.txt")
                print_completed_process(res)
                item.audio_path = find_youtube_audio(item.output_dir, vid)
                if item.audio_path:
                    item.download_ready = True
                    log_event("download", "ok", time.monotonic()-t_start, item.label, item.audio_path.name)
                else: item.failed = True; overall_ok = False
    log_event("phase_download", "ok", time.monotonic() - start)

    print("Transcribe phase: start")
    start = time.monotonic()
    if args.enable_transcribe:
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
    else:
        print("  Transcribe execution disabled. Checking for existing files.")
        for item in items:
            if item.audio_path:
                item.transcript_path = transcript_path_for(item.audio_path)

    # Post-transcribe: Traditionalize and set mail attachments
    if args.enable_traditionalize:
        print("Traditionalize phase: start")
        for item in items:
            if not item.transcript_path or not item.transcript_path.exists() or item.failed: continue
            t_start = time.monotonic()
            
            # Convert .srt.txt
            if item.transcript_path.name.endswith(".srt.txt") and not item.transcript_path.name.endswith(".zh-Hant.srt.txt"):
                hant = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".zh-Hant.srt.txt"))
                if not (hant.exists() and hant.stat().st_mtime >= item.transcript_path.stat().st_mtime):
                    res = run_command([sys.executable, str(conv_script), str(item.transcript_path), "--output-path", str(hant), "--config", args.opencc_config])
                    if res.returncode == 0: log_event("traditionalize", "ok", time.monotonic()-t_start, item.label, hant.name)
            
            # Convert .txt
            txt_path = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".txt"))
            if txt_path.exists() and not txt_path.name.endswith(".zh-Hant.txt"):
                txt_hant = txt_path.with_name(txt_path.name[:-4] + ".zh-Hant.txt")
                if not (txt_hant.exists() and txt_hant.stat().st_mtime >= txt_path.stat().st_mtime):
                    res = run_command([sys.executable, str(conv_script), str(txt_path), "--output-path", str(txt_hant), "--config", args.opencc_config])
                    if res.returncode == 0: log_event("traditionalize", "ok", time.monotonic()-t_start, item.label, txt_hant.name)

    # Set attachment paths
    for item in items:
        if not item.transcript_path or not item.transcript_path.exists() or item.failed: continue
        item.mail_attachment_path = item.transcript_path
        if args.enable_traditionalize:
            hant_srt = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".zh-Hant.srt.txt"))
            if hant_srt.exists():
                item.mail_attachment_path = hant_srt

    log_event("phase_transcribe", "ok", time.monotonic() - start)

    print("Summarize phase: start")
    start = time.monotonic()
    if args.enable_summarize:
        for item in items:
            if not item.transcript_path or item.failed: continue
            
            # Priority: .zh-Hant.txt -> .txt
            txt_hant = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".zh-Hant.txt"))
            txt_plain = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".txt"))
            
            target_txt = None
            if args.enable_traditionalize and txt_hant.exists():
                target_txt = txt_hant
            elif txt_plain.exists():
                target_txt = txt_plain
            elif txt_hant.exists():
                target_txt = txt_hant
                
            if target_txt.exists():
                t_start = time.monotonic()
                summary_path = summarize_file(target_txt, item.prompt_file)
                if summary_path and summary_path.exists():
                    item.mail_body = summary_path.read_text(encoding="utf-8")
                    log_event("summarize", "ok", time.monotonic()-t_start, item.label, summary_path.name)
                else:
                    log_event("summarize", "failed", time.monotonic()-t_start, item.label)
    else:
        print("  Summarize disabled. Skipping.")
    log_event("phase_summarize", "ok", time.monotonic() - start)

    print("Notification phase: start")
    start = time.monotonic()
    
    notifiers = get_notifiers()
    active_any = False
    for notifier in notifiers:
        if notifier.is_enabled(args):
            active_any = True
            notifier.notify(items, args)
    
    if not active_any:
        print("  All notifications disabled. Skipping.")
    
    log_event("phase_notification", "ok", time.monotonic() - start)

    all_downloaded = all(item.download_ready for item in items)
    print(f"PIPELINE_ALL_DOWNLOADED={'1' if all_downloaded else '0'}")

    return 0 if overall_ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
