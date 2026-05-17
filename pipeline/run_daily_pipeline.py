#!/usr/bin/env python3

import argparse
import os
import re
import subprocess
import sys
import time
import fcntl
import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import List

# Setup paths
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "tools"))
sys.path.insert(0, str(BASE_DIR / "pipeline"))

from recipient_groups import load_recipient_groups, resolve_emails
from local_config import load_local_config as load_env_file
from output_paths import (
    infer_podcast_slug,
    subscribed_podcast_output_dir,
    subscribed_youtube_output_dir,
    telegram_media_output_dir,
    telegram_url_output_dir,
    write_task_metadata,
    youtube_video_id,
)
from run_registered_podcasts import (
    load_subscriptions as load_podcast_subs,
    parse_run_date,
    resolve_podcast_title,
    transcript_path_for,
    sync_podcast_latest
)
from run_registered_youtube import (
    load_subscriptions as load_youtube_subs,
    sync_youtube_latest,
    run_command
)
from summarize_transcript import summarize_file
from notifier import get_notifiers, send_telegram_msg

from transcribers import BaseTranscriber, WhisperCPPTranscriber, WhisperKitTranscriber

def get_transcriber(args) -> BaseTranscriber:
    if args.transcriber_type == "whisperkit":
        bin_path = os.environ.get("WHISPERKIT_BIN", "/Users/willard/Downloads/WhisperKit/.build/arm64-apple-macosx/release/whisperkit-cli")
        model_path = os.environ.get("WHISPERKIT_MODEL_PATH")
        return WhisperKitTranscriber(bin_path, model_path)
    else:
        return WhisperCPPTranscriber(args.transcribe_script)

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
    duration_str: str = ""
    failed: bool = False
    download_ready: bool = False
    messages: list[str] = field(default_factory=list)

# --- OCP Pipeline Architecture ---

class PipelineContext:
    def __init__(self, args, items: List[DailyItem]):
        self.args = args
        self.items = items
        self.overall_ok = True

    def report_status(self, message: str):
        """Sends a progress update to Telegram if enabled."""
        print(f"[Status] {message}")
        if getattr(self.args, "telegram_progress", False):
            try:
                send_telegram_msg(message)
            except Exception as e:
                print(f"Failed to send Telegram status: {e}")

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
    @staticmethod
    def should_use_archive(context: PipelineContext) -> bool:
        return not bool(getattr(context.args, "url", None))

    def can_handle(self, item: DailyItem) -> bool:
        return item.kind == "youtube"
    
    def download(self, item: DailyItem, context: PipelineContext) -> bool:
        t_start = time.monotonic()
        use_archive = self.should_use_archive(context)
        
        # Delegate specific resolution and download logic to platform module
        res = sync_youtube_latest(item.source_url, item.output_dir, use_archive=use_archive)
        
        if res.success:
            item.audio_path = res.audio_path
            item.download_ready = True
            if res.specific_url:
                item.source_url = res.specific_url
            if res.title:
                item.title = res.title
            
            detail = res.audio_path.name if res.audio_path else "ok"
            self.log_event("ok", time.monotonic()-t_start, item.label, detail)
            return True
            
        return False

class PodcastDownloader(BaseDownloader):
    def can_handle(self, item: DailyItem) -> bool:
        return item.kind == "podcast"
    
    def download(self, item: DailyItem, context: PipelineContext) -> bool:
        t_start = time.monotonic()
        
        # Delegate resolution and download to platform module
        res = sync_podcast_latest(item.source_url, item.output_dir, context.args.run_date, debug_mode=context.args.debug)
        
        if res.skipped:
            self.log_event("skipped", time.monotonic()-t_start, item.label, "no episode")
            return True
            
        if res.success:
            item.audio_path = res.audio_path
            item.download_ready = True
            if res.specific_url:
                item.source_url = res.specific_url
            if res.title:
                item.title = res.title
                
            self.log_event("ok", time.monotonic()-t_start, item.label, res.audio_path.name)
            return True
        
        return False

