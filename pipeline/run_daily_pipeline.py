#!/usr/bin/env python3

import argparse
import os
import re
import subprocess
import sys
import time
import fcntl
import hashlib
import concurrent.futures
import threading
import logging
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
from logger import setup_logging, get_logger, TaskLogger

from transcribers import BaseTranscriber, WhisperCPPTranscriber, WhisperKitTranscriber

logger = get_logger("pipeline")

def get_transcriber(args) -> BaseTranscriber:
    if args.transcriber_type == "whisperkit":
        bin_path = os.environ.get("WHISPERKIT_BIN")
        if not bin_path:
            bin_path = "whisperkit-cli"
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
    processing_time_str: str = ""
    summarization_time_str: str = ""
    failed: bool = False
    download_ready: bool = False
    messages: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    def log_trace(self, message: str):
        self.trace.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

# --- OCP Pipeline Architecture ---

class PipelineContext:
    def __init__(self, args, items: List[DailyItem]):
        self.args = args
        self.items = items
        self.overall_ok = True
        self.task_loggers = [TaskLogger("pipeline", item.label) for item in items]

    def report_status(self, item_index: int, message: str, level: str = "info"):
        """Logs a message with task context and optionally sends to Telegram."""
        task_logger = self.task_loggers[item_index]
        if level == "error":
            task_logger.error(message)
            self.overall_ok = False
        else:
            task_logger.info(message)

    def log_event(self, item_index: int, action: str, status: str, duration: float = 0, detail: str = ""):
        msg = f"EVENT {action} status={status} duration={duration:.2f}s"
        if detail: msg += f" detail=\"{detail}\""
        self.task_loggers[item_index].info(msg, action=action)

class BaseDownloader:
    def can_handle(self, item: DailyItem) -> bool: raise NotImplementedError
    def download(self, item: DailyItem, context: PipelineContext, item_index: int) -> bool: raise NotImplementedError

class YouTubeDownloader(BaseDownloader):
    @staticmethod
    def should_use_archive(context: PipelineContext) -> bool:
        return not bool(getattr(context.args, "url", None))

    def can_handle(self, item: DailyItem) -> bool:
        return item.kind == "youtube"
    
    def download(self, item: DailyItem, context: PipelineContext, item_index: int) -> bool:
        t_start = time.monotonic()
        use_archive = self.should_use_archive(context)
        
        res = sync_youtube_latest(item.source_url, item.output_dir, use_archive=use_archive)
        
        if res.success:
            item.audio_path = res.audio_path
            item.download_ready = True
            if res.specific_url:
                item.source_url = res.specific_url
            if res.title:
                item.title = res.title
            
            detail = res.audio_path.name if res.audio_path else "ok"
            context.log_event(item_index, "download", "ok", time.monotonic()-t_start, detail)
            return True
            
        return False

class PodcastDownloader(BaseDownloader):
    def can_handle(self, item: DailyItem) -> bool:
        return item.kind == "podcast"
    
    def download(self, item: DailyItem, context: PipelineContext, item_index: int) -> bool:
        t_start = time.monotonic()
        
        res = sync_podcast_latest(item.source_url, item.output_dir, context.args.run_date, debug_mode=context.args.debug)
        
        if res.skipped:
            if context.args.url: # Ad-hoc task
                item.failed = True
                error_msg = f"❌ 找不到該日期的單集: {item.label}"
                item.messages.append(error_msg)
                context.log_event(item_index, "download", "failed", time.monotonic()-t_start, "no episode for specific request")
                return False
            else:
                context.log_event(item_index, "download", "skipped", time.monotonic()-t_start, "no episode")
                return True
            
        if res.success:
            item.audio_path = res.audio_path
            item.download_ready = True
            if res.specific_url:
                item.source_url = res.specific_url
            if res.title:
                item.title = res.title
                
            context.log_event(item_index, "download", "ok", time.monotonic()-t_start, res.audio_path.name)
            return True
        
        return False

# --- Concurrency & Locking ---

class TranscriptionLock:
    """A file-based lock to ensure only one transcription runs at a time across processes and threads."""
    def __init__(self, lock_file="/tmp/whisper_transcription.lock"):
        self.lock_file_path = Path(lock_file)
        self.lock_file = None
        self.thread_lock = threading.Lock()

    def __enter__(self):
        self.thread_lock.acquire()
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

