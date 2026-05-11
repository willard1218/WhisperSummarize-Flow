#!/usr/bin/env python3

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List

# Setup paths
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "tools"))
sys.path.insert(0, str(BASE_DIR / "pipeline"))

from recipient_groups import load_recipient_groups, resolve_emails
from run_registered_podcasts import (
    load_subscriptions as load_podcast_subs,
    make_podcast_output_dir_name,
    parse_audio_path,
    parse_run_date,
    resolve_podcast_title,
    transcript_path_for,
    download_single_podcast,
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

# --- Data Models ---

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

# --- OCP Pipeline Architecture ---

class PipelineContext:
    def __init__(self, args, items: List[DailyItem]):
        self.args = args
        self.items = items
        self.overall_ok = True

class BasePipelineStage:
    """Base class for all pipeline processing stages."""
    def run(self, context: PipelineContext) -> None:
        raise NotImplementedError

    def log_event(self, event: str, status: str, seconds: float, item: str = "", detail: str = "") -> None:
        parts = ["EVENT", event, f"status={status}", f"seconds={seconds:.2f}"]
        if item: parts.append(f'item="{item}"')
        if detail: parts.append(f'detail="{detail}"')
        print(" ".join(parts))

# --- OCP Downloader Architecture ---

class BaseDownloader:
    """Base class for URL-specific download logic."""
    def can_handle(self, item: DailyItem) -> bool:
        raise NotImplementedError
    
    def download(self, item: DailyItem, context: PipelineContext) -> bool:
        """Returns True if download succeeded, False otherwise."""
        raise NotImplementedError

    def log_event(self, status: str, seconds: float, item_label: str, detail: str = ""):
        parts = ["EVENT", "download", f"status={status}", f"seconds={seconds:.2f}", f'item="{item_label}"']
        if detail: parts.append(f'detail="{detail}"')
        print(" ".join(parts))

class YouTubeDownloader(BaseDownloader):
    def can_handle(self, item: DailyItem) -> bool:
        return item.kind == "youtube"
    
    def download(self, item: DailyItem, context: PipelineContext) -> bool:
        t_start = time.monotonic()
        is_direct_video = "watch?v=" in item.source_url or "youtu.be/" in item.source_url
        
        # Extract Video ID first to check for existing files
        vid_match = re.search(r"v=([a-zA-Z0-9_-]+)", item.source_url) or re.search(r"be/([a-zA-Z0-9_-]+)", item.source_url)
        vid = vid_match.group(1) if vid_match else None
        
        if vid:
            existing = find_youtube_audio(item.output_dir, vid)
            if existing:
                item.audio_path = existing
                item.download_ready = True
                self.log_event("ok", 0, item.label, existing.name)
                return True

        if is_direct_video:
            res = download_youtube_video(item.source_url, item.output_dir, BASE_DIR / "id.txt")
            if res.stdout: print(res.stdout, end="")
            if vid: item.audio_path = find_youtube_audio(item.output_dir, vid)
        else:
            latest = resolve_youtube_latest(item.source_url)
            if not latest: return False
            item.title = latest.get("title", "")
            vid = latest.get("id", "")
            # Double check existing again after resolution
            existing = find_youtube_audio(item.output_dir, vid)
            if existing:
                item.audio_path = existing
                item.download_ready = True
                self.log_event("ok", 0, item.label, existing.name)
                return True
            res = download_youtube_video(latest.get("webpage_url", ""), item.output_dir, BASE_DIR / "id.txt")
            if res.stdout: print(res.stdout, end="")
            item.audio_path = find_youtube_audio(item.output_dir, vid)
        
        if item.audio_path:
            item.download_ready = True
            self.log_event("ok", time.monotonic()-t_start, item.label, item.audio_path.name)
            return True
        return False

class PodcastDownloader(BaseDownloader):
    def can_handle(self, item: DailyItem) -> bool:
        return item.kind == "podcast"
    
    def download(self, item: DailyItem, context: PipelineContext) -> bool:
        t_start = time.monotonic()
        downloader_bin = BASE_DIR / "pipeline" / "download_latest_podcast.py"
        
        # Determine if we use specific date or latest
        run_date = context.args.run_date
        # Special case: in ad-hoc mode with a specific episode URL, we don't want a date filter
        if "/episodes/" in item.source_url:
            run_date = None

        res = download_single_podcast(item.source_url, item.output_dir, run_date, downloader_bin, item.title)
        
        # Fallback for debug mode
        if res.returncode == NO_EPISODE_EXIT_CODE and context.args.debug:
            print(f"Debug mode fallback: Fetching latest episode for {item.label} regardless of date")
            res = download_single_podcast(item.source_url, item.output_dir, None, downloader_bin, item.title)
        
        if res.stdout: print(res.stdout, end="")
        
        if res.returncode == NO_EPISODE_EXIT_CODE:
            self.log_event("skipped", time.monotonic()-t_start, item.label, "no episode")
            return True # Not a failure
        elif res.returncode == 0:
            item.audio_path = parse_audio_path(res.stdout)
            item.download_ready = item.audio_path is not None
            if item.audio_path:
                self.log_event("ok", time.monotonic()-t_start, item.label, item.audio_path.name)
            return True
        
        return False

class DownloadStage(BasePipelineStage):
    def run(self, context: PipelineContext) -> None:
        print("Download phase: start")
        start = time.monotonic()
        
        # Discover all downloader implementations
        downloaders = [cls() for cls in BaseDownloader.__subclasses__()]
        
        for item in context.items:
            item.output_dir.mkdir(parents=True, exist_ok=True)
            handled = False
            for d in downloaders:
                if d.can_handle(item):
                    if d.download(item, context):
                        handled = True
                    else:
                        item.failed = True
                        context.overall_ok = False
                    break
            
            if not handled and not item.failed:
                print(f"  [Error] No downloader found for URL: {item.source_url}")
                item.failed = True
                context.overall_ok = False
                
        self.log_event("phase_download", "ok", time.monotonic() - start)

class TranscribeStage(BasePipelineStage):
    def run(self, context: PipelineContext) -> None:
        print("Transcribe phase: start")
        start = time.monotonic()
        if context.args.enable_transcribe:
            for item in context.items:
                if not item.audio_path or item.failed: continue
                t_start = time.monotonic()
                existing = transcript_path_for(item.audio_path) if item.audio_path.suffix == ".mp3" else None
                if existing and existing.exists(): 
                    item.transcript_path = existing
                    self.log_event("transcribe", "ok", 0, item.label, existing.name)
                else:
                    res = subprocess.run([context.args.transcribe_script, str(item.audio_path)])
                    item.transcript_path = transcript_path_for(item.audio_path)
                    if res.returncode == 0 and item.transcript_path.exists(): 
                        self.log_event("transcribe", "ok", time.monotonic()-t_start, item.label, item.transcript_path.name)
                    else: 
                        item.failed = True
                        context.overall_ok = False
        else:
            print("  Transcribe execution disabled. Checking for existing files.")
            for item in context.items:
                if item.audio_path:
                    item.transcript_path = transcript_path_for(item.audio_path)
        self.log_event("phase_transcribe", "ok", time.monotonic() - start)

class TraditionalizeStage(BasePipelineStage):
    def run(self, context: PipelineContext) -> None:
        if not context.args.enable_traditionalize:
            return
            
        print("Traditionalize phase: start")
        start = time.monotonic()
        conv_script = BASE_DIR / "tools" / "convert_transcript_opencc.py"
        
        for item in context.items:
            if not item.transcript_path or not item.transcript_path.exists() or item.failed: continue
            t_start = time.monotonic()
            
            # Convert .srt.txt
            if item.transcript_path.name.endswith(".srt.txt") and not item.transcript_path.name.endswith(".zh-Hant.srt.txt"):
                hant = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".zh-Hant.srt.txt"))
                if not (hant.exists() and hant.stat().st_mtime >= item.transcript_path.stat().st_mtime):
                    res = run_command([sys.executable, str(conv_script), str(item.transcript_path), "--output-path", str(hant), "--config", context.args.opencc_config])
                    if res.returncode == 0: self.log_event("traditionalize", "ok", time.monotonic()-t_start, item.label, hant.name)
            
            # Convert .txt
            txt_path = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".txt"))
            if txt_path.exists() and not txt_path.name.endswith(".zh-Hant.txt"):
                txt_hant = txt_path.with_name(txt_path.name[:-4] + ".zh-Hant.txt")
                if not (txt_hant.exists() and txt_hant.stat().st_mtime >= txt_path.stat().st_mtime):
                    res = run_command([sys.executable, str(conv_script), str(txt_path), "--output-path", str(txt_hant), "--config", context.args.opencc_config])
                    if res.returncode == 0: self.log_event("traditionalize", "ok", time.monotonic()-t_start, item.label, txt_hant.name)

        # Update attachment paths to use Traditional Chinese if available
        for item in context.items:
            if not item.transcript_path or not item.transcript_path.exists() or item.failed: continue
            item.mail_attachment_path = item.transcript_path
            hant_srt = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".zh-Hant.srt.txt"))
            if hant_srt.exists():
                item.mail_attachment_path = hant_srt
        
        self.log_event("phase_traditionalize", "ok", time.monotonic() - start)