class DownloadStage(BasePipelineStage):
    def run(self, context: PipelineContext) -> None:
        context.report_status("📂 開始下載階段...")
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
                        if item.download_ready:
                            context.report_status(f"✅ 下載完成: {item.label}")
                    else:
                        item.failed = True
                        context.overall_ok = False
                        context.report_status(f"❌ 下載失敗: {item.label}")
                    break
            
            if not handled and not item.failed:
                error_msg = f"  [Error] No downloader found for URL: {item.source_url}"
                print(error_msg)
                context.report_status(f"❌ 錯誤: 找不到支援的下載器 ({item.label})")
                item.failed = True
                context.overall_ok = False
                
        self.log_event("phase_download", "ok", time.monotonic() - start)

class TranscribeStage(BasePipelineStage):
    def run(self, context: PipelineContext) -> None:
        if not context.args.enable_transcribe:
            context.report_status("⏭️ 轉錄已禁用，跳過。")
            return
            
        context.report_status("🎙️ 開始轉錄階段 (GPU)...")
        start = time.monotonic()
        for item in context.items:
            if not item.audio_path or item.failed: continue
            t_start = time.monotonic()
            
            # Check for existing transcript or summary (idempotency)
            existing = transcript_path_for(item.audio_path)
            summary_hant = existing.with_name(existing.name.replace(".srt.txt", ".zh-Hant.summary.md"))
            
            if (existing and existing.exists()) or (summary_hant and summary_hant.exists()): 
                item.transcript_path = existing
                self.log_event("transcribe", "ok", 0, item.label, existing.name)
                context.report_status(f"⏭️ 轉錄已存在 (或摘要已完成): {item.label}")
            else:
                res = subprocess.run([context.args.transcribe_script, str(item.audio_path)])
                item.transcript_path = transcript_path_for(item.audio_path)
                if res.returncode == 0 and item.transcript_path.exists(): 
                    self.log_event("transcribe", "ok", time.monotonic()-t_start, item.label, item.transcript_path.name)
                    context.report_status(f"✅ 轉錄完成: {item.label}")
                else: 
                    item.failed = True
                    context.overall_ok = False
                    context.report_status(f"❌ 轉錄失敗: {item.label}")
        self.log_event("phase_transcribe", "ok", time.monotonic() - start)

class TraditionalizeStage(BasePipelineStage):
    def run(self, context: PipelineContext) -> None:
        if not context.args.enable_traditionalize:
            return
            
        context.report_status("🔠 開始簡轉繁階段...")
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
            context.report_status("⏭️ 摘要已禁用，跳過。")
            return
            
        context.report_status("🤖 開始 AI 摘要階段...")
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
                    context.report_status(f"✅ 摘要完成: {item.label}")
                else:
                    self.log_event("summarize", "failed", time.monotonic()-t_start, item.label)
                    context.report_status(f"❌ 摘要失敗: {item.label}")
        self.log_event("phase_summarize", "ok", time.monotonic() - start)

class NotificationStage(BasePipelineStage):
    def run(self, context: PipelineContext) -> None:
        context.report_status("✉️ 開始發送通知...")
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

def load_local_config() -> None:
    load_env_file(BASE_DIR / "config" / "local_config.sh", os.environ)