def process_item_full_lifecycle(item_index: int, args, context: PipelineContext, transcribe_lock: TranscriptionLock, transcriber: BaseTranscriber):
    """Processes a single item through all stages of the pipeline."""
    item = context.items[item_index]
    
    # 1. Download
    if not item.download_ready:
        downloaders = [cls() for cls in BaseDownloader.__subclasses__()]
        handled = False
        for d in downloaders:
            if d.can_handle(item):
                try:
                    if d.download(item, context, item_index):
                        handled = True
                        if item.download_ready:
                            context.report_status(item_index, "✅ 下載完成")
                    else:
                        item.failed = True
                        error_msg = f"❌ {item.label} 下載失敗"
                        context.report_status(item_index, error_msg, level="error")
                        item.messages.append("Download failed")
                        send_telegram_msg(f"{error_msg} (URL: {item.source_url})")
                except Exception as e:
                    item.failed = True
                    error_msg = f"❌ {item.label} 下載錯誤: {e}"
                    context.report_status(item_index, error_msg, level="error")
                    item.messages.append(str(e))
                    send_telegram_msg(f"{error_msg} (URL: {item.source_url})")
                break
        
        if not handled and not item.failed:
            item.failed = True
            error_msg = f"❌ {item.label} 錯誤: 找不到支援的下載器"
            context.report_status(item_index, error_msg, level="error")
            item.messages.append("No downloader found")
            send_telegram_msg(error_msg)

    if item.failed or not item.download_ready:
        return

    # 2. Transcribe (LOCKED)
    existing = transcript_path_for(item.audio_path) if item.audio_path else None
    
    if args.enable_transcribe:
        summary_hant = existing.with_name(existing.name.replace(".srt.txt", ".zh-Hant.summary.md")) if existing else None
        
        is_stale = False
        if existing and existing.exists():
            if item.audio_path.stat().st_mtime > existing.stat().st_mtime:
                is_stale = True
        
        if (existing and existing.exists() and not is_stale) or (summary_hant and summary_hant.exists() and not is_stale): 
            item.transcript_path = existing
            if item.audio_path and item.audio_path.exists():
                item.duration_str = transcriber.get_audio_duration(item.audio_path)
            context.report_status(item_index, "⏭️ 轉錄已存在")
        else:
            if is_stale:
                context.report_status(item_index, "🔄 偵測到音檔更新，重新轉錄")
            context.report_status(item_index, "⏳ 等待轉錄資源...")
            with transcribe_lock:
                context.report_status(item_index, f"🎙️ 開始轉錄 ({args.transcriber_type})")
                t_start = time.monotonic()
                
                item.transcript_path = transcriber.transcribe(item.audio_path, item.output_dir)
                
                if item.transcript_path and item.transcript_path.exists(): 
                    duration_secs = time.monotonic() - t_start
                    context.report_status(item_index, "✅ 轉錄完成")
                    context.log_event(item_index, "transcribe", "ok", duration_secs, item.transcript_path.name)
                    
                    # Format processing time as HH:MM:SS
                    h = int(duration_secs // 3600)
                    m = int((duration_secs % 3600) // 60)
                    s = int(duration_secs % 60)
                    item.processing_time_str = f"{h:02}:{m:02}:{s:02}"
                    
                    if item.audio_path and item.audio_path.exists():
                        item.duration_str = transcriber.get_audio_duration(item.audio_path)
                else: 
                    item.failed = True
                    error_msg = f"❌ {item.label} 轉錄失敗"
                    context.report_status(item_index, error_msg, level="error")
                    item.messages.append("Transcription failed")
                    send_telegram_msg(f"{error_msg} (Audio: {item.audio_path.name if item.audio_path else 'unknown'})")
    else:
        if existing and existing.exists():
            item.transcript_path = existing
            context.report_status(item_index, "⏭️ 轉錄已存在 (transcribe disabled)")

    if item.failed or not item.transcript_path:
        return

    # 3. Traditionalize
    if args.enable_traditionalize:
        t_start = time.monotonic()
        conv_script = BASE_DIR / "tools" / "convert_transcript_opencc.py"
        try:
            if item.transcript_path.name.endswith(".srt.txt") and not item.transcript_path.name.endswith(".zh-Hant.srt.txt"):
                hant = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".zh-Hant.srt.txt"))
                if not (hant.exists() and hant.stat().st_mtime >= item.transcript_path.stat().st_mtime):
                    run_command([sys.executable, str(conv_script), str(item.transcript_path), "--output-path", str(hant), "--config", args.opencc_config])
            
            txt_path = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".txt"))
            if txt_path.exists() and not txt_path.name.endswith(".zh-Hant.txt"):
                txt_hant = txt_path.with_name(txt_path.name[:-4] + ".zh-Hant.txt")
                if not (txt_hant.exists() and txt_hant.stat().st_mtime >= txt_path.stat().st_mtime):
                    run_command([sys.executable, str(conv_script), str(txt_path), "--output-path", str(txt_hant), "--config", args.opencc_config])
            
            hant_srt = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".zh-Hant.srt.txt"))
            if hant_srt.exists():
                item.mail_attachment_path = hant_srt
            context.log_event(item_index, "traditionalize", "ok", time.monotonic()-t_start)
        except Exception as e:
            item.failed = True
            error_msg = f"❌ {item.label} 繁體化失敗: {e}"
            context.report_status(item_index, error_msg, level="error")
            item.messages.append(error_msg)
            send_telegram_msg(error_msg)

    if item.failed: return

    # 4. Summarize
    if args.enable_summarize:
        txt_hant = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".zh-Hant.txt"))
        txt_plain = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".txt"))
        target_txt = txt_hant if (args.enable_traditionalize and txt_hant.exists()) else (txt_plain if txt_plain.exists() else txt_hant)
        
        if target_txt and target_txt.exists():
            t_start = time.monotonic()
            try:
                summary_path = summarize_file(target_txt, item.prompt_file)
                if summary_path and summary_path.exists():
                    duration_secs = time.monotonic() - t_start
                    item.mail_body = summary_path.read_text(encoding="utf-8")
                    context.report_status(item_index, "✅ 摘要完成")
                    context.log_event(item_index, "summarize", "ok", duration_secs, summary_path.name)
                    
                    # Format summarization time as HH:MM:SS
                    h = int(duration_secs // 3600)
                    m = int((duration_secs % 3600) // 60)
                    s = int(duration_secs % 60)
                    item.summarization_time_str = f"{h:02}:{m:02}:{s:02}"
                else:
                    raise RuntimeError(f"Summary result empty for {item.label}")
            except Exception as e:
                item.failed = True
                error_msg = f"❌ {item.label} 摘要失敗: {e}"
                context.report_status(item_index, error_msg, level="error")
                item.messages.append(error_msg)
                send_telegram_msg(error_msg)
        else:
            item.failed = True
            error_msg = f"❌ {item.label} 找不到摘要所需的文字檔"
            context.report_status(item_index, error_msg, level="error")
            item.messages.append(error_msg)
            send_telegram_msg(error_msg)

    if item.failed: return

    # 5. Notify
    if not item.mail_attachment_path and item.transcript_path:
        item.mail_attachment_path = item.transcript_path

    notifiers = get_notifiers()
    for notifier in notifiers:
        if notifier.is_enabled(args):
            try:
                notifier.notify([item], args)
            except Exception as e:
                error_msg = f"❌ {item.label} 通知失敗 ({notifier.name}): {e}"
                context.report_status(item_index, error_msg, level="error")
                # Don't send Telegram here if it was the Telegram notifier that failed to avoid loops, 
                # but we'll try sending via send_telegram_msg directly for other errors.
                if notifier.name != "telegram":
                    send_telegram_msg(error_msg)

# --- Configuration & Setup ---

def load_local_config() -> None:
    load_env_file(BASE_DIR / "config" / "local_config.sh", os.environ)

def build_items(args, root: Path) -> List[DailyItem]:
    groups = load_recipient_groups(Path(args.recipient_config).expanduser().resolve())
    items: list[DailyItem] = []
    
    if args.url:
        emails = resolve_emails({"recipient_group": args.recipient_group}, groups)
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
        items.append(DailyItem(label="Ad-hoc Task", kind=kind, source_url=args.url, emails=emails, output_dir=output_dir))
        return items

    if getattr(args, "local_file", None):
        local_path = Path(args.local_file).expanduser().resolve()
        emails = resolve_emails({"recipient_group": args.recipient_group}, groups)
        suffix = local_path.suffix.lower()
        kind = "video" if suffix in {".mp4", ".mov", ".mkv"} else "voice"
        items.append(DailyItem(label="Voice Message", kind=kind, source_url=f"file://{local_path}", emails=emails, output_dir=local_path.parent, audio_path=local_path, download_ready=True))
        return items

    pod_cfg = Path(args.podcast_config).expanduser().resolve()
    yt_cfg = Path(args.youtube_config).expanduser().resolve()
    
    for i, sub in enumerate(load_podcast_subs(pod_cfg), 1):
        rss = sub.get("rss_url", "").strip()
        title = sub.get("podcast_title", "").strip() or (resolve_podcast_title(rss) if rss else "")
        prompt_file = BASE_DIR / sub["prompt_file"] if sub.get("prompt_file") else None
        items.append(DailyItem(f"Podcast {i}", "podcast", sub.get("podcast_url", rss), resolve_emails(sub, groups), subscribed_podcast_output_dir(root, title or "podcast"), prompt_file=prompt_file, title=title))
    for i, sub in enumerate(load_youtube_subs(yt_cfg), 1):
        url = sub.get("channel_url", "").strip()
        prompt_file = BASE_DIR / sub["prompt_file"] if sub.get("prompt_file") else None
        items.append(DailyItem(f"YouTube {i}" if i > 1 else "YouTube", "youtube", url, resolve_emails(sub, groups), subscribed_youtube_output_dir(root, url), prompt_file=prompt_file))
    return items

def write_task_metadata_for_items(items: List[DailyItem], args) -> None:
    if getattr(args, "task_origin", "") != "telegram":
        return
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    created_at = datetime.now().astimezone().isoformat()
    for item in items:
        payload = {"task_origin": args.task_origin, "kind": item.kind, "source_url": item.source_url, "created_at": created_at, "chat_id": chat_id, "title": item.title, "label": item.label}
        if item.kind == "youtube": payload["video_id"] = youtube_video_id(item.source_url) or ""
        write_task_metadata(item.output_dir, payload)

def main() -> int:
    load_local_config()
    parser = argparse.ArgumentParser(description="Daily pipeline.")
    parser.add_argument("--url", help="Ad-hoc URL to process")
    parser.add_argument("--local-file", help="Local audio file to process")
    parser.add_argument("--recipient-group", default="all", help="Recipient group for ad-hoc task")
    parser.add_argument("--date", dest="run_date", type=parse_run_date, default=date.today())
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--transcribe-script", default=os.environ.get("GENSRT_SCRIPT", str(BASE_DIR / "gensrt.sh")))
    parser.add_argument("--podcast-config", default="config/subscriptions.json")
    parser.add_argument("--youtube-config", default="config/youtube_subscriptions.json")
    parser.add_argument("--recipient-config", default=os.environ.get("RECIPIENT_CONFIG_FILE", "config/recipient_groups.local.json"))
    parser.add_argument("--traditionalize-transcript", dest="enable_traditionalize", action="store_true", default=os.environ.get("ENABLE_TRADITIONALIZE", "1") == "1")
    parser.add_argument("--opencc-config", default=os.environ.get("OPENCC_CONFIG", "s2twp.json"))
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--enable-transcribe", type=int, default=int(os.environ.get("ENABLE_TRANSCRIBE", "1")))
    parser.add_argument("--enable-summarize", type=int, default=int(os.environ.get("ENABLE_SUMMARIZE", "1")))
    parser.add_argument("--enable-mail", type=int, default=int(os.environ.get("ENABLE_MAIL", "1")))
    parser.add_argument("--enable-telegram", type=int, default=int(os.environ.get("ENABLE_TELEGRAM", "1")))
    parser.add_argument("--telegram-progress", action="store_true")
    parser.add_argument("--telegram-chat-id", help="Override chat ID")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--transcriber-type", default="whisperkit", choices=["whisperkit", "whispercpp"])
    parser.add_argument("--task-origin", default="default", choices=["default", "telegram"])
    
    args = parser.parse_args()
    setup_logging(level=logging.DEBUG if args.debug else logging.INFO, format_type="kv")
    
    if args.telegram_chat_id: os.environ["TELEGRAM_CHAT_ID"] = args.telegram_chat_id
    
    root = Path(args.output_root).expanduser().resolve()
    items = build_items(args, root)
    write_task_metadata_for_items(items, args)
    
    if args.debug:
        debug_email = os.environ.get("DEBUG_RECIPIENT")
        if debug_email:
            for item in items: item.emails = [debug_email]
        debug_telegram = os.environ.get("DEBUG_TELEGRAM_CHAT_ID")
        if debug_telegram: os.environ["TELEGRAM_CHAT_ID"] = debug_telegram

    if not items:
        logger.info("No tasks to process.")
        return 0

    context = PipelineContext(args, items)
    transcribe_lock = TranscriptionLock()
    transcriber = get_transcriber(args)

    logger.info(f"Starting concurrent pipeline concurrency={args.concurrency} transcriber={args.transcriber_type}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(process_item_full_lifecycle, i, args, context, transcribe_lock, transcriber) for i in range(len(items))]
        concurrent.futures.wait(futures)

    all_downloaded = all(item.download_ready for item in items)
    logger.info(f"Pipeline finished overall_ok={context.overall_ok} all_downloaded={all_downloaded}")

    return 0 if context.overall_ok else 1

if __name__ == "__main__":
    import logging
    raise SystemExit(main())