class SummarizeStage(BasePipelineStage):
    def run(self, context: PipelineContext) -> None:
        if not context.args.enable_summarize:
            print("  Summarize disabled. Skipping.")
            return
            
        print("Summarize phase: start")
        start = time.monotonic()
        for item in context.items:
            if not item.transcript_path or item.failed: continue
            
            # Priority: .zh-Hant.txt -> .txt
            txt_hant = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".zh-Hant.txt"))
            txt_plain = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".txt"))
            
            target_txt = None
            if context.args.enable_traditionalize and txt_hant.exists():
                target_txt = txt_hant
            elif txt_plain.exists():
                target_txt = txt_plain
            elif txt_hant.exists():
                target_txt = txt_hant
                
            if target_txt and target_txt.exists():
                t_start = time.monotonic()
                summary_path = summarize_file(target_txt, item.prompt_file)
                if summary_path and summary_path.exists():
                    item.mail_body = summary_path.read_text(encoding="utf-8")
                    self.log_event("summarize", "ok", time.monotonic()-t_start, item.label, summary_path.name)
                else:
                    self.log_event("summarize", "failed", time.monotonic()-t_start, item.label)
        self.log_event("phase_summarize", "ok", time.monotonic() - start)

class NotificationStage(BasePipelineStage):
    def run(self, context: PipelineContext) -> None:
        print("Notification phase: start")
        start = time.monotonic()
        
        notifiers = get_notifiers()
        active_any = False
        for notifier in notifiers:
            if notifier.is_enabled(context.args):
                active_any = True
                notifier.notify(context.items, context.args)
        
        if not active_any:
            print("  All notifications disabled. Skipping.")
        
        self.log_event("phase_notification", "ok", time.monotonic() - start)