def build_items(args, root: Path) -> List[DailyItem]:
    groups = load_recipient_groups(Path(args.recipient_config).expanduser().resolve())
    items: list[DailyItem] = []
    
    # Ad-hoc single URL mode
    if args.url:
        emails = resolve_emails({"recipient_group": args.recipient_group}, groups)
        # Identify kind
        kind = "youtube" if "youtube.com" in args.url or "youtu.be" in args.url else "podcast"

        if getattr(args, "task_origin", "") == "telegram":
            output_dir = telegram_url_output_dir(root, args.url)
        else:
            output_dir = root / "adhoc"
            if kind == "youtube":
                vid = youtube_video_id(args.url) or "unknown"
                output_dir = root / f"adhoc_yt_{vid}"
            else:
                output_dir = root / f"adhoc_podcast_{infer_podcast_slug(args.url)}"
        
        items.append(DailyItem(
            label="Ad-hoc Task", 
            kind=kind, 
            source_url=args.url, 
            emails=emails, 
            output_dir=output_dir
        ))
        return items

    # Local file mode
    if getattr(args, "local_file", None):
        local_path = Path(args.local_file).expanduser().resolve()
        emails = resolve_emails({"recipient_group": args.recipient_group}, groups)
        
        suffix = local_path.suffix.lower()
        kind = "video" if suffix in {".mp4", ".mov", ".mkv"} else "voice"
        output_dir = local_path.parent
        
        item = DailyItem(
            label="Voice Message",
            kind=kind,
            source_url=f"file://{local_path}",
            emails=emails,
            output_dir=output_dir,
            audio_path=local_path,
            download_ready=True
        )
        items.append(item)
        return items

    # Standard subscription mode
    pod_cfg = Path(args.podcast_config).expanduser().resolve()
    yt_cfg = Path(args.youtube_config).expanduser().resolve()
    
    for i, sub in enumerate(load_podcast_subs(pod_cfg), 1):
        rss = sub.get("rss_url", "").strip()
        title = sub.get("podcast_title", "").strip() or (resolve_podcast_title(rss) if rss else "")
        prompt_file = Path(sub["prompt_file"]) if sub.get("prompt_file") else None
        items.append(DailyItem(f"Podcast {i}", "podcast", sub.get("podcast_url", rss), resolve_emails(sub, groups), subscribed_podcast_output_dir(root, title or "podcast"), prompt_file=prompt_file, title=title))
    for i, sub in enumerate(load_youtube_subs(yt_cfg), 1):
        url = sub.get("channel_url", "").strip()
        prompt_file = Path(sub["prompt_file"]) if sub.get("prompt_file") else None
        items.append(DailyItem(f"YouTube {i}" if i > 1 else "YouTube", "youtube", url, resolve_emails(sub, groups), subscribed_youtube_output_dir(root, url), prompt_file=prompt_file))
    return items


def write_task_metadata_for_items(items: List[DailyItem], args) -> None:
    if getattr(args, "task_origin", "") != "telegram":
        return

    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    created_at = datetime.now().astimezone().isoformat()
    for item in items:
        payload = {
            "task_origin": args.task_origin,
            "kind": item.kind,
            "source_url": item.source_url,
            "created_at": created_at,
            "chat_id": chat_id,
            "title": item.title,
            "label": item.label,
        }
        if item.kind == "youtube":
            payload["video_id"] = youtube_video_id(item.source_url) or ""
        write_task_metadata(item.output_dir, payload)

import concurrent.futures
import threading

# --- Concurrency & Locking ---

class TranscriptionLock:
    """A file-based lock to ensure only one transcription runs at a time across processes and threads."""
    def __init__(self, lock_file="/tmp/whisper_transcription.lock"):
        self.lock_file_path = Path(lock_file)
        self.lock_file = None
        self.thread_lock = threading.Lock()

    def __enter__(self):
        # 1. Thread-level lock (within same process)
        self.thread_lock.acquire()
        
        # 2. Process-level lock (across different processes)
        try:
            self.lock_file = open(self.lock_file_path, "w")
            fcntl.flock(self.lock_file, fcntl.LOCK_EX)
            self.lock_file.write(str(os.getpid()))
            self.lock_file.flush()
        except Exception:
            self.thread_lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.lock_file:
                fcntl.flock(self.lock_file, fcntl.LOCK_UN)
                self.lock_file.close()
                self.lock_file = None
        finally:
            self.thread_lock.release()