# --- Configuration & Setup ---

def load_local_config():
    config_path = BASE_DIR / "config" / "local_config.sh"
    if config_path.exists():
        content = config_path.read_text()
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"): continue
            
            # Remove 'export ' prefix if present
            if line.startswith("export "):
                line = line[7:].strip()
            
            if "=" in line:
                try:
                    # Split only on first '=' and remove comments
                    kv_part = line.split("#", 1)[0].strip()
                    key, value = kv_part.split("=", 1)
                    value = value.strip().strip('"').strip("'")
                    
                    # Handle PATH variable specifically (append/prepend)
                    if key == "PATH":
                        os.environ["PATH"] = value.replace("$PATH", os.environ.get("PATH", ""))
                    else:
                        os.environ[key] = value
                except ValueError:
                    continue

def build_items(args, root: Path) -> List[DailyItem]:
    groups = load_recipient_groups(Path(args.recipient_config).expanduser().resolve())
    items: list[DailyItem] = []
    
    # Ad-hoc single URL mode
    if args.url:
        emails = resolve_emails({"recipient_group": args.recipient_group}, groups)
        # Identify kind
        kind = "youtube" if "youtube.com" in args.url or "youtu.be" in args.url else "podcast"
        
        output_dir = root / "adhoc"
        if kind == "youtube":
            # For ad-hoc youtube, we don't have a slug yet, use video ID if possible
            vid_match = re.search(r"v=([a-zA-Z0-9_-]+)", args.url) or re.search(r"be/([a-zA-Z0-9_-]+)", args.url)
            vid = vid_match.group(1) if vid_match else "unknown"
            output_dir = root / f"adhoc_yt_{vid}"
        
        items.append(DailyItem(
            label="Ad-hoc Task", 
            kind=kind, 
            source_url=args.url, 
            emails=emails, 
            output_dir=output_dir
        ))
        return items

    # Standard subscription mode
    pod_cfg = Path(args.podcast_config).expanduser().resolve()
    yt_cfg = Path(args.youtube_config).expanduser().resolve()
    
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
    parser.add_argument("--url", help="Ad-hoc URL to process (YouTube video or Podcast RSS/Page)")
    parser.add_argument("--recipient-group", default="all", help="Recipient group for ad-hoc task")
    parser.add_argument("--date", dest="run_date", type=parse_run_date, default=date.today())
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--transcribe-script", default=os.environ.get("GENSRT_SCRIPT", "gensrt.sh"))
    parser.add_argument("--podcast-config", default="config/subscriptions.json")
    parser.add_argument("--youtube-config", default="config/youtube_subscriptions.json")
    parser.add_argument("--recipient-config", default=os.environ.get("RECIPIENT_CONFIG_FILE", "config/recipient_groups.local.json"))
    parser.add_argument("--traditionalize-transcript", dest="enable_traditionalize", action="store_true", default=os.environ.get("ENABLE_TRADITIONALIZE", os.environ.get("OPENCC_TRADITIONALIZE", "1")) == "1")
    parser.add_argument("--opencc-config", default=os.environ.get("OPENCC_CONFIG", "s2twp.json"))
    parser.add_argument("--debug", action="store_true")
    
    # Stage toggles
    parser.add_argument("--enable-transcribe", type=int, default=int(os.environ.get("ENABLE_TRANSCRIBE", "1")))
    parser.add_argument("--enable-summarize", type=int, default=int(os.environ.get("ENABLE_SUMMARIZE", "1")))
    parser.add_argument("--enable-mail", type=int, default=int(os.environ.get("ENABLE_MAIL", "1")))
    parser.add_argument("--enable-telegram", type=int, default=int(os.environ.get("ENABLE_TELEGRAM", "1")))
    
    args = parser.parse_args()
    root = Path(args.output_root).expanduser().resolve()

    items = build_items(args, root)
    
    if args.debug:
        debug_email = os.environ.get("DEBUG_RECIPIENT")
        if not debug_email:
            print("ERROR: DEBUG_RECIPIENT environment variable is not set.", file=sys.stderr)
            return 1
        print(f"DEBUG MODE ENABLED: All emails redirected to {debug_email}")
        for item in items: item.emails = [debug_email]
        debug_telegram = os.environ.get("DEBUG_TELEGRAM_CHAT_ID")
        if debug_telegram:
            print(f"DEBUG MODE ENABLED: Telegram redirected to {debug_telegram}")
            os.environ["TELEGRAM_CHAT_ID"] = debug_telegram

    if not items:
        print("No subscriptions found.")
        return 0

    # --- Run Pipeline ---
    context = PipelineContext(args, items)
    
    # Define stages (Following OCP: sequence can be changed or extended easily)
    stages: List[BasePipelineStage] = [
        DownloadStage(),
        TranscribeStage(),
        TraditionalizeStage(),
        SummarizeStage(),
        NotificationStage()
    ]
    
    for stage in stages:
        stage.run(context)

    all_downloaded = all(item.download_ready for item in items)
    print(f"PIPELINE_ALL_DOWNLOADED={'1' if all_downloaded else '0'}")

    return 0 if context.overall_ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