def process_item_full_lifecycle(item: DailyItem, args, context: PipelineContext, transcribe_lock: TranscriptionLock, transcriber: BaseTranscriber):
    """Processes a single item through all stages of the pipeline."""
    # 1. Download (Skip if already ready, e.g. local file)
    if not item.download_ready:
        downloaders = [cls() for cls in BaseDownloader.__subclasses__()]
        handled = False
        for d in downloaders:
            if d.can_handle(item):
                try:
                    if d.download(item, context):
                        handled = True
                        if item.download_ready:
                            context.report_status(f"✅ 下載完成: {item.label}")
                    else:
                        item.failed = True
                        context.report_status(f"❌ 下載失敗: {item.label}")
                except Exception as e:
                    print(f"Error downloading {item.label}: {e}")
                    item.failed = True
                break
        
        if not handled and not item.failed:
            item.failed = True
            context.report_status(f"❌ 錯誤: 找不到支援的下載器 ({item.label})")

    if item.failed or not item.download_ready:
        return

    # 2. Transcribe (LOCKED)
    existing = transcript_path_for(item.audio_path) if item.audio_path else None
    
    if args.enable_transcribe:
        summary_hant = existing.with_name(existing.name.replace(".srt.txt", ".zh-Hant.summary.md")) if existing else None
        
        # Check if existing transcription is valid and up-to-date
        is_stale = False
        if existing and existing.exists():
            if item.audio_path.stat().st_mtime > existing.stat().st_mtime:
                is_stale = True
        
        if (existing and existing.exists() and not is_stale) or (summary_hant and summary_hant.exists() and not is_stale): 
            item.transcript_path = existing
            if item.audio_path and item.audio_path.exists():
                item.duration_str = transcriber.get_audio_duration(item.audio_path)
            context.report_status(f"⏭️ 轉錄已存在: {item.label}")
        else:
            if is_stale:
                context.report_status(f"🔄 偵測到音檔更新，重新轉錄: {item.label}")
            context.report_status(f"⏳ 等待轉錄資源: {item.label} ...")
            with transcribe_lock:
                context.report_status(f"🎙️ 開始轉錄: {item.label} ({args.transcriber_type})")
                t_start = time.monotonic()
                item.transcript_path = transcriber.transcribe(item.audio_path, item.output_dir)
                
                # Extract duration
                if item.audio_path and item.audio_path.exists():
                    item.duration_str = transcriber.get_audio_duration(item.audio_path)

                if item.transcript_path and item.transcript_path.exists(): 
                    context.report_status(f"✅ 轉錄完成: {item.label}")
                    print(f"EVENT transcribe status=ok seconds={time.monotonic()-t_start:.2f} item=\"{item.label}\" detail=\"{item.transcript_path.name}\"")
                else: 
                    item.failed = True
                    context.report_status(f"❌ 轉錄失敗: {item.label}")
    else:
        # If transcription is disabled, still try to find existing transcript to continue
        if existing and existing.exists():
            item.transcript_path = existing
            context.report_status(f"⏭️ 轉錄已存在 (transcribe disabled): {item.label}")
        else:
            # Maybe check if .zh-Hant.txt exists directly
            txt_hant = existing.with_name(existing.name.replace(".srt.txt", ".zh-Hant.txt")) if existing else None
            if txt_hant and txt_hant.exists():
                # We can't set srt path easily if it doesn't exist, but summarize only needs target_txt
                # However, many parts assume transcript_path is the .srt.txt.
                # Let's just set it to the base one if it exists.
                item.transcript_path = existing 

    if item.failed or not item.transcript_path:
        return

    # 3. Traditionalize
    if args.enable_traditionalize:
        t_start = time.monotonic()
        conv_script = BASE_DIR / "tools" / "convert_transcript_opencc.py"
        # Convert .srt.txt
        if item.transcript_path.name.endswith(".srt.txt") and not item.transcript_path.name.endswith(".zh-Hant.srt.txt"):
            hant = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".zh-Hant.srt.txt"))
            if not (hant.exists() and hant.stat().st_mtime >= item.transcript_path.stat().st_mtime):
                run_command([sys.executable, str(conv_script), str(item.transcript_path), "--output-path", str(hant), "--config", args.opencc_config])
        
        # Convert .txt
        txt_path = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".txt"))
        if txt_path.exists() and not txt_path.name.endswith(".zh-Hant.txt"):
            txt_hant = txt_path.with_name(txt_path.name[:-4] + ".zh-Hant.txt")
            if not (txt_hant.exists() and txt_hant.stat().st_mtime >= txt_path.stat().st_mtime):
                run_command([sys.executable, str(conv_script), str(txt_path), "--output-path", str(txt_hant), "--config", args.opencc_config])
        
        # Update attachment path
        item.mail_attachment_path = item.transcript_path
        hant_srt = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".zh-Hant.srt.txt"))
        if hant_srt.exists():
            item.mail_attachment_path = hant_srt
        print(f"EVENT traditionalize status=ok seconds={time.monotonic()-t_start:.2f} item=\"{item.label}\"")

    # 4. Summarize
    if args.enable_summarize:
        txt_hant = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".zh-Hant.txt"))
        txt_plain = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".txt"))
        target_txt = txt_hant if (args.enable_traditionalize and txt_hant.exists()) else (txt_plain if txt_plain.exists() else txt_hant)
        
        if target_txt and target_txt.exists():
            t_start = time.monotonic()
            summary_path = summarize_file(target_txt, item.prompt_file)
            if summary_path and summary_path.exists():
                item.mail_body = summary_path.read_text(encoding="utf-8")
                context.report_status(f"✅ 摘要完成: {item.label}")
                print(f"EVENT summarize status=ok seconds={time.monotonic()-t_start:.2f} item=\"{item.label}\" detail=\"{summary_path.name}\"")
            else:
                context.report_status(f"❌ 摘要失敗: {item.label}")

    # 5. Notify
    notifiers = get_notifiers()
    for notifier in notifiers:
        if notifier.is_enabled(args):
            notifier.notify([item], args)

def main() -> int:
    load_local_config()
    parser = argparse.ArgumentParser(description="Daily pipeline.")
    parser.add_argument("--url", help="Ad-hoc URL to process (YouTube video or Podcast RSS/Page)")
    parser.add_argument("--local-file", help="Local audio file to process")
    parser.add_argument("--recipient-group", default="all", help="Recipient group for ad-hoc task")
    parser.add_argument("--date", dest="run_date", type=parse_run_date, default=date.today())
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--transcribe-script", default=os.environ.get("GENSRT_SCRIPT", str(BASE_DIR / "gensrt.sh")))
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
    parser.add_argument("--telegram-progress", dest="telegram_progress", action="store_true", help="Send real-time progress to Telegram")
    parser.add_argument("--telegram-chat-id", help="Override default Telegram chat ID")
    parser.add_argument("--concurrency", type=int, default=4, help="Number of concurrent downloads/summaries")
    parser.add_argument("--transcriber-type", default="whisperkit", choices=["whisperkit", "whispercpp"], help="Transcriber engine to use")
    parser.add_argument("--task-origin", default="default", choices=["default", "telegram"], help="Origin of the ad-hoc task for output routing")
    
    args = parser.parse_args()
    if args.telegram_chat_id:
        os.environ["TELEGRAM_CHAT_ID"] = args.telegram_chat_id
    
    root = Path(args.output_root).expanduser().resolve()
    items = build_items(args, root)
    write_task_metadata_for_items(items, args)
    
    if args.debug:
        debug_email = os.environ.get("DEBUG_RECIPIENT")
        if not debug_email:
            print("ERROR: DEBUG_RECIPIENT environment variable is not set.", file=sys.stderr)
            return 1
        for item in items: item.emails = [debug_email]
        debug_telegram = os.environ.get("DEBUG_TELEGRAM_CHAT_ID")
        if debug_telegram: os.environ["TELEGRAM_CHAT_ID"] = debug_telegram

    if not items:
        print("No subscriptions found.")
        return 0

    context = PipelineContext(args, items)
    transcribe_lock = TranscriptionLock()
    transcriber = get_transcriber(args)

    print(f"🚀 Starting concurrent pipeline (concurrency={args.concurrency}, transcriber={args.transcriber_type})")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(process_item_full_lifecycle, item, args, context, transcribe_lock, transcriber) for item in items]
        concurrent.futures.wait(futures)

    all_downloaded = all(item.download_ready for item in items)
    print(f"PIPELINE_ALL_DOWNLOADED={'1' if all_downloaded else '0'}")

    return 0 if context.overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
